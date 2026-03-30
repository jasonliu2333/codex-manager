"""
注册流程引擎
从 main.py 中提取并重构的注册流程
"""

import re
import json
import time
import logging
import secrets
import string
import urllib.parse
from typing import Optional, Dict, Any, Tuple, Callable
from dataclasses import dataclass
from datetime import datetime

from curl_cffi import requests as cffi_requests

from .openai.oauth import OAuthManager, OAuthStart
from .http_client import OpenAIHTTPClient, HTTPClientError
from .dynamic_proxy import get_proxy_url_for_task
from ..services import EmailServiceFactory, BaseEmailService, EmailServiceType
from ..database import crud
from ..database.session import get_db
from ..config.constants import (
    OPENAI_API_ENDPOINTS,
    OPENAI_PAGE_TYPES,
    generate_random_user_info,
    OTP_CODE_PATTERN,
    DEFAULT_PASSWORD_LENGTH,
    PASSWORD_CHARSET,
    AccountStatus,
    TaskStatus,
)
from ..config.settings import get_settings


logger = logging.getLogger(__name__)


@dataclass
class RegistrationResult:
    """注册结果"""
    success: bool
    email: str = ""
    password: str = ""  # 注册密码
    account_id: str = ""
    workspace_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    session_token: str = ""  # 会话令牌
    error_message: str = ""
    logs: list = None
    metadata: dict = None
    source: str = "register"  # 'register' 或 'login'，区分账号来源

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "success": self.success,
            "email": self.email,
            "password": self.password,
            "account_id": self.account_id,
            "workspace_id": self.workspace_id,
            "access_token": self.access_token[:20] + "..." if self.access_token else "",
            "refresh_token": self.refresh_token[:20] + "..." if self.refresh_token else "",
            "id_token": self.id_token[:20] + "..." if self.id_token else "",
            "session_token": self.session_token[:20] + "..." if self.session_token else "",
            "error_message": self.error_message,
            "logs": self.logs or [],
            "metadata": self.metadata or {},
            "source": self.source,
        }


@dataclass
class SignupFormResult:
    """提交注册表单的结果"""
    success: bool
    page_type: str = ""  # 响应中的 page.type 字段
    is_existing_account: bool = False  # 是否为已注册账号
    response_data: Dict[str, Any] = None  # 完整的响应数据
    error_message: str = ""


