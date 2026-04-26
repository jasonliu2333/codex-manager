"""
topic_1849054 注册流程引擎（direct_auth / chatgpt_web 入口 + consent 换取 token）
"""

import json
import logging
import secrets
import time
import urllib.parse
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from curl_cffi import requests as cffi_requests

from ..dynamic_proxy import get_proxy_url_for_task
from ..http_client import OpenAIHTTPClient
from ..openai.oauth import OAuthManager, OAuthStart
from ..openai.phone_verification import handle_openai_add_phone_challenge, is_add_phone_challenge
from ..openai.mfa_verification import handle_openai_mfa_challenge, is_mfa_challenge
from ..registration_types import RegistrationResult, SignupFormResult
from ...config.constants import (
    OPENAI_API_ENDPOINTS,
    OPENAI_PAGE_TYPES,
    OTP_CODE_PATTERN,
    DEFAULT_PASSWORD_LENGTH,
    PASSWORD_CHARSET,
    AccountStatus,
    TaskStatus,
    generate_random_user_info,
)
from ...config.settings import get_settings
from ...database import crud
from ...database.session import get_db
from ...services import BaseEmailService


logger = logging.getLogger(__name__)


class Topic1849054RegistrationEngine:
    """
    注册引擎
    负责协调邮箱服务、OAuth 流程和 OpenAI API 调用
    """

    def __init__(
        self,
        email_service: BaseEmailService,
        proxy_url: Optional[str] = None,
        callback_logger: Optional[Callable[[str], None]] = None,
        task_uuid: Optional[str] = None,
    ):
        self.email_service = email_service
        self.proxy_url = proxy_url
        self.callback_logger = callback_logger or (lambda msg: logger.info(msg))
        self.task_uuid = task_uuid

        self.http_client = OpenAIHTTPClient(proxy_url=proxy_url)

        settings = get_settings()
        self.oauth_manager = OAuthManager(
            client_id=settings.openai_client_id,
            auth_url=settings.openai_auth_url,
            token_url=settings.openai_token_url,
            redirect_uri=settings.openai_redirect_uri,
            scope=settings.openai_scope,
            proxy_url=proxy_url,
        )

        self.email: Optional[str] = None
        self.password: Optional[str] = None
        self.email_info: Optional[Dict[str, Any]] = None
        self.oauth_start: Optional[OAuthStart] = None
        self._last_oauth_callback_url: Optional[str] = None
        self.session: Optional[cffi_requests.Session] = None
        self.session_token: Optional[str] = None
        self.logs: list = []
        self._otp_sent_at: Optional[float] = None
        self._last_otp_error_code: Optional[str] = None
        self._last_otp_error_message: Optional[str] = None
        self._is_existing_account: bool = False
        self.device_id: Optional[str] = None
        self._create_account_response_data: Optional[Dict[str, Any]] = None
        self._post_signup_continue_url: Optional[str] = None
        self._last_otp_continue_url: Optional[str] = None
        self._last_otp_response_data: Optional[Dict[str, Any]] = None
        self._oauth_authorize_url: Optional[str] = None
        self._signup_authorize_url: Optional[str] = None
        self._signup_state: Optional[str] = None
        self._signup_auth_session_logging_id: Optional[str] = None
        self._register_oauth_start: Optional[OAuthStart] = None
        self._entry_mode: str = "direct_auth"
        self._entry_mode_fallback: bool = True
        self._chatgpt_base: str = "https://chatgpt.com"

    def _rebuild_clients_for_proxy(self, proxy_url: Optional[str]) -> None:
        self.proxy_url = proxy_url
        self.http_client.close()
        self.http_client = OpenAIHTTPClient(proxy_url=proxy_url)
        self.session = self.http_client.session

        settings = get_settings()
        self.oauth_manager = OAuthManager(
            client_id=settings.openai_client_id,
            auth_url=settings.openai_auth_url,
            token_url=settings.openai_token_url,
            redirect_uri=settings.openai_redirect_uri,
            scope=settings.openai_scope,
            proxy_url=proxy_url,
        )

    def _rotate_proxy_for_rate_limit(self) -> bool:
        try:
            new_proxy_url = get_proxy_url_for_task()
        except Exception as e:
            self._log(f"429 后尝试切换代理失败: {e}", "warning")
            return False

        if not new_proxy_url:
            self._log("429 后未获取到可用的新代理，将继续使用当前网络环境", "warning")
            return False

        if new_proxy_url == self.proxy_url:
            self._log(f"429 后代理未变化，继续使用: {new_proxy_url}", "warning")
            return False

        self._log(f"429 后切换代理成功: {new_proxy_url}")
        self._rebuild_clients_for_proxy(new_proxy_url)
        return True

    def _log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        self.logs.append(log_message)

        if self.callback_logger:
            self.callback_logger(log_message)

        if self.task_uuid:
            try:
                with get_db() as db:
                    crud.append_task_log(db, self.task_uuid, log_message)
            except Exception as e:
                logger.warning(f"记录任务日志失败: {e}")

        if level == "error":
            logger.error(message)
        elif level == "warning":
            logger.warning(message)
        else:
            logger.info(message)

    def _generate_password(self, length: int = DEFAULT_PASSWORD_LENGTH) -> str:
        return "".join(secrets.choice(PASSWORD_CHARSET) for _ in range(length))

    def _check_ip_location(self) -> Tuple[bool, Optional[str]]:
        try:
            return self.http_client.check_ip_location()
        except Exception as e:
            self._log(f"检查 IP 地理位置失败: {e}", "error")
            return False, None

    def _create_email(self) -> bool:
        try:
            self._log(f"正在创建 {self.email_service.service_type.value} 邮箱...")
            self.email_info = self.email_service.create_email()
            if not self.email_info or "email" not in self.email_info:
                self._log("创建邮箱失败: 返回信息不完整", "error")
                return False
            self.email = self.email_info["email"]
            self._log(f"成功创建邮箱: {self.email}")
            return True
        except Exception as e:
            self._log(f"创建邮箱失败: {e}", "error")
            return False

    def _entry_mode_candidates(self) -> List[str]:
        ordered = [self._entry_mode]
        if self._entry_mode_fallback:
            fallback = "chatgpt_web" if self._entry_mode == "direct_auth" else "direct_auth"
            ordered.append(fallback)
        unique: List[str] = []
        for mode in ordered:
            if mode not in unique:
                unique.append(mode)
        return unique

    def _init_session_via_direct_auth(self) -> Tuple[bool, str]:
        if not self.session:
            self.session = self.http_client.session

        if not self.device_id:
            self.device_id = str(uuid.uuid4())

        try:
            self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
            self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")
        except Exception:
            pass

        oauth_start = self.oauth_manager.start_oauth(
            screen_hint="signup",
            prompt="login",
            codex_cli_simplified_flow=None,
            id_token_add_organizations=None,
        )
        self._register_oauth_start = oauth_start
        self._signup_authorize_url = oauth_start.auth_url
        self._oauth_authorize_url = oauth_start.auth_url
        self._log(f"OAuth URL 已生成: {oauth_start.auth_url[:80]}...")

        try:
            resp = self.session.get(
                oauth_start.auth_url,
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "user-agent": self.http_client.default_headers.get("User-Agent", ""),
                    "referer": "https://auth.openai.com/",
                },
                allow_redirects=True,
                timeout=30,
            )
        except Exception as e:
            return False, f"oauth_authorize_failed:{e}"

        if resp.status_code not in (200, 302):
            return False, f"oauth_authorize_http_{resp.status_code}"

        has_login_session = bool(
            self._get_cookie_value(
                "login_session",
                domains=["auth.openai.com", ".auth.openai.com", ".openai.com", "openai.com", ".chatgpt.com", "chatgpt.com"],
            )
        )
        if not has_login_session:
            return False, "login_session_missing"

        if resp is not None and resp.url:
            self._oauth_authorize_url = str(resp.url)
        return True, ""

    def _init_session_via_chatgpt_web(self) -> Tuple[bool, str]:
        base_url = self._chatgpt_base.rstrip("/")
        if not self.session:
            self.session = self.http_client.session

        try:
            self.session.get(
                f"{base_url}/",
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "user-agent": self.http_client.default_headers.get("User-Agent", ""),
                    "upgrade-insecure-requests": "1",
                },
                timeout=15,
            )
        except Exception as e:
            return False, f"chatgpt_home_failed:{e}"

        csrf_headers = {
            "accept": "application/json",
            "referer": f"{base_url}/auth/login",
            "user-agent": self.http_client.default_headers.get("User-Agent", ""),
        }
        try:
            csrf_resp = self.session.get(
                f"{base_url}/api/auth/csrf",
                headers=csrf_headers,
                timeout=15,
            )
        except Exception as e:
            return False, f"chatgpt_csrf_failed:{e}"
        if csrf_resp.status_code != 200:
            return False, f"chatgpt_csrf_http_{csrf_resp.status_code}"
        try:
            csrf_data = csrf_resp.json()
        except Exception as e:
            return False, f"chatgpt_csrf_parse_failed:{e}"
        csrf_token = str((csrf_data or {}).get("csrfToken") or "").strip() if isinstance(csrf_data, dict) else ""
        if not csrf_token:
            return False, "chatgpt_csrf_missing"

        signin_headers = {
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json",
            "origin": base_url,
            "referer": f"{base_url}/auth/login",
            "user-agent": self.http_client.default_headers.get("User-Agent", ""),
        }
        signin_form = urllib.parse.urlencode(
            {
                "csrfToken": csrf_token,
                "callbackUrl": f"{base_url}/",
                "json": "true",
            }
        )
        try:
            signin_resp = self.session.post(
                f"{base_url}/api/auth/signin/openai",
                headers=signin_headers,
                data=signin_form,
                timeout=15,
                allow_redirects=False,
            )
        except Exception as e:
            return False, f"chatgpt_signin_openai_failed:{e}"

        auth_url = ""
        try:
            signin_payload = signin_resp.json()
        except Exception:
            signin_payload = {}
        if isinstance(signin_payload, dict):
            auth_url = str(signin_payload.get("url") or "").strip()
        if not auth_url and signin_resp.status_code in (301, 302, 303, 307, 308):
            auth_url = str(signin_resp.headers.get("Location") or "").strip()
        if not auth_url:
            return False, "chatgpt_signin_openai_missing_auth_url"

        try:
            follow_resp = self.session.get(
                auth_url,
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "user-agent": self.http_client.default_headers.get("User-Agent", ""),
                    "upgrade-insecure-requests": "1",
                },
                timeout=20,
            )
            if follow_resp is not None and follow_resp.url:
                self._oauth_authorize_url = str(follow_resp.url)
        except Exception as e:
            return False, f"chatgpt_auth_follow_failed:{e}"

        has_login_session = bool(
            self._get_cookie_value(
                "login_session",
                domains=["auth.openai.com", ".auth.openai.com", ".openai.com", "openai.com", ".chatgpt.com", "chatgpt.com"],
            )
        )
        if not has_login_session:
            return False, "login_session_missing"
        self._register_oauth_start = None
        return True, ""

    def _start_oauth(self) -> bool:
        try:
            self._log("开始 OAuth 授权流程 (topic_1849054)...")
            if not self.device_id:
                self.device_id = str(uuid.uuid4())
            self._signup_auth_session_logging_id = str(uuid.uuid4())

            last_reason = ""
            for mode in self._entry_mode_candidates():
                if mode == "chatgpt_web":
                    ok, reason = self._init_session_via_chatgpt_web()
                else:
                    ok, reason = self._init_session_via_direct_auth()
                if ok:
                    self._log(f"OAuth 入口: {mode}")
                    return True
                last_reason = reason or f"{mode}_failed"
                self._log(f"OAuth 入口失败: {mode} -> {last_reason}", "warning")

            self._log(f"开始 OAuth 流程失败: {last_reason}", "error")
            return False
        except Exception as e:
            self._log(f"生成 OAuth URL 失败: {e}", "error")
            return False

    def _init_session(self) -> bool:
        try:
            self.session = self.http_client.session
            return True
        except Exception as e:
            self._log(f"初始化会话失败: {e}", "error")
            return False

    def _get_cookie_value(self, name: str, domains: Optional[List[str]] = None) -> Optional[str]:
        if not self.session or not getattr(self.session, "cookies", None):
            return None

        jar = self.session.cookies
        domain_candidates = [d for d in (domains or []) if d]

        for domain in domain_candidates:
            try:
                value = jar.get(name, domain=domain)
            except Exception:
                value = None
            if value:
                return value

        cookie_items = []
        try:
            cookie_items = list(jar)
        except Exception:
            try:
                cookie_items = list(getattr(jar, "jar", []))
            except Exception:
                cookie_items = []

        for cookie in cookie_items:
            try:
                if getattr(cookie, "name", None) != name:
                    continue
                if domain_candidates and getattr(cookie, "domain", None) not in domain_candidates:
                    continue
                return getattr(cookie, "value", None)
            except Exception:
                continue

        if domain_candidates:
            for cookie in cookie_items:
                try:
                    if getattr(cookie, "name", None) == name:
                        return getattr(cookie, "value", None)
                except Exception:
                    continue

        return None

    def _get_device_id(self) -> Optional[str]:
        if self.device_id:
            self._log(f"Device ID: {self.device_id}")
            return self.device_id

        auth_url = self._signup_authorize_url
        if not auth_url and self.oauth_start:
            auth_url = self.oauth_start.auth_url
        if not auth_url:
            return None

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                if not self.session:
                    self.session = self.http_client.session

                response = self.session.get(auth_url, timeout=20)
                if response is not None and response.url:
                    self._oauth_authorize_url = str(response.url)
                did = self._get_cookie_value(
                    "oai-did",
                    domains=["auth.openai.com", ".openai.com", "openai.com", ".chatgpt.com", "chatgpt.com"],
                )
                if did:
                    self._log(f"Device ID: {did}")
                    return did
                self._log(
                    f"获取 Device ID 失败: 未返回 oai-did Cookie (HTTP {response.status_code}, 第 {attempt}/{max_attempts} 次)",
                    "warning" if attempt < max_attempts else "error",
                )
            except Exception as e:
                self._log(
                    f"获取 Device ID 失败: {e} (第 {attempt}/{max_attempts} 次)",
                    "warning" if attempt < max_attempts else "error",
                )

            if attempt < max_attempts:
                time.sleep(attempt)
                self.http_client.close()
                self.session = self.http_client.session

        return None

    def _check_sentinel(self, did: str, flow: str = "authorize_continue") -> Optional[str]:
        try:
            response = self.session.post(
                OPENAI_API_ENDPOINTS["sentinel"],
                headers={
                    "accept": "application/json",
                    "content-type": "application/json",
                    "origin": "https://auth.openai.com",
                    "referer": "https://auth.openai.com/",
                    "user-agent": self.http_client.default_headers.get("User-Agent", ""),
                },
                data=json.dumps({"flow": flow, "device_id": did}),
            )
            if response.status_code != 200:
                self._log(f"Sentinel token 获取失败: HTTP {response.status_code}", "warning")
                return None

            token_data = response.json()
            token = token_data.get("token")
            if not token:
                self._log("Sentinel token 获取失败: 响应中无 token", "warning")
                return None

            self._log("Sentinel token 获取成功")
            return token
        except Exception as e:
            self._log(f"Sentinel token 获取失败: {e}", "warning")
            return None

    @staticmethod
    def _build_sentinel_header(did: str, sen_token: str, flow: str = "authorize_continue") -> str:
        return f"{did}:{flow}:{sen_token}"

    def _get_workspace_id(self) -> Optional[str]:
        try:
            auth_cookie = self.session.cookies.get("oai-client-auth-session")
            if not auth_cookie:
                self._log("授权 Cookie 里没有 oai-client-auth-session", "warning")
                return None

            try:
                payload_part = auth_cookie.split(".")[0]
                pad = "=" * ((4 - (len(payload_part) % 4)) % 4)
                import base64
                payload = json.loads(base64.urlsafe_b64decode(payload_part + pad).decode("utf-8"))
            except Exception as e:
                self._log(f"解析授权 Cookie 失败: {e}", "error")
                return None

            if not isinstance(payload, dict):
                return None

            workspaces = payload.get("workspaces") or payload.get("organizations") or []
            if isinstance(workspaces, list):
                for item in workspaces:
                    workspace_id = str(
                        (item or {}).get("id")
                        or (item or {}).get("workspace_id")
                        or ""
                    ).strip()
                    if workspace_id:
                        return workspace_id

            auth_claims = payload.get("https://api.openai.com/auth") or payload.get("auth") or {}
            if isinstance(auth_claims, dict):
                for key in ("chatgpt_workspace_id", "workspace_id", "default_workspace_id", "organization_id"):
                    workspace_id = str(auth_claims.get(key) or "").strip()
                    if workspace_id:
                        return workspace_id

            for key in ("workspace_id", "default_workspace_id", "organization_id"):
                workspace_id = str(payload.get(key) or "").strip()
                if workspace_id:
                    return workspace_id

            return None
        except Exception as e:
            self._log(f"获取 Workspace ID 失败: {e}", "error")
            return None

    @staticmethod
    def _extract_continue_url_from_payload(payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None

        for key in ("continue_url", "next_url", "redirect_url", "callback_url"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value

        page = payload.get("page") or {}
        if isinstance(page, dict):
            for key in ("continue_url", "next_url", "redirect_url", "callback_url"):
                value = str(page.get(key) or "").strip()
                if value:
                    return value

        return None

    def _oauth_json_headers(self, referer: str) -> Dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://auth.openai.com",
            "referer": referer,
            "user-agent": self.http_client.default_headers.get("User-Agent", ""),
            "oai-device-id": self.device_id or "",
        }

    def _follow_redirects_for_code(
        self,
        start_url: str,
        referer: Optional[str] = None,
        max_redirects: int = 16,
    ) -> Optional[str]:
        current_url = start_url
        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "upgrade-insecure-requests": "1",
            "user-agent": self.http_client.default_headers.get("User-Agent", ""),
        }
        if referer:
            headers["referer"] = referer

        for i in range(max_redirects):
            response = self.session.get(current_url, headers=headers, allow_redirects=False, timeout=30)
            final_url = str(response.url)
            self._log(f"OAuth 重定向 {i + 1}/{max_redirects}: {response.status_code} {final_url[:120]}...")

            if self._extract_code_from_url(final_url):
                return final_url

            if response.status_code not in [301, 302, 303, 307, 308]:
                return None

            location = response.headers.get("Location") or ""
            if not location:
                return None

            next_url = urllib.parse.urljoin(current_url, location)
            if self._extract_code_from_url(next_url):
                return next_url

            current_url = next_url
            headers["referer"] = final_url

        return None

    def _select_organization(self, referer: str, orgs: list) -> Optional[str]:
        if not orgs:
            return None

        first_org = (orgs[0] or {})
        org_id = str(first_org.get("id") or "").strip()
        if not org_id:
            return None

        body: Dict[str, str] = {"org_id": org_id}
        projects = first_org.get("projects") or []
        if projects:
            project_id = str(((projects[0] or {}).get("id")) or "").strip()
            if project_id:
                body["project_id"] = project_id

        response = self.session.post(
            "https://auth.openai.com/api/accounts/organization/select",
            headers=self._oauth_json_headers(referer),
            data=json.dumps(body),
            allow_redirects=False,
            timeout=30,
        )

        self._log(f"选择 organization 状态: {response.status_code}")
        if response.status_code != 200:
            self._log(f"organization/select 失败: {response.text[:200]}", "warning")
            return None

        try:
            data = response.json()
        except Exception as e:
            self._log(f"解析 organization/select 响应失败: {e}", "warning")
            return None

        next_url = self._extract_continue_url_from_payload(data)
        if next_url:
            self._log(f"Organization Continue URL: {next_url[:100]}...")
        return next_url

    def _submit_signup_form(self, did: str, sen_token: Optional[str]) -> SignupFormResult:
        try:
            referer = self._signup_authorize_url or "https://auth.openai.com/"
            headers = self._oauth_json_headers(referer)
            if did:
                headers["oai-device-id"] = did
            if sen_token:
                headers["openai-sentinel-token"] = self._build_sentinel_header(did, sen_token)

            response = self.session.post(
                OPENAI_API_ENDPOINTS["signup"],
                headers=headers,
                data=json.dumps({"username": {"kind": "email", "value": self.email}, "screen_hint": "signup"}),
                allow_redirects=False,
                timeout=30,
            )

            self._log(f"提交注册表单状态: {response.status_code}")
            if response.status_code != 200:
                return SignupFormResult(
                    success=False,
                    error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                )

            return SignupFormResult(success=True, page_type="password", is_existing_account=False)
        except Exception as e:
            self._log(f"提交注册表单失败: {e}", "error")
            return SignupFormResult(success=False, error_message=str(e))

    def _register_password(self) -> Tuple[bool, Optional[str]]:
        try:
            password = self._generate_password()
            self.password = password
            self._log(f"生成密码: {password}")

            register_body = json.dumps({
                "password": password,
                "username": self.email,
            })

            headers = {
                "referer": "https://auth.openai.com/create-account/password",
                "accept": "application/json",
                "content-type": "application/json",
                "origin": "https://auth.openai.com",
            }
            if self.device_id:
                headers["oai-device-id"] = self.device_id
                sentinel = self._check_sentinel(self.device_id, flow="username_password_create")
                if sentinel:
                    headers["openai-sentinel-token"] = self._build_sentinel_header(
                        self.device_id,
                        sentinel,
                        flow="username_password_create",
                    )

            response = self.session.post(
                OPENAI_API_ENDPOINTS["register"],
                headers=headers,
                data=register_body,
            )

            self._log(f"提交密码状态: {response.status_code}")
            if response.status_code != 200:
                error_text = response.text[:500]
                self._log(f"密码注册失败: {error_text}", "warning")
                try:
                    error_json = response.json()
                    error_msg = error_json.get("error", {}).get("message", "")
                    error_code = error_json.get("error", {}).get("code", "")
                    if "already" in error_msg.lower() or "exists" in error_msg.lower() or error_code == "user_exists":
                        self._log(f"邮箱 {self.email} 可能已在 OpenAI 注册过", "error")
                        self._mark_email_as_registered()
                except Exception:
                    pass
                return False, None

            return True, password
        except Exception as e:
            self._log(f"密码注册失败: {e}", "error")
            return False, None

    def _mark_email_as_registered(self):
        try:
            with get_db() as db:
                existing = crud.get_account_by_email(db, self.email)
                if not existing:
                    crud.create_account(
                        db,
                        email=self.email,
                        password="",
                        email_service=self.email_service.service_type.value,
                        email_service_id=self.email_info.get("service_id") if self.email_info else None,
                        status="failed",
                        extra_data={"register_failed_reason": "email_already_registered_on_openai"},
                    )
                    self._log(f"已在数据库中标记邮箱 {self.email} 为已注册状态")
        except Exception as e:
            logger.warning(f"标记邮箱状态失败: {e}")

    def _send_verification_code(self) -> bool:
        try:
            self._otp_sent_at = time.time()
            response = self.session.get(
                OPENAI_API_ENDPOINTS["send_otp"],
                headers={
                    "referer": "https://auth.openai.com/create-account/password",
                    "accept": "application/json",
                },
            )
            self._log(f"验证码发送状态: {response.status_code}")
            if response.status_code != 200:
                return False

            try:
                self.session.get(
                    "https://auth.openai.com/email-verification",
                    headers={
                        "referer": "https://auth.openai.com/create-account/password",
                        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    },
                    allow_redirects=True,
                    timeout=30,
                )
            except Exception:
                pass
            return True
        except Exception as e:
            self._log(f"发送验证码失败: {e}", "error")
            return False

    def _get_verification_code(self) -> Optional[str]:
        try:
            self._log(f"正在等待邮箱 {self.email} 的验证码...")
            total_timeout = 120
            chunk_timeout = 15
            email_id = self.email_info.get("service_id") if self.email_info else None
            started_at = time.time()
            attempt = 0
            last_logged_remaining = None

            while True:
                elapsed = int(time.time() - started_at)
                remaining = max(0, total_timeout - elapsed)
                if remaining <= 0:
                    self._log("等待验证码超时", "error")
                    return None

                attempt += 1
                current_timeout = min(chunk_timeout, remaining)
                should_log_progress = (
                    attempt == 1
                    or remaining != last_logged_remaining
                    and (remaining <= 10 or remaining % 5 == 0)
                )
                if should_log_progress:
                    self._log(
                        f"验证码轮询第 {attempt} 次，最多等待 {current_timeout} 秒"
                        f"（已等待 {elapsed} 秒，剩余 {remaining} 秒）"
                    )
                    last_logged_remaining = remaining

                attempt_started_at = time.time()

                code = self.email_service.get_verification_code(
                    email=self.email,
                    email_id=email_id,
                    timeout=current_timeout,
                    pattern=OTP_CODE_PATTERN,
                    otp_sent_at=self._otp_sent_at,
                )

                if code:
                    self._log(f"成功获取验证码: {code}")
                    return code

                last_error = (getattr(self.email_service, "last_error", None) or "").strip()
                if last_error:
                    error_lower = last_error.lower()
                    auth_keywords = [
                        "auth", "oauth", "token", "login", "authenticate",
                        "unauthorized", "invalid_grant", "xoauth2",
                        "认证", "凭据", "登录", "授权", "令牌",
                    ]
                    mapping_keywords = [
                        "未找到邮箱对应的账户",
                        "没有可用的",
                        "未找到匹配的邮箱配置",
                        "缺少",
                        "依赖",
                    ]

                    if any(keyword in error_lower for keyword in auth_keywords):
                        self._log(
                            f"等待验证码失败：邮箱登录凭据或 OAuth Token 可能已失效（{last_error}）",
                            "error",
                        )
                        return None

                    if any(keyword in last_error for keyword in mapping_keywords):
                        self._log(f"等待验证码失败：邮箱配置不匹配（{last_error}）", "error")
                        return None

                attempt_elapsed = time.time() - attempt_started_at
                if attempt_elapsed < 1 and remaining > 1:
                    time.sleep(min(1 - attempt_elapsed, remaining - 1))

        except Exception as e:
            self._log(f"获取验证码失败: {e}", "error")
            return None

    def _validate_verification_code_once(self, code: str, use_sentinel: bool = False) -> bool:
        try:
            headers = {
                "referer": "https://auth.openai.com/email-verification",
                "accept": "application/json",
                "content-type": "application/json",
            }
            if use_sentinel and self.device_id:
                sentinel = self._check_sentinel(self.device_id, flow="authorize_continue")
                if sentinel:
                    headers["openai-sentinel-token"] = self._build_sentinel_header(self.device_id, sentinel)

            response = self.session.post(
                OPENAI_API_ENDPOINTS["validate_otp"],
                headers=headers,
                data=json.dumps({"code": code}),
            )

            try:
                payload = response.json()
            except Exception:
                payload = None
            self._last_otp_response_data = payload if isinstance(payload, dict) else None
            self._last_otp_continue_url = self._extract_continue_url_from_payload(payload) if isinstance(payload, dict) else None
            if isinstance(payload, dict):
                error_payload = payload.get("error")
                if isinstance(error_payload, dict):
                    self._last_otp_error_code = str(error_payload.get("code") or "").strip() or None
                    self._last_otp_error_message = str(error_payload.get("message") or "").strip() or None

            self._log(f"验证码校验状态: {response.status_code}")
            try:
                self._log(f"验证码校验响应体: {(response.text or '')[:200]}", "warning")
            except Exception:
                self._log("验证码校验响应体读取失败", "warning")
            try:
                self._log(f"验证码校验响应头: {dict(response.headers) if response.headers else {}}", "warning")
            except Exception:
                self._log("验证码校验响应头读取失败", "warning")
            return response.status_code == 200
        except Exception as e:
            self._log(f"验证验证码失败: {e}", "error")
            return False

    def _validate_verification_code(self, code: str) -> bool:
        self._last_otp_error_code = None
        self._last_otp_error_message = None

        if self._validate_verification_code_once(code, use_sentinel=False):
            return True

        self._log("验证码校验失败，尝试 Sentinel fallback", "warning")
        return self._validate_verification_code_once(code, use_sentinel=True)

    def _validate_verification_code_with_retry(self, code: str, max_retries: int = 2) -> bool:
        if self._validate_verification_code(code):
            return True

        if self._last_otp_error_code != "wrong_email_otp_code":
            return False

        for attempt in range(1, max_retries + 1):
            self._log(f"验证码错误，尝试重新获取（第 {attempt}/{max_retries} 次）", "warning")
            next_code = self._get_verification_code()
            if not next_code:
                return False
            if self._validate_verification_code(next_code):
                return True
            if self._last_otp_error_code != "wrong_email_otp_code":
                return False

        return False

    def _create_user_account(self) -> bool:
        try:
            self._create_account_response_data = None
            self._post_signup_continue_url = None
            user_info = generate_random_user_info()
            self._log(f"生成用户信息: {user_info['name']}, 生日: {user_info['birthdate']}")
            create_account_body = json.dumps(user_info)
            headers = {
                "referer": "https://auth.openai.com/about-you",
                "accept": "application/json",
                "content-type": "application/json",
            }

            if self.device_id:
                sentinel_token = self._check_sentinel(self.device_id, flow="oauth_create_account")
                if sentinel_token:
                    headers["openai-sentinel-token"] = self._build_sentinel_header(
                        self.device_id,
                        sentinel_token,
                        flow="oauth_create_account",
                    )
                    self._log("创建账户前已刷新 Sentinel token")
                else:
                    self._log("创建账户前获取 Sentinel token 失败，继续尝试提交", "warning")

            response = self.session.post(
                OPENAI_API_ENDPOINTS["create_account"],
                headers=headers,
                data=create_account_body,
            )

            self._log(f"账户创建状态: {response.status_code}")
            if response.status_code != 200:
                self._log(f"创建账户失败: {response.text[:200]}", "warning")
                return False

            try:
                data = response.json()
                self._create_account_response_data = data
                top_level_keys = list(data.keys()) if isinstance(data, dict) else []
                if top_level_keys:
                    self._log(f"创建账户响应键: {top_level_keys}")
                continue_url = self._extract_continue_url_from_payload(data)
                if continue_url:
                    self._post_signup_continue_url = continue_url
                    self._log(f"创建账户响应包含 Continue URL: {continue_url[:100]}...")
            except Exception as parse_error:
                self._log(f"创建账户响应非 JSON 或解析失败: {parse_error}", "warning")

            return True
        except Exception as e:
            self._log(f"创建账户失败: {e}", "error")
            return False

    def _get_continue_url_after_signup(self, workspace_id: Optional[str]) -> Optional[str]:
        if self._post_signup_continue_url:
            return self._post_signup_continue_url

        if workspace_id:
            return self._select_workspace(workspace_id)

        self._log("既没有 continue_url，也没有 workspace_id，无法继续 OAuth 流程", "error")
        return None

    @staticmethod
    def _extract_code_from_url(url: str) -> Optional[str]:
        if not url or "code=" not in url:
            return None
        try:
            return urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("code", [None])[0]
        except Exception:
            return None

    def _exchange_code_for_token(self, code: str, code_verifier: str) -> Optional[Dict[str, Any]]:
        settings = get_settings()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.openai_redirect_uri,
            "client_id": settings.openai_client_id,
            "code_verifier": code_verifier,
        }
        proxies = None
        if self.proxy_url:
            proxies = {"http": self.proxy_url, "https": self.proxy_url}
        try:
            resp = cffi_requests.post(
                settings.openai_token_url,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                proxies=proxies,
                timeout=60,
                impersonate="chrome",
            )
            if resp.status_code != 200:
                self._log(f"token exchange failed: {resp.status_code} {resp.text[:200]}", "warning")
                return None
            result = resp.json()
            if not isinstance(result, dict):
                return None
            return result
        except Exception as e:
            self._log(f"token exchange failed: {e}", "warning")
            return None

    def _exchange_tokens_via_consent(self) -> Optional[Dict[str, Any]]:
        if not self._register_oauth_start:
            self._log("注册入口未生成 code_verifier，跳过 consent 换取", "warning")
            return None

        consent_url = self._get_continue_url_after_signup(self._get_workspace_id()) or "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
        if consent_url.startswith("/"):
            consent_url = urllib.parse.urljoin("https://auth.openai.com", consent_url)
        self._log(f"OAuth Consent URL: {consent_url[:100]}...")

        workspace_id = self._get_workspace_id()
        workspace_continue = None
        if workspace_id:
            self._log(f"选择 Workspace: {workspace_id}")
            response = self.session.post(
                OPENAI_API_ENDPOINTS["select_workspace"],
                headers={
                    "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                    "content-type": "application/json",
                },
                data=json.dumps({"workspace_id": workspace_id}),
                allow_redirects=False,
                timeout=30,
            )
            self._log(f"选择 workspace 状态: {response.status_code}")
            if response.status_code == 200:
                workspace_data = response.json()
                workspace_continue = self._extract_continue_url_from_payload(workspace_data)
                orgs = (((workspace_data.get("data") or {}).get("orgs")) or [])
                org_continue = self._select_organization(
                    workspace_continue or "https://auth.openai.com/sign-in-with-chatgpt/codex/organization",
                    orgs
                )
                consent_url = org_continue or workspace_continue or consent_url
            else:
                self._log(f"workspace/select 失败: {response.text[:200]}", "warning")

        callback_url = self._follow_redirects_for_code(consent_url, referer="https://auth.openai.com/log-in/password")
        if not callback_url:
            self._log("consent 未能拿到 callback URL", "warning")
            return None

        try:
            token_info = self.oauth_manager.handle_callback(
                callback_url=callback_url,
                expected_state=self._register_oauth_start.state,
                code_verifier=self._register_oauth_start.code_verifier,
            )
            self._log("consent 换取 token 成功")
            return token_info
        except Exception as e:
            self._log(f"consent 换取 token 失败，尝试直接兑换: {e}", "warning")

        code = self._extract_code_from_url(callback_url)
        if not code:
            return None
        result = self._exchange_code_for_token(code, self._register_oauth_start.code_verifier)
        if not result:
            return None
        return result

    def _perform_post_registration_oauth(self) -> Optional[Dict[str, Any]]:
        if not self.email or not self.password:
            self._log("缺少邮箱或密码，跳过注册后 OAuth", "warning")
            return None

        self._log("13. 开始注册后 OAuth 登录...")
        self.oauth_start = self.oauth_manager.start_oauth()

        try:
            callback_url = self._follow_redirects_for_code(self.oauth_start.auth_url)
            if callback_url:
                return self._handle_oauth_callback(callback_url)
        except Exception:
            pass

        response = self.session.get(
            self.oauth_start.auth_url,
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "upgrade-insecure-requests": "1",
                "user-agent": self.http_client.default_headers.get("User-Agent", ""),
            },
            allow_redirects=True,
            timeout=30,
        )
        referer = str(response.url) if str(response.url).startswith("https://auth.openai.com") else "https://auth.openai.com/log-in"
        self._log(f"OAuth authorize 最终页面: {str(response.url)[:100]}...")

        self.device_id = (
            self._get_cookie_value(
                "oai-did",
                domains=["auth.openai.com", ".openai.com", "openai.com", ".chatgpt.com", "chatgpt.com"],
            )
            or self.device_id
        )
        if not self.device_id:
            self.device_id = self._get_device_id()
        if self.device_id:
            self._log(f"注册后 OAuth Device ID: {self.device_id}")
        else:
            self._log("注册后 OAuth 未获取到 Device ID，将继续尝试登录", "warning")

        signup_headers = self._oauth_json_headers(referer)
        if self.device_id:
            sentinel = self._check_sentinel(self.device_id, flow="authorize_continue")
            if sentinel:
                signup_headers["openai-sentinel-token"] = self._build_sentinel_header(self.device_id, sentinel)

        auth_continue_resp = self.session.post(
            OPENAI_API_ENDPOINTS["signup"],
            headers=signup_headers,
            data=json.dumps({"username": {"value": self.email, "kind": "email"}, "screen_hint": "login"}),
            allow_redirects=False,
            timeout=30,
        )
        self._log(f"注册后 authorize/continue 状态: {auth_continue_resp.status_code}")
        if auth_continue_resp.status_code != 200:
            self._log(f"注册后 authorize/continue 失败: {auth_continue_resp.text[:200]}", "warning")
            return None

        continue_data = auth_continue_resp.json()
        continue_url = self._extract_continue_url_from_payload(continue_data) or "https://auth.openai.com/log-in/password"

        verify_headers = self._oauth_json_headers("https://auth.openai.com/log-in/password")
        if self.device_id:
            sentinel = self._check_sentinel(self.device_id, flow="password_verify")
            if sentinel:
                verify_headers["openai-sentinel-token"] = self._build_sentinel_header(
                    self.device_id,
                    sentinel,
                    flow="password_verify",
                )

        verify_resp = self.session.post(
            "https://auth.openai.com/api/accounts/password/verify",
            headers=verify_headers,
            data=json.dumps({"password": self.password}),
            allow_redirects=False,
            timeout=30,
        )
        self._log(f"注册后 password/verify 状态: {verify_resp.status_code}")
        if verify_resp.status_code != 200:
            if verify_resp.status_code == 401 and "invalid_username_or_password" in (verify_resp.text or ""):
                self._log("注册后 password/verify 返回 401：当前账号保存的 ChatGPT 密码可能不正确", "error")
            self._log(f"注册后 password/verify 失败: {verify_resp.text[:200]}", "warning")
            return None

        verify_data = verify_resp.json()
        page_type = str((verify_data.get("page") or {}).get("type") or "")
        continue_url = self._extract_continue_url_from_payload(verify_data) or continue_url

        if page_type == OPENAI_PAGE_TYPES["EMAIL_OTP_VERIFICATION"] or "email-verification" in continue_url:
            self._log("14. 注册后 OAuth 需要邮箱 OTP 验证...")
            self._otp_sent_at = time.time()
            code = self._get_verification_code()
            if not code:
                return None
            if not self._validate_verification_code_with_retry(code):
                return None
            otp_continue_url = self._last_otp_continue_url or self._extract_continue_url_from_payload(self._last_otp_response_data)
            otp_page_type = str(((self._last_otp_response_data or {}).get("page") or {}).get("type") or "") if isinstance(self._last_otp_response_data, dict) else ""
            if is_add_phone_challenge(otp_page_type, otp_continue_url or "", self._last_otp_response_data):
                self._log("14.1 注册后 OAuth 需要 add-phone 手机验证...")
                phone_continue_url = handle_openai_add_phone_challenge(self, otp_continue_url or continue_url)
                if not phone_continue_url:
                    return None
                continue_url = phone_continue_url
            else:
                continue_url = otp_continue_url or "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"

        if is_add_phone_challenge(page_type, continue_url, verify_data):
            self._log("14. 注册后 OAuth 需要 add-phone 手机验证...")
            phone_continue_url = handle_openai_add_phone_challenge(self, continue_url)
            if not phone_continue_url:
                return None
            continue_url = phone_continue_url

        if is_mfa_challenge(page_type, continue_url, verify_data):
            mfa_continue_url = handle_openai_mfa_challenge(self, continue_url)
            if not mfa_continue_url:
                return None
            continue_url = mfa_continue_url
            if is_add_phone_challenge("", continue_url, None):
                self._log("14.3 MFA 完成后仍需 add-phone 手机验证...")
                phone_continue_url = handle_openai_add_phone_challenge(self, continue_url)
                if not phone_continue_url:
                    return None
                continue_url = phone_continue_url

        consent_url = continue_url
        if consent_url.startswith("/"):
            consent_url = urllib.parse.urljoin("https://auth.openai.com", consent_url)
        self._log(f"15. OAuth Consent URL: {consent_url[:100]}...")

        workspace_id = self._get_workspace_id()
        workspace_continue = None
        if workspace_id:
            self._log(f"16. 选择 Workspace: {workspace_id}")
            response = self.session.post(
                OPENAI_API_ENDPOINTS["select_workspace"],
                headers={
                    "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                    "content-type": "application/json",
                },
                data=json.dumps({"workspace_id": workspace_id}),
                allow_redirects=False,
                timeout=30,
            )
            self._log(f"选择 workspace 状态: {response.status_code}")
            if response.status_code == 200:
                workspace_data = response.json()
                workspace_continue = self._extract_continue_url_from_payload(workspace_data)
                orgs = (((workspace_data.get("data") or {}).get("orgs")) or [])
                org_continue = self._select_organization(
                    workspace_continue or "https://auth.openai.com/sign-in-with-chatgpt/codex/organization",
                    orgs
                )
                consent_url = org_continue or workspace_continue or consent_url
            else:
                self._log(f"workspace/select 失败: {response.text[:200]}", "warning")

        callback_url = self._follow_redirects_for_code(consent_url, referer="https://auth.openai.com/log-in/password")
        if not callback_url:
            self._log("注册后 OAuth 未能拿到 callback URL", "warning")
            return None

        return self._handle_oauth_callback(callback_url)

    def recover_oauth_tokens(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        self.logs = []
        self.email = email
        self.password = password
        self.session_token = None
        self.oauth_start = None
        self._last_oauth_callback_url = None
        self.device_id = None
        self._create_account_response_data = None
        self._post_signup_continue_url = None
        self._last_otp_continue_url = None
        self._last_otp_response_data = None
        self._last_mfa_required = False
        self._last_mfa_challenge_url = None
        self._last_mfa_error_message = None

        self._log("=" * 60)
        self._log("开始 OAuth 补录流程")
        self._log("=" * 60)
        self._log("1. 初始化全新登录会话...")

        self.http_client.close()
        self.http_client = OpenAIHTTPClient(proxy_url=self.proxy_url)
        self.oauth_manager = OAuthManager(
            client_id=get_settings().openai_client_id,
            auth_url=get_settings().openai_auth_url,
            token_url=get_settings().openai_token_url,
            redirect_uri=get_settings().openai_redirect_uri,
            scope=get_settings().openai_scope,
            proxy_url=self.proxy_url,
        )

        if not self._init_session():
            self._log("初始化全新登录会话失败", "error")
            return None

        self._log("2. 使用新会话执行 OAuth 登录...")
        return self._perform_post_registration_oauth()

    def _select_workspace(self, workspace_id: str) -> Optional[str]:
        try:
            select_body = f'{{"workspace_id":"{workspace_id}"}}'
            response = self.session.post(
                OPENAI_API_ENDPOINTS["select_workspace"],
                headers={
                    "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                    "content-type": "application/json",
                },
                data=select_body,
            )
            if response.status_code != 200:
                self._log(f"选择 workspace 失败: {response.status_code}", "error")
                self._log(f"响应: {response.text[:200]}", "warning")
                return None

            continue_url = str((response.json() or {}).get("continue_url") or "").strip()
            if not continue_url:
                self._log("workspace/select 响应里缺少 continue_url", "error")
                return None

            self._log(f"Continue URL: {continue_url[:100]}...")
            return continue_url
        except Exception as e:
            self._log(f"选择 Workspace 失败: {e}", "error")
            return None

    def _follow_redirects(self, start_url: str) -> Optional[str]:
        try:
            current_url = start_url
            max_redirects = 6
            for i in range(max_redirects):
                self._log(f"重定向 {i+1}/{max_redirects}: {current_url[:100]}...")
                response = self.session.get(current_url, allow_redirects=False, timeout=15)
                location = response.headers.get("Location") or ""

                if response.status_code not in [301, 302, 303, 307, 308]:
                    self._log(f"非重定向状态码: {response.status_code}")
                    break

                if not location:
                    self._log("重定向响应缺少 Location 头")
                    break

                next_url = urllib.parse.urljoin(current_url, location)
                if "code=" in next_url and "state=" in next_url:
                    self._log(f"找到回调 URL: {next_url[:100]}...")
                    return next_url

                current_url = next_url

            self._log("未能在重定向链中找到回调 URL", "error")
            return None
        except Exception as e:
            self._log(f"跟随重定向失败: {e}", "error")
            return None

    def _handle_oauth_callback(self, callback_url: str) -> Optional[Dict[str, Any]]:
        try:
            if not self.oauth_start:
                self._log("OAuth 流程未初始化", "error")
                return None

            self._last_oauth_callback_url = callback_url
            self._log("处理 OAuth 回调...")
            token_info = self.oauth_manager.handle_callback(
                callback_url=callback_url,
                expected_state=self.oauth_start.state,
                code_verifier=self.oauth_start.code_verifier,
            )
            self._log("OAuth 授权成功")
            return token_info
        except Exception as e:
            self._log(f"处理 OAuth 回调失败: {e}", "error")
            return None

    def retry_last_oauth_callback_token_exchange(self, proxy_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not self.oauth_start or not self._last_oauth_callback_url:
            self._log("缺少 callback_url 或 oauth_start，无法仅重试 token exchange", "warning")
            return None
        if proxy_url:
            self.proxy_url = proxy_url
            self.oauth_manager.proxy_url = proxy_url
        self._log("仅重试 OAuth 最终 token exchange...")
        return self._handle_oauth_callback(self._last_oauth_callback_url)

    def run(self) -> RegistrationResult:
        result = RegistrationResult(success=False, logs=self.logs)

        try:
            self._log("=" * 60)
            self._log("开始注册流程")
            self._log("=" * 60)

            self._log("1. 检查 IP 地理位置...")
            ip_ok, location = self._check_ip_location()
            if not ip_ok:
                result.error_message = f"IP 地理位置不支持: {location}"
                self._log(f"IP 检查失败: {location}", "error")
                return result

            self._log(f"IP 位置: {location}")

            self._log("2. 创建邮箱...")
            if not self._create_email():
                result.error_message = "创建邮箱失败"
                return result

            result.email = self.email

            self._log("3. 初始化会话...")
            if not self._init_session():
                result.error_message = "初始化会话失败"
                return result

            self._log("4. 开始 OAuth 授权流程...")
            if not self._start_oauth():
                result.error_message = "开始 OAuth 流程失败"
                return result

            self._log("5. 获取 Device ID...")
            did = self._get_device_id()
            if not did:
                result.error_message = "获取 Device ID 失败"
                return result
            self.device_id = did

            self._log("6. 初始化注册会话...")
            sen_token = None
            if self.device_id:
                sen_token = self._check_sentinel(self.device_id, flow="authorize_continue")

            self._log("7. 提交注册表单...")
            signup_result = self._submit_signup_form(did, sen_token)
            if not signup_result.success:
                result.error_message = f"提交注册表单失败: {signup_result.error_message}"
                return result

            if self._is_existing_account:
                self._log("8. [已注册账号] 跳过密码设置，OTP 已自动发送")
            else:
                self._log("8. 注册密码...")
                password_ok, _ = self._register_password()
                if not password_ok:
                    result.error_message = "注册密码失败"
                    return result

            if self._is_existing_account:
                self._log("9. [已注册账号] 跳过发送验证码，使用自动发送的 OTP")
                self._otp_sent_at = time.time()
            else:
                self._log("9. 发送验证码...")
                if not self._send_verification_code():
                    result.error_message = "发送验证码失败"
                    return result

            self._log("10. 等待验证码...")
            code = self._get_verification_code()
            if not code:
                result.error_message = "获取验证码失败"
                return result

            self._log("11. 验证验证码...")
            if not self._validate_verification_code_with_retry(code):
                result.error_message = "验证验证码失败"
                return result

            if self._is_existing_account:
                self._log("12. [已注册账号] 跳过创建用户账户")
            else:
                self._log("12. 创建用户账户...")
                if not self._create_user_account():
                    result.error_message = "创建用户账户失败"
                    return result

            token_info = self._exchange_tokens_via_consent()
            if not token_info:
                token_info = self._perform_post_registration_oauth()

            if token_info:
                result.account_id = token_info.get("account_id", "")
                result.access_token = token_info.get("access_token", "")
                result.refresh_token = token_info.get("refresh_token", "")
                result.id_token = token_info.get("id_token", "")
            else:
                self._log("注册已完成，但注册后 OAuth 未完成；账号将以无 token 状态保存", "warning")
            result.password = self.password or ""

            result.source = "login" if self._is_existing_account else "register"

            session_cookie = self.session.cookies.get("__Secure-next-auth.session-token")
            if session_cookie:
                self.session_token = session_cookie
                result.session_token = session_cookie
                self._log("获取到 Session Token")

            workspace_id = self._get_workspace_id()
            if workspace_id:
                result.workspace_id = workspace_id

            self._log("=" * 60)
            self._log("注册成功!" if not self._is_existing_account else "登录成功! (已注册账号)")
            self._log(f"邮箱: {result.email}")
            self._log(f"Account ID: {result.account_id}")
            self._log(f"Workspace ID: {result.workspace_id}")
            self._log("=" * 60)

            result.success = True
            result.metadata = {
                "email_service": self.email_service.service_type.value,
                "proxy_used": self.proxy_url,
                "registered_at": datetime.now().isoformat(),
                "is_existing_account": self._is_existing_account,
                "oauth_completed": bool(result.access_token),
                "entry_mode": self._entry_mode,
            }
            return result

        except Exception as e:
            self._log(f"注册过程中发生未预期错误: {e}", "error")
            result.error_message = str(e)
            return result

    def save_to_database(self, result: RegistrationResult) -> bool:
        if not result.success:
            return False

        try:
            settings = get_settings()
            with get_db() as db:
                account = crud.create_account(
                    db,
                    email=result.email,
                    password=result.password,
                    email_service=self.email_service.service_type.value,
                    email_service_id=self.email_info.get("service_id") if self.email_info else None,
                    account_id=result.account_id,
                    workspace_id=result.workspace_id,
                    status=AccountStatus.ACTIVE.value if result.access_token else AccountStatus.FAILED.value,
                    access_token=result.access_token,
                    refresh_token=result.refresh_token,
                    id_token=result.id_token,
                    client_id=settings.openai_client_id,
                    token_type="Bearer",
                    expires_in=3600,
                    token_expires_at=datetime.now().timestamp() + 3600,
                    metadata=result.metadata or {},
                    source=result.source,
                )
                if account:
                    self._log(f"账户已保存到数据库，ID: {account.id}")
                    return True
                return False
        except Exception as e:
            self._log(f"保存账户到数据库失败: {e}", "error")
            return False
