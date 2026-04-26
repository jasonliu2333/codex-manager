"""
Temp-Mail 邮箱服务实现
基于自部署 Cloudflare Worker 临时邮箱服务
接口文档参见 plan/temp-mail.md
"""

import re
import time
import json
import logging
from datetime import datetime, timezone
from email import message_from_string
from email.header import decode_header, make_header
from email.message import Message
from email.policy import default as email_policy
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Optional, Dict, Any, List

from .base import BaseEmailService, EmailServiceError, EmailServiceType
from ..core.http_client import HTTPClient, RequestConfig
from ..config.constants import OTP_CODE_PATTERN


logger = logging.getLogger(__name__)


class TempMailService(BaseEmailService):
    """
    Temp-Mail 邮箱服务
    基于自部署 Cloudflare Worker 的临时邮箱，admin 模式管理邮箱
    不走代理，不使用 requests 库
    """

    def __init__(self, config: Dict[str, Any] = None, name: str = None):
        """
        初始化 TempMail 服务

        Args:
            config: 配置字典，支持以下键:
                - base_url: Worker 域名地址，如 https://mail.example.com (必需)
                - admin_password: Admin 密码，对应 x-admin-auth header (必需)
                - domain: 邮箱域名，如 example.com (必需)
                - enable_prefix: 是否启用前缀，默认 True
                - timeout: 请求超时时间，默认 30
                - max_retries: 最大重试次数，默认 3
            name: 服务名称
        """
        super().__init__(EmailServiceType.TEMP_MAIL, name)

        required_keys = ["base_url", "admin_password", "domain"]
        missing_keys = [key for key in required_keys if not (config or {}).get(key)]
        if missing_keys:
            raise ValueError(f"缺少必需配置: {missing_keys}")

        default_config = {
            "enable_prefix": True,
            "timeout": 30,
            "max_retries": 3,
        }
        self.config = {**default_config, **(config or {})}

        # 不走代理，proxy_url=None
        http_config = RequestConfig(
            timeout=self.config["timeout"],
            max_retries=self.config["max_retries"],
        )
        self.http_client = HTTPClient(proxy_url=None, config=http_config)

        # 邮箱缓存：email -> {jwt, address}
        self._email_cache: Dict[str, Dict[str, Any]] = {}
        # 已使用的邮件 ID：email -> set(ids)
        self._used_mail_ids: Dict[str, set] = {}

    def _decode_mime_header(self, value: str) -> str:
        """解码 MIME 头，兼容 RFC 2047 编码主题。"""
        if not value:
            return ""
        try:
            return str(make_header(decode_header(value)))
        except Exception:
            return value

    def _extract_body_from_message(self, message: Message) -> str:
        """从 MIME 邮件对象中提取可读正文。"""
        parts: List[str] = []

        if message.is_multipart():
            for part in message.walk():
                if part.get_content_maintype() == "multipart":
                    continue

                content_type = (part.get_content_type() or "").lower()
                if content_type not in ("text/plain", "text/html"):
                    continue

                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace") if payload else ""
                except Exception:
                    try:
                        text = part.get_content()
                    except Exception:
                        text = ""

                if content_type == "text/html":
                    text = re.sub(r"<[^>]+>", " ", text)
                parts.append(text)
        else:
            try:
                payload = message.get_payload(decode=True)
                charset = message.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace") if payload else ""
            except Exception:
                try:
                    body = message.get_content()
                except Exception:
                    body = str(message.get_payload() or "")

            if "html" in (message.get_content_type() or "").lower():
                body = re.sub(r"<[^>]+>", " ", body)
            parts.append(body)

        return unescape("\n".join(part for part in parts if part).strip())

    def _extract_mail_fields(self, mail: Dict[str, Any]) -> Dict[str, str]:
        """统一提取邮件字段，兼容 raw MIME 和不同 Worker 返回格式。"""
        sender = str(
            mail.get("source")
            or mail.get("from")
            or mail.get("from_address")
            or mail.get("fromAddress")
            or ""
        ).strip()
        to_addr = str(
            mail.get("to")
            or mail.get("to_address")
            or mail.get("recipient")
            or mail.get("address")
            or ""
        ).strip()
        subject = str(mail.get("subject") or mail.get("title") or "").strip()
        body_text = str(
            mail.get("text")
            or mail.get("body")
            or mail.get("content")
            or mail.get("html")
            or ""
        ).strip()
        raw = str(mail.get("raw") or "").strip()

        if raw:
            try:
                message = message_from_string(raw, policy=email_policy)
                sender = sender or self._decode_mime_header(message.get("From", ""))
                to_addr = to_addr or self._decode_mime_header(message.get("To", ""))
                subject = subject or self._decode_mime_header(message.get("Subject", ""))
                parsed_body = self._extract_body_from_message(message)
                if parsed_body:
                    body_text = f"{body_text}\n{parsed_body}".strip() if body_text else parsed_body
            except Exception as e:
                logger.debug(f"解析 TempMail raw 邮件失败: {e}")
                body_text = f"{body_text}\n{raw}".strip() if body_text else raw

        body_text = unescape(re.sub(r"<[^>]+>", " ", body_text))
        return {
            "sender": sender,
            "to": to_addr,
            "subject": subject,
            "body": body_text,
            "raw": raw,
        }

    def _extract_mail_timestamp(self, mail: Dict[str, Any]) -> Optional[float]:
        """提取邮件时间戳，兼容秒/毫秒时间戳与 ISO 日期字符串。"""
        for key in ("createdAt", "created_at", "date", "timestamp", "receivedAt", "received_at"):
            raw_value = mail.get(key)
            if raw_value in (None, ""):
                continue

            if isinstance(raw_value, (int, float)):
                value = float(raw_value)
                return value / 1000 if value > 10**12 else value

            value = str(raw_value).strip()
            if not value:
                continue

            try:
                numeric = float(value)
                return numeric / 1000 if numeric > 10**12 else numeric
            except ValueError:
                pass

            try:
                normalized = value.replace("Z", "+00:00")
                dt = datetime.fromisoformat(normalized)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except ValueError:
                continue

        raw = mail.get("raw")
        if raw:
            try:
                message = message_from_string(str(raw), policy=email_policy)
                date_header = message.get("Date")
                if date_header:
                    dt = parsedate_to_datetime(date_header)
                    if dt is not None:
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt.timestamp()
            except Exception:
                pass

        return None

    def _admin_headers(self) -> Dict[str, str]:
        """构造 admin 请求头"""
        return {
            "x-admin-auth": self.config["admin_password"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _make_request(self, method: str, path: str, **kwargs) -> Any:
        """
        发送请求并返回 JSON 数据

        Args:
            method: HTTP 方法
            path: 请求路径（以 / 开头）
            **kwargs: 传递给 http_client.request 的额外参数

        Returns:
            响应 JSON 数据

        Raises:
            EmailServiceError: 请求失败
        """
        base_url = self.config["base_url"].rstrip("/")
        url = f"{base_url}{path}"

        # 合并默认 admin headers
        kwargs.setdefault("headers", {})
        for k, v in self._admin_headers().items():
            kwargs["headers"].setdefault(k, v)

        try:
            response = self.http_client.request(method, url, **kwargs)

            if response.status_code >= 400:
                error_msg = f"请求失败: {response.status_code}"
                try:
                    error_data = response.json()
                    error_msg = f"{error_msg} - {error_data}"
                except Exception:
                    error_msg = f"{error_msg} - {response.text[:200]}"
                self.update_status(False, EmailServiceError(error_msg))
                raise EmailServiceError(error_msg)

            try:
                return response.json()
            except json.JSONDecodeError:
                return {"raw_response": response.text}

        except Exception as e:
            self.update_status(False, e)
            if isinstance(e, EmailServiceError):
                raise
            raise EmailServiceError(f"请求失败: {method} {path} - {e}")

    def create_email(self, config: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        通过 admin API 创建临时邮箱

        Returns:
            包含邮箱信息的字典:
            - email: 邮箱地址
            - jwt: 用户级 JWT token
            - service_id: 同 email（用作标识）
        """
        import random
        import string

        # 生成随机邮箱名
        letters = ''.join(random.choices(string.ascii_lowercase, k=5))
        digits = ''.join(random.choices(string.digits, k=random.randint(1, 3)))
        suffix = ''.join(random.choices(string.ascii_lowercase, k=random.randint(1, 3)))
        name = letters + digits + suffix

        domain = self.config["domain"]
        enable_prefix = self.config.get("enable_prefix", True)

        body = {
            "enablePrefix": enable_prefix,
            "name": name,
            "domain": domain,
        }

        try:
            response = self._make_request("POST", "/admin/new_address", json=body)

            address = response.get("address", "").strip()
            jwt = response.get("jwt", "").strip()

            if not address:
                raise EmailServiceError(f"API 返回数据不完整: {response}")

            email_info = {
                "email": address,
                "jwt": jwt,
                "service_id": address,
                "id": address,
                "created_at": time.time(),
            }

            # 缓存 jwt，供获取验证码时使用
            self._email_cache[address] = email_info

            logger.info(f"成功创建 TempMail 邮箱: {address}")
            self.update_status(True)
            return email_info

        except Exception as e:
            self.update_status(False, e)
            if isinstance(e, EmailServiceError):
                raise
            raise EmailServiceError(f"创建邮箱失败: {e}")

    def get_verification_code(
        self,
        email: str,
        email_id: str = None,
        timeout: int = 120,
        pattern: str = OTP_CODE_PATTERN,
        otp_sent_at: Optional[float] = None,
    ) -> Optional[str]:
        """
        从 TempMail 邮箱获取验证码

        Args:
            email: 邮箱地址
            email_id: 未使用，保留接口兼容
            timeout: 超时时间（秒）
            pattern: 验证码正则
            otp_sent_at: OTP 发送时间戳，用于过滤旧邮件

        Returns:
            验证码字符串，超时返回 None
        """
        logger.info(f"正在从 TempMail 邮箱 {email} 获取验证码...")

        start_time = time.time()
        target_email = (email or "").strip().lower()
        used_ids = self._used_mail_ids.setdefault(target_email, set())
        seen_mail_ids: set = set()
        use_address_filter = True
        empty_cycles = 0
        time_skew_seconds = 120

        # 优先使用用户级 JWT，回退到 admin API 先注释用户级API
        # cached = self._email_cache.get(email, {})
        # jwt = cached.get("jwt")

        while time.time() - start_time < timeout:
            try:
                # if jwt:
                #     response = self._make_request(
                #         "GET",
                #         "/user_api/mails",
                #         params={"limit": 20, "offset": 0},
                #         headers={"x-user-token": jwt, "Content-Type": "application/json", "Accept": "application/json"},
                #     )
                # else:
                params = {"limit": 20, "offset": 0}
                if use_address_filter:
                    params["address"] = email
                response = self._make_request("GET", "/admin/mails", params=params)

                # /user_api/mails 和 /admin/mails 返回格式相同: {"results": [...], "total": N}
                mails = response.get("results", [])
                if not isinstance(mails, list):
                    time.sleep(3)
                    continue
                if not mails:
                    empty_cycles += 1
                    if use_address_filter and empty_cycles >= 2:
                        use_address_filter = False
                    time.sleep(3)
                    continue
                empty_cycles = 0

                def _mail_sort_key(item: Dict[str, Any]) -> tuple:
                    mail_ts = self._extract_mail_timestamp(item)
                    mail_id = item.get("id")
                    mail_id_num = 0
                    if mail_id is not None:
                        try:
                            mail_id_num = int(str(mail_id))
                        except Exception:
                            mail_id_num = 0
                    return (mail_ts or 0, mail_id_num)

                def _extract_emails(value: Any) -> List[str]:
                    emails: List[str] = []
                    if value is None:
                        return emails
                    if isinstance(value, (list, tuple, set)):
                        for item in value:
                            emails.extend(_extract_emails(item))
                        return emails
                    if isinstance(value, dict):
                        for key in ("address", "email", "value"):
                            if key in value:
                                emails.extend(_extract_emails(value.get(key)))
                                return emails
                        return emails
                    text = str(value)
                    if not text:
                        return emails
                    matches = re.findall(r"[\w.+-]+@[\w.-]+\.\w+", text)
                    if matches:
                        emails.extend(matches)
                        return emails
                    parts = re.split(r"[;,\\s]+", text)
                    emails.extend([part for part in parts if "@" in part])
                    return emails

                for mail in sorted(mails, key=_mail_sort_key, reverse=True):
                    mail_id = mail.get("id")
                    if not mail_id or mail_id in seen_mail_ids or mail_id in used_ids:
                        continue

                    seen_mail_ids.add(mail_id)

                    mail_timestamp = self._extract_mail_timestamp(mail)
                    if otp_sent_at and mail_timestamp and mail_timestamp < (otp_sent_at - time_skew_seconds):
                        continue

                    parsed = self._extract_mail_fields(mail)
                    if not parsed["body"] and not parsed["raw"] and mail_id:
                        try:
                            detail = self._make_request("GET", f"/admin/mails/{mail_id}")
                            if isinstance(detail, dict):
                                parsed = self._extract_mail_fields({**mail, **detail})
                        except Exception:
                            pass
                    sender = parsed["sender"].lower()
                    subject = parsed["subject"]
                    body_text = parsed["body"]
                    raw_text = parsed["raw"]
                    mail_to = parsed.get("to") or ""
                    address_candidates = set()
                    for addr_val in (
                        mail.get("address"),
                        mail.get("to"),
                        mail.get("to_address"),
                        mail.get("recipient"),
                        mail_to,
                    ):
                        for item in _extract_emails(addr_val):
                            address_candidates.add(item.lower())
                    if address_candidates and target_email and target_email not in address_candidates:
                        continue

                    raw_body = ""
                    if raw_text:
                        if "\r\n\r\n" in raw_text:
                            raw_body = raw_text.split("\r\n\r\n", 1)[1]
                        elif "\n\n" in raw_text:
                            raw_body = raw_text.split("\n\n", 1)[1]
                        else:
                            raw_body = raw_text

                    def _find_code(text: str) -> Optional[str]:
                        if not text:
                            return None
                        m = re.search(pattern, text)
                        return m.group(1) if m else None

                    code = (
                        _find_code(subject)
                        or _find_code(body_text)
                        or _find_code(raw_body)
                        or _find_code(raw_text)
                    )
                    if code:
                        if not mail_timestamp and otp_sent_at:
                            # 没有时间戳时，尽量依赖关键字过滤，避免误匹配
                            content_lower = f"{subject}\n{body_text}\n{raw_body}".lower()
                            if not any(key in content_lower for key in ("openai", "chatgpt", "验证码", "code")):
                                continue
                        used_ids.add(mail_id)
                        self._used_mail_ids[target_email] = used_ids
                        logger.info(
                            f"从 TempMail 邮箱 {email} 找到验证码: {code} "
                            f"(mail_id={mail_id}, ts={mail_timestamp})"
                        )
                        self.update_status(True)
                        return code

            except Exception as e:
                logger.debug(f"检查 TempMail 邮件时出错: {e}")

            time.sleep(3)

        logger.warning(f"等待 TempMail 验证码超时: {email}")
        return None

    def list_emails(self, limit: int = 100, offset: int = 0, **kwargs) -> List[Dict[str, Any]]:
        """
        列出邮箱

        Args:
            limit: 返回数量上限
            offset: 分页偏移
            **kwargs: 额外查询参数，透传给 admin API

        Returns:
            邮箱列表
        """
        params = {
            "limit": limit,
            "offset": offset,
        }
        params.update({k: v for k, v in kwargs.items() if v is not None})

        try:
            response = self._make_request("GET", "/admin/mails", params=params)
            mails = response.get("results", [])
            if not isinstance(mails, list):
                raise EmailServiceError(f"API 返回数据格式错误: {response}")

            emails: List[Dict[str, Any]] = []
            for mail in mails:
                address = (mail.get("address") or "").strip()
                mail_id = mail.get("id") or address
                email_info = {
                    "id": mail_id,
                    "service_id": mail_id,
                    "email": address,
                    "subject": mail.get("subject"),
                    "from": mail.get("source"),
                    "created_at": mail.get("createdAt") or mail.get("created_at"),
                    "raw_data": mail,
                }
                emails.append(email_info)

                if address:
                    cached = self._email_cache.get(address, {})
                    self._email_cache[address] = {**cached, **email_info}

            self.update_status(True)
            return emails
        except Exception as e:
            logger.warning(f"列出 TempMail 邮箱失败: {e}")
            self.update_status(False, e)
            return list(self._email_cache.values())

    def delete_email(self, email_id: str) -> bool:
        """
        删除邮箱

        Note:
            当前 TempMail admin API 文档未见删除地址接口，这里先从本地缓存移除，
            以满足统一接口并避免服务实例化失败。
        """
        removed = False
        emails_to_delete = []

        for address, info in self._email_cache.items():
            candidate_ids = {
                address,
                info.get("id"),
                info.get("service_id"),
            }
            if email_id in candidate_ids:
                emails_to_delete.append(address)

        for address in emails_to_delete:
            self._email_cache.pop(address, None)
            removed = True

        if removed:
            logger.info(f"已从 TempMail 缓存移除邮箱: {email_id}")
            self.update_status(True)
        else:
            logger.info(f"TempMail 缓存中未找到邮箱: {email_id}")

        return removed

    def check_health(self) -> bool:
        """检查服务健康状态"""
        try:
            self._make_request(
                "GET",
                "/admin/mails",
                params={"limit": 1, "offset": 0},
            )
            self.update_status(True)
            return True
        except Exception as e:
            logger.warning(f"TempMail 健康检查失败: {e}")
            self.update_status(False, e)
            return False
