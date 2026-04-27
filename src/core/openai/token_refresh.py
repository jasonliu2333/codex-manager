"""
Token 刷新模块
支持 Session Token 和 OAuth Refresh Token 两种刷新方式
"""

import logging
import json
import time
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

from curl_cffi import requests as cffi_requests

from ...config.settings import get_settings
from ...database.session import get_db
from ...database import crud
from ...database.models import Account

logger = logging.getLogger(__name__)


def _error_text(response: cffi_requests.Response) -> str:
    """尽量从响应中提取可读错误文本。"""
    body_text = (response.text or "").strip()
    try:
        body = response.json()
        if isinstance(body, dict):
            error_obj = body.get("error")
            if isinstance(error_obj, dict):
                message = str(error_obj.get("message") or error_obj.get("code") or "").strip()
                if message:
                    return message
            for key in ("error_description", "message", "detail", "code"):
                message = str(body.get(key) or "").strip()
                if message:
                    return message
    except Exception:
        pass
    return body_text[:300]


def _is_deleted_or_deactivated(error_message: str) -> bool:
    text = str(error_message or "").lower()
    return (
        "deleted or deactivated" in text
        or "do not have an account because it has been deleted or deactivated" in text
        or "account has been deactivated" in text
    )


def _is_forbidden_or_banned(error_message: str) -> bool:
    text = str(error_message or "").lower()
    if _looks_like_html_challenge(text):
        return False
    return (
        "账号可能被封禁" in text
        or "account banned" in text
        or "user banned" in text
        or "account suspended" in text
        or "user suspended" in text
        or "your account has been suspended" in text
    )


def _looks_like_html_challenge(text: str) -> bool:
    text = str(text or "").lower()
    return (
        "<html" in text
        or "<!doctype html" in text
        or "<head>" in text
        or "cloudflare" in text
        or "just a moment" in text
        or "enable javascript" in text
        or "cf-ray" in text
    )


def _should_mark_oauth_recovery_required(error_message: str) -> bool:
    text = str(error_message or "").lower()
    markers = [
        "refresh_token 已失效",
        "refresh token has already been used",
        "一次性令牌已被使用",
        "invalid_grant",
        "refresh_token 无效或已过期",
        "账号没有可用的刷新方式",
        "缺少 session_token 和 refresh_token",
    ]
    return any(marker.lower() in text for marker in markers)


def _mark_account_deleted_or_deactivated(db, account: Account, reason: str) -> None:
    extra = dict(account.extra_data or {})
    extra["openai_account_state"] = "forbidden_or_banned"
    extra["openai_account_state_reason"] = reason
    extra["openai_account_state_marked_at"] = datetime.utcnow().isoformat()
    for key in (
        "oauth_recovery_required",
        "oauth_recovery_required_reason",
        "oauth_recovery_required_marked_at",
        "openai_auth_state",
        "openai_auth_state_reason",
        "openai_auth_state_marked_at",
    ):
        extra.pop(key, None)
    crud.update_account(db, account.id, status="banned", extra_data=extra)


def _mark_account_forbidden_or_banned(db, account: Account, reason: str) -> None:
    extra = dict(account.extra_data or {})
    extra["openai_account_state"] = "forbidden_or_banned"
    extra["openai_account_state_reason"] = reason
    extra["openai_account_state_marked_at"] = datetime.utcnow().isoformat()
    for key in (
        "oauth_recovery_required",
        "oauth_recovery_required_reason",
        "oauth_recovery_required_marked_at",
        "openai_auth_state",
        "openai_auth_state_reason",
        "openai_auth_state_marked_at",
    ):
        extra.pop(key, None)
    crud.update_account(db, account.id, status="banned", extra_data=extra)


def _mark_oauth_recovery_required(db, account: Account, reason: str) -> None:
    extra = dict(account.extra_data or {})
    if account.status == "banned" or str(extra.get("openai_account_state") or "").strip().lower() in {
        "forbidden_or_banned",
        "deleted_or_deactivated",
    }:
        return
    extra["oauth_recovery_required"] = True
    extra["oauth_recovery_required_reason"] = reason
    extra["oauth_recovery_required_marked_at"] = datetime.utcnow().isoformat()
    crud.update_account(
        db,
        account.id,
        status="failed",
        extra_data=extra,
    )