class RegistrationEngine:
    """
    注册引擎
    负责协调邮箱服务、OAuth 流程和 OpenAI API 调用
    """

    def __init__(
        self,
        email_service: BaseEmailService,
        proxy_url: Optional[str] = None,
        callback_logger: Optional[Callable[[str], None]] = None,
        task_uuid: Optional[str] = None
    ):
        """
        初始化注册引擎

        Args:
            email_service: 邮箱服务实例
            proxy_url: 代理 URL
            callback_logger: 日志回调函数
            task_uuid: 任务 UUID（用于数据库记录）
        """
        self.email_service = email_service
        self.proxy_url = proxy_url
        self.callback_logger = callback_logger or (lambda msg: logger.info(msg))
        self.task_uuid = task_uuid

        # 创建 HTTP 客户端
        self.http_client = OpenAIHTTPClient(proxy_url=proxy_url)

        # 创建 OAuth 管理器
        settings = get_settings()
        self.oauth_manager = OAuthManager(
            client_id=settings.openai_client_id,
            auth_url=settings.openai_auth_url,
            token_url=settings.openai_token_url,
            redirect_uri=settings.openai_redirect_uri,
            scope=settings.openai_scope,
            proxy_url=proxy_url  # 传递代理配置
        )

        # 状态变量
        self.email: Optional[str] = None
        self.password: Optional[str] = None  # 注册密码
        self.email_info: Optional[Dict[str, Any]] = None
        self.oauth_start: Optional[OAuthStart] = None
        self.session: Optional[cffi_requests.Session] = None
        self.session_token: Optional[str] = None  # 会话令牌
        self.logs: list = []
        self._otp_sent_at: Optional[float] = None  # OTP 发送时间戳
        self._is_existing_account: bool = False  # 是否为已注册账号（用于自动登录）
        self.device_id: Optional[str] = None
        self._create_account_response_data: Optional[Dict[str, Any]] = None
        self._post_signup_continue_url: Optional[str] = None

    def _rebuild_clients_for_proxy(self, proxy_url: Optional[str]) -> None:
        """切换代理后重建 HTTP 客户端、Session 和 OAuth 管理器。"""
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
        """429 时尝试切换代理，优先动态代理。"""
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
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"

        # 添加到日志列表
        self.logs.append(log_message)

        # 调用回调函数
        if self.callback_logger:
            self.callback_logger(log_message)

        # 记录到数据库（如果有关联任务）
        if self.task_uuid:
            try:
                with get_db() as db:
                    crud.append_task_log(db, self.task_uuid, log_message)
            except Exception as e:
                logger.warning(f"记录任务日志失败: {e}")

        # 根据级别记录到日志系统
        if level == "error":
            logger.error(message)
        elif level == "warning":
            logger.warning(message)
        else:
            logger.info(message)

    def _generate_password(self, length: int = DEFAULT_PASSWORD_LENGTH) -> str:
        """生成随机密码"""
        return ''.join(secrets.choice(PASSWORD_CHARSET) for _ in range(length))

    def _check_ip_location(self) -> Tuple[bool, Optional[str]]:
        """检查 IP 地理位置"""
        try:
            return self.http_client.check_ip_location()
        except Exception as e:
            self._log(f"检查 IP 地理位置失败: {e}", "error")
            return False, None

    def _create_email(self) -> bool:
        """创建邮箱"""
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

    def _start_oauth(self) -> bool:
        """开始 OAuth 流程"""
        try:
            self._log("开始 OAuth 授权流程...")
            self.oauth_start = self.oauth_manager.start_oauth()
            self._log(f"OAuth URL 已生成: {self.oauth_start.auth_url[:80]}...")
            return True
        except Exception as e:
            self._log(f"生成 OAuth URL 失败: {e}", "error")
            return False

    def _init_session(self) -> bool:
        """初始化会话"""
        try:
            self.session = self.http_client.session
            return True
        except Exception as e:
            self._log(f"初始化会话失败: {e}", "error")
            return False

    def _get_device_id(self) -> Optional[str]:
        """获取 Device ID"""
        if not self.oauth_start:
            return None

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                if not self.session:
                    self.session = self.http_client.session

                response = self.session.get(
                    self.oauth_start.auth_url,
                    timeout=20
                )
                did = self.session.cookies.get("oai-did")

                if did:
                    self._log(f"Device ID: {did}")
                    return did

                self._log(
                    f"获取 Device ID 失败: 未返回 oai-did Cookie (HTTP {response.status_code}, 第 {attempt}/{max_attempts} 次)",
                    "warning" if attempt < max_attempts else "error"
                )
            except Exception as e:
                self._log(
                    f"获取 Device ID 失败: {e} (第 {attempt}/{max_attempts} 次)",
                    "warning" if attempt < max_attempts else "error"
                )

            if attempt < max_attempts:
                time.sleep(attempt)
                self.http_client.close()
                self.session = self.http_client.session

        return None

    def _check_sentinel(self, did: str, flow: str = "authorize_continue") -> Optional[str]:
        """检查 Sentinel 拦截"""
        try:
            sen_req_body = json.dumps({"p": "", "id": did, "flow": flow}, separators=(",", ":"))

            response = self.http_client.post(
                OPENAI_API_ENDPOINTS["sentinel"],
                headers={
                    "origin": "https://sentinel.openai.com",
                    "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                    "content-type": "text/plain;charset=UTF-8",
                },
                data=sen_req_body,
            )

            if response.status_code == 200:
                sen_token = response.json().get("token")
                self._log(f"Sentinel token 获取成功")
                return sen_token
            else:
                self._log(f"Sentinel 检查失败: {response.status_code}", "warning")
                return None

        except Exception as e:
            self._log(f"Sentinel 检查异常: {e}", "warning")
            return None

    @staticmethod
    def _build_sentinel_header(did: str, sen_token: str, flow: str = "authorize_continue") -> str:
        """构造 OpenAI Sentinel 请求头值。"""
        return json.dumps(
            {"p": "", "t": "", "c": sen_token, "id": did, "flow": flow},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _extract_workspace_id_from_payload(payload: Any) -> Optional[str]:
        """从已知响应结构中提取 workspace_id。"""
        if not isinstance(payload, dict):
            return None

        workspaces = payload.get("workspaces") or []
        if isinstance(workspaces, list):
            for item in workspaces:
                workspace_id = str((item or {}).get("id") or "").strip()
                if workspace_id:
                    return workspace_id

        organizations = payload.get("organizations") or payload.get("orgs") or []
        if isinstance(organizations, list):
            for item in organizations:
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

    @staticmethod
    def _extract_continue_url_from_payload(payload: Any) -> Optional[str]:
        """从响应结构中提取继续跳转 URL。"""
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

    def _submit_signup_form(self, did: str, sen_token: Optional[str]) -> SignupFormResult:
        """
        提交注册表单

        Returns:
            SignupFormResult: 提交结果，包含账号状态判断
        """
        try:
            signup_body = f'{{"username":{{"value":"{self.email}","kind":"email"}},"screen_hint":"signup"}}'

            headers = {
                "referer": "https://auth.openai.com/create-account",
                "accept": "application/json",
                "content-type": "application/json",
            }

            if sen_token:
                headers["openai-sentinel-token"] = self._build_sentinel_header(did, sen_token)

            max_attempts = 3
            response = None
            for attempt in range(1, max_attempts + 1):
                response = self.session.post(
                    OPENAI_API_ENDPOINTS["signup"],
                    headers=headers,
                    data=signup_body,
                )

                self._log(
                    f"提交注册表单状态: {response.status_code}"
                    + (f" (第 {attempt}/{max_attempts} 次)" if max_attempts > 1 else "")
                )

                if response.status_code != 429:
                    break

                if attempt >= max_attempts:
                    self._log("提交注册表单触发限流，已达到最大重试次数", "warning")
                    break

                self._rotate_proxy_for_rate_limit()
                retry_delay = min(15 * attempt, 45)
                self._log(
                    f"提交注册表单触发限流(429)，将在 {retry_delay} 秒后重试...",
                    "warning"
                )
                time.sleep(retry_delay)

            if response is None:
                return SignupFormResult(success=False, error_message="未获得注册表单响应")

            if response.status_code != 200:
                return SignupFormResult(
                    success=False,
                    error_message=f"HTTP {response.status_code}: {response.text[:200]}"
                )

            # 解析响应判断账号状态
            try:
                response_data = response.json()
                page_type = response_data.get("page", {}).get("type", "")
                self._log(f"响应页面类型: {page_type}")

                # 判断是否为已注册账号
                is_existing = page_type == OPENAI_PAGE_TYPES["EMAIL_OTP_VERIFICATION"]

                if is_existing:
                    self._log(f"检测到已注册账号，将自动切换到登录流程")
                    self._is_existing_account = True

                return SignupFormResult(
                    success=True,
                    page_type=page_type,
                    is_existing_account=is_existing,
                    response_data=response_data
                )

            except Exception as parse_error:
                self._log(f"解析响应失败: {parse_error}", "warning")
                # 无法解析，默认成功
                return SignupFormResult(success=True)

        except Exception as e:
            self._log(f"提交注册表单失败: {e}", "error")
            return SignupFormResult(success=False, error_message=str(e))

    def _register_password(self) -> Tuple[bool, Optional[str]]:
        """注册密码"""
        try:
            # 生成密码
            password = self._generate_password()
            self.password = password  # 保存密码到实例变量
            self._log(f"生成密码: {password}")

            # 提交密码注册
            register_body = json.dumps({
                "password": password,
                "username": self.email
            })

            response = self.session.post(
                OPENAI_API_ENDPOINTS["register"],
                headers={
                    "referer": "https://auth.openai.com/create-account/password",
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                data=register_body,
            )

            self._log(f"提交密码状态: {response.status_code}")

            if response.status_code != 200:
                error_text = response.text[:500]
                self._log(f"密码注册失败: {error_text}", "warning")

                # 解析错误信息，判断是否是邮箱已注册
                try:
                    error_json = response.json()
                    error_msg = error_json.get("error", {}).get("message", "")
                    error_code = error_json.get("error", {}).get("code", "")

                    # 检测邮箱已注册的情况
                    if "already" in error_msg.lower() or "exists" in error_msg.lower() or error_code == "user_exists":
                        self._log(f"邮箱 {self.email} 可能已在 OpenAI 注册过", "error")
                        # 标记此邮箱为已注册状态
                        self._mark_email_as_registered()
                except Exception:
                    pass

                return False, None

            return True, password

        except Exception as e:
            self._log(f"密码注册失败: {e}", "error")
            return False, None

    def _mark_email_as_registered(self):
        """标记邮箱为已注册状态（用于防止重复尝试）"""
        try:
            with get_db() as db:
                # 检查是否已存在该邮箱的记录
                existing = crud.get_account_by_email(db, self.email)
                if not existing:
                    # 创建一个失败记录，标记该邮箱已注册过
                    crud.create_account(
                        db,
                        email=self.email,
                        password="",  # 空密码表示未成功注册
                        email_service=self.email_service.service_type.value,
                        email_service_id=self.email_info.get("service_id") if self.email_info else None,
                        status="failed",
                        extra_data={"register_failed_reason": "email_already_registered_on_openai"}
                    )
                    self._log(f"已在数据库中标记邮箱 {self.email} 为已注册状态")
        except Exception as e:
            logger.warning(f"标记邮箱状态失败: {e}")

    def _send_verification_code(self) -> bool:
        """发送验证码"""
        try:
            # 记录发送时间戳
            self._otp_sent_at = time.time()

            response = self.session.get(
                OPENAI_API_ENDPOINTS["send_otp"],
                headers={
                    "referer": "https://auth.openai.com/create-account/password",
                    "accept": "application/json",
                },
            )

            self._log(f"验证码发送状态: {response.status_code}")
            return response.status_code == 200

        except Exception as e:
            self._log(f"发送验证码失败: {e}", "error")
            return False

    def _get_verification_code(self) -> Optional[str]:
        """获取验证码"""
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
                # 节流进度日志，避免在邮箱服务快速失败时刷出成千上万行。
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

                # 某些邮箱服务在配置异常/快速失败时会立即返回，这里做兜底节流，
                # 避免外层 while 进入高频忙等。
                attempt_elapsed = time.time() - attempt_started_at
                if attempt_elapsed < 1 and remaining > 1:
                    time.sleep(min(1 - attempt_elapsed, remaining - 1))

        except Exception as e:
            self._log(f"获取验证码失败: {e}", "error")
            return None

    def _validate_verification_code(self, code: str) -> bool:
        """验证验证码"""
        try:
            code_body = f'{{"code":"{code}"}}'
            def _extract_session_email(raw_cookie: str) -> Optional[str]:
                try:
                    segments = raw_cookie.split(".")
                    if len(segments) < 2:
                        return None
                    import base64 as _b64
                    import json as _json
                    seg = segments[0]
                    pad = "=" * ((4 - (len(seg) % 4)) % 4)
                    payload = _json.loads(_b64.urlsafe_b64decode((seg + pad).encode("ascii")).decode("utf-8"))
                    sess_email = (payload.get("email") or "").strip()
                    return sess_email or None
                except Exception:
                    return None
            try:
                auth_cookie = self.session.cookies.get("oai-client-auth-session")
                if auth_cookie:
                    sess_email = _extract_session_email(auth_cookie)
                    if sess_email:
                        self._log(f"验证码校验会话邮箱: {sess_email}")
                        if self.email and sess_email.lower() != self.email.lower():
                            self._log(f"会话邮箱与目标邮箱不一致: {self.email}", "warning")
            except Exception:
                self._log("验证码校验会话邮箱解析失败", "warning")

            response = self.session.post(
                OPENAI_API_ENDPOINTS["validate_otp"],
                headers={
                    "referer": "https://auth.openai.com/email-verification",
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                data=code_body,
            )

            self._log(f"验证码校验状态: {response.status_code}")
            try:
                self._log(f"验证码校验响应体: {(response.text or '')[:200]}", "warning")
            except Exception:
                self._log("验证码校验响应体读取失败", "warning")
            try:
                self._log(f"验证码校验响应头: {dict(response.headers) if response.headers else {}}", "warning")
            except Exception:
                self._log("验证码校验响应头读取失败", "warning")
            try:
                resp_auth_cookie = response.cookies.get("oai-client-auth-session")
                if resp_auth_cookie:
                    resp_sess_email = _extract_session_email(resp_auth_cookie)
                    if resp_sess_email:
                        self._log(f"验证码校验响应会话邮箱: {resp_sess_email}", "warning")
                        if self.email and resp_sess_email.lower() != self.email.lower():
                            self._log(f"响应会话邮箱与目标邮箱不一致: {self.email}", "warning")
            except Exception:
                self._log("验证码校验响应会话邮箱解析失败", "warning")
            return response.status_code == 200

        except Exception as e:
            self._log(f"验证验证码失败: {e}", "error")
            return False

    def _create_user_account(self) -> bool:
        """创建用户账户"""
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
                sentinel_token = self._check_sentinel(self.device_id)
                if sentinel_token:
                    headers["openai-sentinel-token"] = self._build_sentinel_header(
                        self.device_id,
                        sentinel_token,
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
                self._log(f"账户创建失败: {response.text[:200]}", "warning")
                return False

            try:
                response_data = response.json()
                self._create_account_response_data = response_data
                top_level_keys = sorted(response_data.keys())[:20] if isinstance(response_data, dict) else []
                if top_level_keys:
                    self._log(f"创建账户响应键: {top_level_keys}")

                continue_url = self._extract_continue_url_from_payload(response_data)
                if continue_url:
                    self._post_signup_continue_url = continue_url
                    self._log(f"创建账户响应包含 Continue URL: {continue_url[:100]}...")
            except Exception as parse_error:
                self._log(f"创建账户响应非 JSON 或解析失败: {parse_error}", "warning")

            return True

        except Exception as e:
            self._log(f"创建账户失败: {e}", "error")
            return False

    def _get_workspace_id(self) -> Optional[str]:
        """获取 Workspace ID"""
        try:
            workspace_id = self._extract_workspace_id_from_payload(self._create_account_response_data)
            if workspace_id:
                self._log(f"Workspace ID(来自 create_account 响应): {workspace_id}")
                return workspace_id

            auth_cookie = self.session.cookies.get("oai-client-auth-session")
            if not auth_cookie:
                self._log("未能获取到授权 Cookie", "error")
                return None

            import base64
            import json as json_module

            try:
                segments = auth_cookie.split(".")
                if len(segments) < 2:
                    self._log("授权 Cookie 格式错误", "error")
                    return None

                def _decode_segment(segment: str) -> Dict[str, Any]:
                    pad = "=" * ((4 - (len(segment) % 4)) % 4)
                    decoded = base64.urlsafe_b64decode((segment + pad).encode("ascii"))
                    return json_module.loads(decoded.decode("utf-8"))

                # 标准 JWT 的 payload 在第二段；保留第一段回退以兼容非标准格式。
                payload_candidates = []
                for index in (1, 0):
                    try:
                        payload_candidates.append(_decode_segment(segments[index]))
                    except Exception:
                        continue

                for payload in payload_candidates:
                    workspace_id = self._extract_workspace_id_from_payload(payload)
                    if workspace_id:
                        self._log(f"Workspace ID: {workspace_id}")
                        return workspace_id

                payload_keys = []
                for payload in payload_candidates:
                    if isinstance(payload, dict):
                        payload_keys.extend(list(payload.keys()))
                self._log(
                    f"授权 Cookie 里没有 workspace 信息，可见键: {sorted(set(payload_keys))[:20]}",
                    "error"
                )
                return None

            except Exception as e:
                self._log(f"解析授权 Cookie 失败: {e}", "error")
                return None

        except Exception as e:
            self._log(f"获取 Workspace ID 失败: {e}", "error")
            return None

    def _get_continue_url_after_signup(self, workspace_id: Optional[str]) -> Optional[str]:
        """获取创建账号后的 continue_url。"""
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
        max_redirects: int = 16
    ) -> Optional[str]:
        """跟随 OAuth 重定向链，直到拿到 callback code。"""
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
        """选择组织并返回下一步 continue_url。"""
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

    def _perform_post_registration_oauth(self) -> Optional[Dict[str, Any]]:
        """注册成功后重新走一轮 OAuth 登录，获取 token。"""
        if not self.email or not self.password:
            self._log("缺少邮箱或密码，跳过注册后 OAuth", "warning")
            return None

        self._log("13. 开始注册后 OAuth 登录...")
        self.oauth_start = self.oauth_manager.start_oauth()

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
            self.session.cookies.get("oai-did")
            or self.session.cookies.get("oai-did", domain=".openai.com")
            or self.session.cookies.get("oai-did", domain="auth.openai.com")
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
                    flow="password_verify"
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
            if not self._validate_verification_code(code):
                return None
            continue_url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"

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
        """
        使用全新会话重新走一轮验证码登录 + OAuth 授权。

        该流程不复用注册阶段的 session/cookie/device_id，适合补录 ak/rk。
        """
        self.logs = []
        self.email = email
        self.password = password
        self.session_token = None
        self.oauth_start = None
        self.device_id = None
        self._create_account_response_data = None
        self._post_signup_continue_url = None

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
        """选择 Workspace"""
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
        """跟随重定向链，寻找回调 URL"""
        try:
            current_url = start_url
            max_redirects = 6

            for i in range(max_redirects):
                self._log(f"重定向 {i+1}/{max_redirects}: {current_url[:100]}...")

                response = self.session.get(
                    current_url,
                    allow_redirects=False,
                    timeout=15
                )

                location = response.headers.get("Location") or ""

                # 如果不是重定向状态码，停止
                if response.status_code not in [301, 302, 303, 307, 308]:
                    self._log(f"非重定向状态码: {response.status_code}")
                    break

                if not location:
                    self._log("重定向响应缺少 Location 头")
                    break

                # 构建下一个 URL
                import urllib.parse
                next_url = urllib.parse.urljoin(current_url, location)

                # 检查是否包含回调参数
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
        """处理 OAuth 回调"""
        try:
            if not self.oauth_start:
                self._log("OAuth 流程未初始化", "error")
                return None

            self._log("处理 OAuth 回调...")
            token_info = self.oauth_manager.handle_callback(
                callback_url=callback_url,
                expected_state=self.oauth_start.state,
                code_verifier=self.oauth_start.code_verifier
            )

            self._log("OAuth 授权成功")
            return token_info

        except Exception as e:
            self._log(f"处理 OAuth 回调失败: {e}", "error")
            return None

    def run(self) -> RegistrationResult:
        """
        执行完整的注册流程

        支持已注册账号自动登录：
        - 如果检测到邮箱已注册，自动切换到登录流程
        - 已注册账号跳过：设置密码、发送验证码、创建用户账户
        - 共用步骤：获取验证码、验证验证码、Workspace 和 OAuth 回调

        Returns:
            RegistrationResult: 注册结果
        """
        result = RegistrationResult(success=False, logs=self.logs)

        try:
            self._log("=" * 60)
            self._log("开始注册流程")
            self._log("=" * 60)

            # 1. 检查 IP 地理位置
            self._log("1. 检查 IP 地理位置...")
            ip_ok, location = self._check_ip_location()
            if not ip_ok:
                result.error_message = f"IP 地理位置不支持: {location}"
                self._log(f"IP 检查失败: {location}", "error")
                return result

            self._log(f"IP 位置: {location}")

            # 2. 创建邮箱
            self._log("2. 创建邮箱...")
            if not self._create_email():
                result.error_message = "创建邮箱失败"
                return result

            result.email = self.email

            # 3. 初始化会话
            self._log("3. 初始化会话...")
            if not self._init_session():
                result.error_message = "初始化会话失败"
                return result

            # 4. 开始 OAuth 流程
            self._log("4. 开始 OAuth 授权流程...")
            if not self._start_oauth():
                result.error_message = "开始 OAuth 流程失败"
                return result

            # 5. 获取 Device ID
            self._log("5. 获取 Device ID...")
            did = self._get_device_id()
            if not did:
                result.error_message = "获取 Device ID 失败"
                return result
            self.device_id = did

            # 6. 检查 Sentinel 拦截
            self._log("6. 检查 Sentinel 拦截...")
            sen_token = self._check_sentinel(did)
            if sen_token:
                self._log("Sentinel 检查通过")
            else:
                self._log("Sentinel 检查失败或未启用", "warning")

            # 7. 提交注册表单 + 解析响应判断账号状态
            self._log("7. 提交注册表单...")
            signup_result = self._submit_signup_form(did, sen_token)
            if not signup_result.success:
                result.error_message = f"提交注册表单失败: {signup_result.error_message}"
                return result

            # 8. [已注册账号跳过] 注册密码
            if self._is_existing_account:
                self._log("8. [已注册账号] 跳过密码设置，OTP 已自动发送")
            else:
                self._log("8. 注册密码...")
                password_ok, password = self._register_password()
                if not password_ok:
                    result.error_message = "注册密码失败"
                    return result

            # 9. [已注册账号跳过] 发送验证码
            if self._is_existing_account:
                self._log("9. [已注册账号] 跳过发送验证码，使用自动发送的 OTP")
                # 已注册账号的 OTP 在提交表单时已自动发送，记录时间戳
                self._otp_sent_at = time.time()
            else:
                self._log("9. 发送验证码...")
                if not self._send_verification_code():
                    result.error_message = "发送验证码失败"
                    return result

            # 10. 获取验证码
            self._log("10. 等待验证码...")
            code = self._get_verification_code()
            if not code:
                result.error_message = "获取验证码失败"
                return result

            # 11. 验证验证码
            self._log("11. 验证验证码...")
            if not self._validate_verification_code(code):
                result.error_message = "验证验证码失败"
                return result

            # 12. [已注册账号跳过] 创建用户账户
            if self._is_existing_account:
                self._log("12. [已注册账号] 跳过创建用户账户")
            else:
                self._log("12. 创建用户账户...")
                if not self._create_user_account():
                    result.error_message = "创建用户账户失败"
                    return result

            token_info = self._perform_post_registration_oauth()

            # 提取账户信息
            if token_info:
                result.account_id = token_info.get("account_id", "")
                result.access_token = token_info.get("access_token", "")
                result.refresh_token = token_info.get("refresh_token", "")
                result.id_token = token_info.get("id_token", "")
            else:
                self._log("注册已完成，但注册后 OAuth 未完成；账号将以无 token 状态保存", "warning")
            result.password = self.password or ""  # 保存密码（已注册账号为空）

            # 设置来源标记
            result.source = "login" if self._is_existing_account else "register"

            # 尝试获取 session_token 从 cookie
            session_cookie = self.session.cookies.get("__Secure-next-auth.session-token")
            if session_cookie:
                self.session_token = session_cookie
                result.session_token = session_cookie
                self._log(f"获取到 Session Token")

            workspace_id = self._get_workspace_id()
            if workspace_id:
                result.workspace_id = workspace_id

            # 17. 完成
            self._log("=" * 60)
            if self._is_existing_account:
                self._log("登录成功! (已注册账号)")
            else:
                self._log("注册成功!")
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
            }

            return result

        except Exception as e:
            self._log(f"注册过程中发生未预期错误: {e}", "error")
            result.error_message = str(e)
            return result

    def save_to_database(self, result: RegistrationResult) -> bool:
        """
        保存注册结果到数据库

        Args:
            result: 注册结果

        Returns:
            是否保存成功
        """
        if not result.success:
            return False

        try:
            # 获取默认 client_id
            settings = get_settings()

            with get_db() as db:
                # 保存账户信息
                account = crud.create_account(
                    db,
                    email=result.email,
                    password=result.password,
                    client_id=settings.openai_client_id,
                    session_token=result.session_token,
                    email_service=self.email_service.service_type.value,
                    email_service_id=self.email_info.get("service_id") if self.email_info else None,
                    account_id=result.account_id,
                    workspace_id=result.workspace_id,
                    access_token=result.access_token,
                    refresh_token=result.refresh_token,
                    id_token=result.id_token,
                    proxy_used=self.proxy_url,
                    extra_data=result.metadata,
                    source=result.source
                )

                self._log(f"账户已保存到数据库，ID: {account.id}")
                return True

        except Exception as e:
            self._log(f"保存到数据库失败: {e}", "error")
            return False
