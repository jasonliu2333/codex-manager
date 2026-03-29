"""
Tuta 邮箱服务实现（内置取件逻辑）
"""

import base64
import json
import logging
import os
import re
import time
import html
from datetime import datetime
from typing import Dict, Any, List, Optional

from .base import BaseEmailService, EmailServiceError, EmailServiceType
from .tuta_client import TutaMailClient
from ..config.constants import OTP_CODE_PATTERN

logger = logging.getLogger(__name__)


def _generate_client_id() -> str:
    raw = os.urandom(6)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")[:8]


class TutaMailService(BaseEmailService):
    """
    Tuta 邮箱服务

    依赖内置的 TutaMailClient / TutaCryptoCore 提供登录与取件能力。
    """

    def __init__(self, config: Dict[str, Any], name: Optional[str] = None):
        super().__init__(EmailServiceType.TUTA, name)
        default_config = {
            "base_url": "https://app.tuta.com",
            "timeout": 30,
            "poll_interval": 3,
            "max_mails": 10,
            "proxy_url": None,
            "auth_token": None,
            "access_key": None,
            "otp_sender_keywords": ["openai.com"],
            "otp_subject_keywords": ["chatgpt", "verification", "code", "security", "验证码"],
            "otp_body_pattern": r"\b\d{6}\b",
        }
        self.config = {**default_config, **(config or {})}

    def _create_client(self):
        return TutaMailClient(
            proxy_url=self.config.get("proxy_url"),
            base_url=self.config.get("base_url"),
            timeout=self.config.get("timeout", 30),
        )

    def _ensure_login(self, client: TutaMailClient, email: str, password: str) -> Dict[str, Any]:
        salt_b64 = self.config.get("salt_b64")
        status, session_data = client.create_session(
            email,
            password,
            salt_b64,
            access_key=self.config.get("access_key"),
            auth_token=self.config.get("auth_token"),
        )
        if status not in (200, 201):
            raise EmailServiceError(f"登录失败: {status} {session_data}")
        return session_data or {}

    def bootstrap_credentials(self) -> Dict[str, Any]:
        email = (self.config.get("email") or "").strip().lower()
        password = (self.config.get("password") or "").strip()
        if not email or not password:
            raise EmailServiceError("缺少邮箱或密码")
        if "@" not in email:
            raise EmailServiceError("邮箱地址无效")

        updated = dict(self.config or {})
        updated["email"] = email
        updated["password"] = password
        if not updated.get("client_id"):
            updated["client_id"] = _generate_client_id()

        access_token = updated.get("access_token")
        user_id = updated.get("user_id")

        client = self._create_client()
        if access_token and user_id:
            client.access_token = access_token
            client.user_id = user_id
            status, _ = client.get_user()
            if status == 200:
                if client.salt_b64:
                    updated["salt_b64"] = client.salt_b64
                return updated

        self._ensure_login(client, email, password)
        if not client.access_token or not client.user_id:
            status, _ = client.get_user()
            if status != 200:
                raise EmailServiceError(f"get_user failed: {status}")

        updated["access_token"] = client.access_token
        updated["user_id"] = client.user_id
        if client.salt_b64:
            updated["salt_b64"] = client.salt_b64
        return updated

    def _read_mail_texts(self, output_dir: str) -> List[str]:
        texts: List[str] = []
        json_path = os.path.join(output_dir, "mail_readable.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                texts.extend(self._flatten_mail_json(data))
            except Exception as e:
                logger.debug(f"读取 mail_readable.json 失败: {e}")

        txt_path = os.path.join(output_dir, "mail_readable.txt")
        if os.path.exists(txt_path):
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    texts.append(f.read())
            except Exception as e:
                logger.debug(f"读取 mail_readable.txt 失败: {e}")

        plain_path = os.path.join(output_dir, "mail_plain.txt")
        if os.path.exists(plain_path):
            try:
                with open(plain_path, "r", encoding="utf-8") as f:
                    texts.append(f.read())
            except Exception as e:
                logger.debug(f"读取 mail_plain.txt 失败: {e}")

        return texts

    def _flatten_mail_json(self, data: Any) -> List[str]:
        texts: List[str] = []

        def render_item(item: Dict[str, Any]) -> str:
            parts = []
            for key in ("from", "sender", "from_address", "fromAddress"):
                if item.get(key):
                    parts.append(str(item.get(key)))
                    break
            subject = item.get("subject") or item.get("title")
            if subject:
                parts.append(str(subject))
            for key in ("body_plain", "body", "content", "text", "plain"):
                if item.get(key):
                    parts.append(str(item.get(key)))
                    break
            return "\n".join(parts)

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    texts.append(render_item(item))
                else:
                    texts.append(str(item))
        elif isinstance(data, dict):
            for key in ("mails", "messages", "items", "data"):
                if isinstance(data.get(key), list):
                    for item in data.get(key):
                        if isinstance(item, dict):
                            texts.append(render_item(item))
                        else:
                            texts.append(str(item))
            if not texts:
                texts.append(json.dumps(data, ensure_ascii=False))
        else:
            texts.append(str(data))

        return texts

    def _extract_code_from_text(
        self,
        text: str,
        pattern: str,
        sender_keywords: List[str],
        subject_keywords: List[str],
    ) -> Optional[str]:
        if not text:
            return None

        if "<" in text and ">" in text:
            text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)

        lowered = text.lower()
        sender_hit = any(k in lowered for k in sender_keywords) if sender_keywords else False
        subject_hit = any(k in lowered for k in subject_keywords) if subject_keywords else False

        if sender_keywords or subject_keywords:
            if not (sender_hit or subject_hit):
                return None

        match = re.search(pattern, text)
        if match:
            return match.group(0)

        return None

    def create_email(self, config: Dict[str, Any] = None) -> Dict[str, Any]:
        email = self.config.get("email") or (config or {}).get("email")
        password = self.config.get("password") or (config or {}).get("password")

        if not email or not password:
            self.update_status(False, EmailServiceError("缺少邮箱或密码"))
            raise EmailServiceError("缺少邮箱或密码")

        self.update_status(True)
        return {
            "email": email,
            "service_id": email,
            "id": email,
            "account": {
                "email": email,
                "has_access_token": bool(self.config.get("access_token")),
            }
        }

    def get_verification_code(
        self,
        email: str,
        email_id: str = None,
        timeout: int = 120,
        pattern: str = OTP_CODE_PATTERN,
        otp_sent_at: Optional[float] = None,
    ) -> Optional[str]:
        password = self.config.get("password")
        if not email or not password:
            self.update_status(False, EmailServiceError("缺少邮箱或密码"))
            return None

        sender_keywords = [k.lower() for k in self.config.get("otp_sender_keywords", []) if k]
        subject_keywords = [k.lower() for k in self.config.get("otp_subject_keywords", []) if k]
        body_pattern = self.config.get("otp_body_pattern") or pattern or OTP_CODE_PATTERN
        if isinstance(body_pattern, str):
            if "\\\\b" in body_pattern:
                body_pattern = body_pattern.replace("\\\\b", "\\b")
            if "\\\\d" in body_pattern:
                body_pattern = body_pattern.replace("\\\\d", "\\d")
            if "\x08" in body_pattern:
                body_pattern = body_pattern.replace("\x08", "\\b")
        poll_interval = int(self.config.get("poll_interval") or 3)
        max_mails = int(self.config.get("max_mails") or 10)

        start_time = time.time()
        attempt = 0

        access_token = self.config.get("access_token")
        user_id = self.config.get("user_id")
        salt_b64 = self.config.get("salt_b64")
        token_refreshed = False

        if not access_token or not user_id:
            try:
                updated = self.bootstrap_credentials()
                self.config.update(updated)
                access_token = updated.get("access_token")
                user_id = updated.get("user_id")
                salt_b64 = updated.get("salt_b64") or salt_b64
            except Exception as e:
                logger.warning(f"Tuta 初始化 access_token 失败: {e}")
                self.update_status(False, e)
                return None

        while time.time() - start_time < timeout:
            attempt += 1
            try:
                client = self._create_client()
                if access_token and user_id:
                    client.access_token = access_token
                    client.user_id = user_id
                    status, _ = client.get_user()
                    if status != 200 and not token_refreshed:
                        updated = self.bootstrap_credentials()
                        self.config.update(updated)
                        access_token = updated.get("access_token")
                        user_id = updated.get("user_id")
                        salt_b64 = updated.get("salt_b64") or salt_b64
                        token_refreshed = True
                        client.access_token = access_token
                        client.user_id = user_id
                        client.get_user()
                else:
                    updated = self.bootstrap_credentials()
                    self.config.update(updated)
                    access_token = updated.get("access_token")
                    user_id = updated.get("user_id")
                    salt_b64 = updated.get("salt_b64") or salt_b64
                    client.access_token = access_token
                    client.user_id = user_id
                    client.get_user()
                if salt_b64:
                    client.salt_b64 = salt_b64

                safe_email = email.replace("@", "_").replace(".", "_")
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_dir = os.path.join("data", "tuta_mail", safe_email, ts)
                os.makedirs(output_dir, exist_ok=True)

                client.download_mail_details(
                    output_dir=output_dir,
                    max_mails=max_mails,
                    decrypt=True,
                    password=password,
                )

                texts = self._read_mail_texts(output_dir)
                for text in texts:
                    code = self._extract_code_from_text(
                        text=text,
                        pattern=body_pattern,
                        sender_keywords=sender_keywords,
                        subject_keywords=subject_keywords,
                    )
                    if code:
                        logger.info(f"[{email}] 已提取验证码，准备提交")
                        self.update_status(True)
                        return code

                if sender_keywords or subject_keywords:
                    for text in texts:
                        code = self._extract_code_from_text(
                            text=text,
                            pattern=body_pattern,
                            sender_keywords=[],
                            subject_keywords=[],
                        )
                        if code:
                            logger.info(f"[{email}] 关键词未命中，已回退匹配到验证码")
                            self.update_status(True)
                            return code

            except Exception as e:
                logger.warning(f"Tuta 获取验证码失败: {e}")
                self.update_status(False, e)
                err_text = str(e)
                fatal_keywords = ["缺少", "登录失败", "create_session failed", "auth", "unauthorized"]
                if "429" in err_text:
                    pass
                elif any(k in err_text for k in fatal_keywords):
                    return None

            remaining = timeout - (time.time() - start_time)
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, max(1, remaining)))

        return None

    def list_emails(self, **kwargs) -> List[Dict[str, Any]]:
        return []

    def delete_email(self, email_id: str) -> bool:
        return False

    def check_health(self) -> bool:
        email = self.config.get("email")
        password = self.config.get("password")
        if not email or not password:
            self.update_status(False, EmailServiceError("缺少邮箱或密码"))
            return False

        try:
            client = self._create_client()
            access_token = self.config.get("access_token")
            user_id = self.config.get("user_id")
            if access_token and user_id:
                client.access_token = access_token
                client.user_id = user_id

            status, _ = client.get_user() if client.access_token and client.user_id else (None, None)
            if status == 200:
                self.update_status(True)
                return True

            status, data = client.create_session(
                email,
                password,
                self.config.get("salt_b64"),
                access_key=self.config.get("access_key"),
                auth_token=self.config.get("auth_token"),
            )
            if status not in (200, 201):
                if status == 429:
                    self.update_status(False, EmailServiceError("create_session failed: 429 (触发限流，请稍后重试)"))
                else:
                    self.update_status(False, EmailServiceError(f"create_session failed: {status}"))
                return False

            status, _ = client.get_user()
            ok = status == 200
            if not ok:
                self.update_status(False, EmailServiceError(f"get_user failed: {status}"))
            else:
                self.update_status(True)
            return ok
        except Exception as e:
            self.update_status(False, e)
            return False