def _mark_access_token_expired(db, account: Account, reason: str) -> None:
    extra = dict(account.extra_data or {})
    extra["token_validation_state"] = "access_token_invalid_or_expired"
    extra["token_validation_reason"] = reason
    extra["token_validation_marked_at"] = datetime.utcnow().isoformat()
    crud.update_account(db, account.id, status="expired", extra_data=extra)


def _clear_success_state(db, account: Account, *, clear_oauth_recovery: bool = False) -> None:
    extra = dict(account.extra_data or {})
    for key in (
        "token_validation_state",
        "token_validation_reason",
        "token_validation_marked_at",
    ):
        extra.pop(key, None)
    if clear_oauth_recovery:
        for key in (
            "oauth_recovery_required",
            "oauth_recovery_required_reason",
            "oauth_recovery_required_marked_at",
        ):
            extra.pop(key, None)

    # 验证/刷新成功时，清理会被成功结果推翻的暂态标记。
    # deleted_or_deactivated 这类硬状态不应在这里静默清理；
    # 但 forbidden_or_banned 常见于旧版本将 HTML challenge / 代理风控误判为封禁，
    # 一旦 token 再次验证成功，说明该标记已经失效，应立即移除，避免页面持续显示“疑似封禁”。
    openai_state = str(extra.get("openai_account_state") or "").strip().lower()
    if openai_state == "forbidden_or_banned":
        for key in (
            "openai_account_state",
            "openai_account_state_reason",
            "openai_account_state_marked_at",
        ):
            extra.pop(key, None)

    if str(extra.get("openai_auth_state") or "").strip().lower() == "mfa_required":
        for key in (
            "openai_auth_state",
            "openai_auth_state_reason",
            "openai_auth_state_marked_at",
        ):
            extra.pop(key, None)

    crud.update_account(db, account.id, status="active", extra_data=extra)


@dataclass
class TokenRefreshResult:
    """Token 刷新结果"""
    success: bool
    access_token: str = ""
    refresh_token: str = ""
    expires_at: Optional[datetime] = None
    error_message: str = ""


class TokenRefreshManager:
    """
    Token 刷新管理器
    支持两种刷新方式：
    1. Session Token 刷新（优先）
    2. OAuth Refresh Token 刷新
    """

    # OpenAI OAuth 端点
    SESSION_URL = "https://chatgpt.com/api/auth/session"
    TOKEN_URL = "https://auth.openai.com/oauth/token"

    def __init__(self, proxy_url: Optional[str] = None):
        """
        初始化 Token 刷新管理器

        Args:
            proxy_url: 代理 URL
        """
        self.proxy_url = proxy_url
        self.settings = get_settings()

    def _create_session(self) -> cffi_requests.Session:
        """创建 HTTP 会话"""
        session = cffi_requests.Session(impersonate="chrome120", proxy=self.proxy_url)
        return session

    def _parse_oauth_error(self, response: cffi_requests.Response) -> str:
        """解析 OAuth 错误信息"""
        body_text = (response.text or "").strip()
        error_message = _error_text(response)

        error_lower = error_message.lower()
        if "refresh token has already been used" in error_lower:
            return "OAuth refresh_token 已失效（一次性令牌已被使用），请重新登录该账号后再上传 CPA"
        if response.status_code == 401:
            if error_message:
                return f"OAuth token 刷新失败: {error_message}"
            else:
                return "OAuth token 刷新失败: refresh_token 无效或已过期，请重新登录账号"
        if error_message:
            return f"OAuth token 刷新失败: {error_message}"
        if body_text:
            return f"OAuth token 刷新失败: HTTP {response.status_code}, 响应: {body_text[:200]}"
        return f"OAuth token 刷新失败: HTTP {response.status_code}"

    def refresh_by_session_token(self, session_token: str) -> TokenRefreshResult:
        """
        使用 Session Token 刷新

        Args:
            session_token: 会话令牌

        Returns:
            TokenRefreshResult: 刷新结果
        """
        result = TokenRefreshResult(success=False)

        try:
            session = self._create_session()

            # 设置会话 Cookie
            session.cookies.set(
                "__Secure-next-auth.session-token",
                session_token,
                domain=".chatgpt.com",
                path="/"
            )

            # 请求会话端点
            response = session.get(
                self.SESSION_URL,
                headers={
                    "accept": "application/json",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
                timeout=30
            )

            if response.status_code != 200:
                result.error_message = f"Session token 刷新失败: HTTP {response.status_code}"
                logger.warning(result.error_message)
                return result

            data = response.json()

            # 提取 access_token
            access_token = data.get("accessToken")
            if not access_token:
                result.error_message = "Session token 刷新失败: 未找到 accessToken"
                logger.warning(result.error_message)
                return result

            # 提取过期时间
            expires_at = None
            expires_str = data.get("expires")
            if expires_str:
                try:
                    expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                except:
                    pass

            result.success = True
            result.access_token = access_token
            result.expires_at = expires_at

            logger.info(f"Session token 刷新成功，过期时间: {expires_at}")
            return result

        except Exception as e:
            result.error_message = f"Session token 刷新异常: {str(e)}"
            logger.error(result.error_message)
            return result

    def refresh_by_oauth_token(
        self,
        refresh_token: str,
        client_id: Optional[str] = None
    ) -> TokenRefreshResult:
        """
        使用 OAuth Refresh Token 刷新

        Args:
            refresh_token: OAuth 刷新令牌
            client_id: OAuth Client ID

        Returns:
            TokenRefreshResult: 刷新结果
        """
        result = TokenRefreshResult(success=False)

        try:
            session = self._create_session()

            # 使用配置的 client_id 或默认值
            client_id = client_id or self.settings.openai_client_id

            # 构建请求体
            token_data = {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "redirect_uri": self.settings.openai_redirect_uri
            }

            response = session.post(
                self.TOKEN_URL,
                headers={
                    "content-type": "application/x-www-form-urlencoded",
                    "accept": "application/json"
                },
                data=token_data,
                timeout=30
            )

            if response.status_code != 200:
                result.error_message = self._parse_oauth_error(response)
                logger.warning(f"{result.error_message}, 响应: {response.text[:200]}")
                return result

            data = response.json()

            # 提取令牌
            access_token = data.get("access_token")
            new_refresh_token = data.get("refresh_token", refresh_token)
            expires_in = data.get("expires_in", 3600)

            if not access_token:
                result.error_message = "OAuth token 刷新失败: 未找到 access_token"
                logger.warning(result.error_message)
                return result

            # 计算过期时间
            expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

            result.success = True
            result.access_token = access_token
            result.refresh_token = new_refresh_token
            result.expires_at = expires_at

            logger.info(f"OAuth token 刷新成功，过期时间: {expires_at}")
            return result

        except Exception as e:
            result.error_message = f"OAuth token 刷新异常: {str(e)}"
            logger.error(result.error_message)
            return result

    def refresh_account(self, account: Account) -> TokenRefreshResult:
        """
        刷新账号的 Token

        优先级：
        1. Session Token 刷新
        2. OAuth Refresh Token 刷新

        Args:
            account: 账号对象

        Returns:
            TokenRefreshResult: 刷新结果
        """
        # 优先尝试 Session Token
        if account.session_token:
            logger.info(f"尝试使用 Session Token 刷新账号 {account.email}")
            result = self.refresh_by_session_token(account.session_token)
            if result.success:
                return result
            logger.warning(f"Session Token 刷新失败，尝试 OAuth 刷新")

        # 尝试 OAuth Refresh Token
        if account.refresh_token:
            logger.info(f"尝试使用 OAuth Refresh Token 刷新账号 {account.email}")
            result = self.refresh_by_oauth_token(
                refresh_token=account.refresh_token,
                client_id=account.client_id
            )
            return result

        # 无可用刷新方式
        return TokenRefreshResult(
            success=False,
            error_message="账号没有可用的刷新方式（缺少 session_token 和 refresh_token）"
        )

    def validate_token(self, access_token: str) -> Tuple[bool, Optional[str]]:
        """
        验证 Access Token 是否有效

        Args:
            access_token: 访问令牌

        Returns:
            Tuple[bool, Optional[str]]: (是否有效, 错误信息)
        """
        try:
            session = self._create_session()

            # 调用 OpenAI API 验证 token
            response = session.get(
                "https://chatgpt.com/backend-api/me",
                headers={
                    "authorization": f"Bearer {access_token}",
                    "accept": "application/json"
                },
                timeout=30
            )

            if response.status_code == 200:
                return True, None
            elif response.status_code == 401:
                detail = _error_text(response)
                return False, f"Token 无效或已过期{': ' + detail if detail else ''}"
            elif response.status_code == 403:
                detail = _error_text(response)
                if _is_deleted_or_deactivated(detail):
                    return False, f"账号已删除或停用: {detail}"
                if _looks_like_html_challenge(detail):
                    return False, "验证受阻: HTTP 403 返回 HTML/挑战页，疑似代理/IP/Cloudflare 风控，并非账号封禁"
                return False, f"账号可能被封禁{': ' + detail if detail else ''}"
            else:
                detail = _error_text(response)
                return False, f"验证失败: HTTP {response.status_code}{', ' + detail if detail else ''}"

        except Exception as e:
            return False, f"验证异常: {str(e)}"


def refresh_account_token(account_id: int, proxy_url: Optional[str] = None) -> TokenRefreshResult:
    """
    刷新指定账号的 Token 并更新数据库

    Args:
        account_id: 账号 ID
        proxy_url: 代理 URL

    Returns:
        TokenRefreshResult: 刷新结果
    """
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            return TokenRefreshResult(success=False, error_message="账号不存在")

        manager = TokenRefreshManager(proxy_url=proxy_url)
        result = manager.refresh_account(account)

        if result.success:
            # 更新数据库
            update_data = {
                "access_token": result.access_token,
                "last_refresh": datetime.utcnow()
            }

            if result.refresh_token:
                update_data["refresh_token"] = result.refresh_token

            if result.expires_at:
                update_data["expires_at"] = result.expires_at

            extra = dict(account.extra_data or {})
            for key in (
                "oauth_recovery_required",
                "oauth_recovery_required_reason",
                "oauth_recovery_required_marked_at",
                "token_validation_state",
                "token_validation_reason",
                "token_validation_marked_at",
            ):
                extra.pop(key, None)
            if str(extra.get("openai_account_state") or "").strip().lower() == "forbidden_or_banned":
                for key in (
                    "openai_account_state",
                    "openai_account_state_reason",
                    "openai_account_state_marked_at",
                ):
                    extra.pop(key, None)
            if str(extra.get("openai_auth_state") or "").strip().lower() == "mfa_required":
                for key in (
                    "openai_auth_state",
                    "openai_auth_state_reason",
                    "openai_auth_state_marked_at",
                ):
                    extra.pop(key, None)
            update_data["extra_data"] = extra
            update_data["status"] = "active"
            crud.update_account(db, account_id, **update_data)
        else:
            if _is_deleted_or_deactivated(result.error_message):
                _mark_account_deleted_or_deactivated(db, account, result.error_message)
            elif _is_forbidden_or_banned(result.error_message):
                _mark_account_forbidden_or_banned(db, account, result.error_message)
            elif _should_mark_oauth_recovery_required(result.error_message):
                _mark_oauth_recovery_required(db, account, result.error_message)

        return result


def validate_account_token(account_id: int, proxy_url: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    验证指定账号的 Token 是否有效

    Args:
        account_id: 账号 ID
        proxy_url: 代理 URL

    Returns:
        Tuple[bool, Optional[str]]: (是否有效, 错误信息)
    """
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            return False, "账号不存在"

        if not account.access_token:
            _mark_oauth_recovery_required(db, account, "账号没有 access_token，需要补录 OAuth")
            return False, "账号没有 access_token"

        manager = TokenRefreshManager(proxy_url=proxy_url)
        is_valid, error = manager.validate_token(account.access_token)
        if is_valid:
            _clear_success_state(db, account, clear_oauth_recovery=False)
            return True, None

        error = error or "Token 验证失败"
        if _is_deleted_or_deactivated(error):
            _mark_account_deleted_or_deactivated(db, account, error)
        elif _is_forbidden_or_banned(error):
            _mark_account_forbidden_or_banned(db, account, error)
        elif "Token 无效或已过期" in error or "HTTP 401" in error:
            _mark_access_token_expired(db, account, error)
        elif "账号没有 access_token" in error:
            _mark_oauth_recovery_required(db, account, error)

        return False, error
