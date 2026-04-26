"""
账号管理 API 路由
"""
import asyncio
import csv
import io
import json
import logging
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Body, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_

from ...config.constants import AccountStatus
from ...config.settings import get_settings
from ...core.dynamic_proxy import fetch_dynamic_proxy
from ...core.openai.token_refresh import refresh_account_token as do_refresh
from ...core.openai.token_refresh import validate_account_token as do_validate
from ...core.register import RegistrationEngine
from ...core.upload.cpa_upload import generate_token_json, batch_upload_to_cpa, upload_to_cpa
from ...core.upload.team_manager_upload import upload_to_team_manager, batch_upload_to_team_manager
from ...core.upload.sub2api_upload import batch_upload_to_sub2api, upload_to_sub2api

from ...core.dynamic_proxy import get_proxy_url_for_task
from ...database import crud
from ...database.models import Account, EmailService as EmailServiceModel, RegistrationTask
from ...database.session import get_db
from ...services import EmailServiceFactory, EmailServiceType
from ..task_manager import task_manager

logger = logging.getLogger(__name__)
router = APIRouter()
recovery_batches: Dict[str, dict] = {}
refresh_batches: Dict[str, dict] = {}


def _extra_data_flag_filter(column, key: str, value: str):
    """兼容 JSON 序列化时有无空格的筛选条件。"""
    return or_(
        column.like(f'%"{key}":"{value}"%'),
        column.like(f'%"{key}": "{value}"%'),
    )


def _extra_data_bool_filter(column, key: str, value: bool):
    literal = "true" if value else "false"
    return or_(
        column.like(f'%"{key}":{literal}%'),
        column.like(f'%"{key}": {literal}%'),
    )


def _get_proxy(request_proxy: Optional[str] = None, purpose: str = "general") -> Optional[str]:
    """获取代理 URL。

    优先级：请求指定代理 >（按操作开关判断）代理池 > 动态代理 > 静态代理。
    purpose 支持: general / refresh / validate
    """
    if request_proxy:
        return request_proxy

    settings = get_settings()
    if purpose == "refresh" and not bool(getattr(settings, "proxy_refresh_use_proxy", False)):
        return None
    if purpose == "validate" and not bool(getattr(settings, "proxy_validate_use_proxy", False)):
        return None

    with get_db() as db:
        proxy = crud.get_random_proxy(db)
        if proxy:
            return proxy.proxy_url
    proxy_url = get_proxy_url_for_task()
    if proxy_url:
        return proxy_url
    return settings.proxy_url


def _detect_deleted_or_deactivated_account(engine) -> Optional[str]:
    """识别 OpenAI 返回的账号已删除/停用错误。"""
    candidates = [
        str(getattr(engine, "_last_otp_error_message", "") or ""),
        str(getattr(engine, "_last_otp_error_code", "") or ""),
    ]
    logs = getattr(engine, "logs", None)
    if isinstance(logs, list):
        candidates.extend(str(item or "") for item in logs[-20:])
    text = "\n".join(candidates).lower()
    markers = [
        "deleted or deactivated",
        "do not have an account because it has been deleted or deactivated",
    ]
    if any(marker in text for marker in markers):
        return "OpenAI 返回账号已被删除或停用"
    return None


def _mark_account_deleted_or_deactivated(db, account: Account, reason: str, proxy_used: Optional[str] = None) -> None:
    extra = dict(account.extra_data or {})
    extra["openai_account_state"] = "deleted_or_deactivated"
    extra["openai_account_state_reason"] = reason
    extra["openai_account_state_marked_at"] = datetime.utcnow().isoformat()
    crud.update_account(
        db,
        account.id,
        status=AccountStatus.BANNED.value,
        proxy_used=proxy_used or account.proxy_used,
        extra_data=extra,
    )


def _detect_mfa_required_account(engine) -> Optional[str]:
    error_message = str(getattr(engine, "_last_mfa_error_message", "") or "").strip()
    if error_message:
        low = error_message.lower()
        if "未配置 mfa 密钥" in low or "未配置 mfa" in low:
            return "OAuth 补录遇到 MFA challenge，且当前账号未配置 MFA 密钥"
        return f"OAuth 补录遇到 MFA challenge：{error_message}"

    logs = getattr(engine, "logs", None)
    if isinstance(logs, list):
        recent = "\n".join(str(item or "") for item in logs[-20:]).lower()
        if "mfa 自动验证失败" in recent or "mfa 密钥不可用" in recent:
            return "OAuth 补录遇到 MFA challenge，需要 MFA 二次验证"

    return None


def _mark_account_mfa_required(db, account: Account, reason: str, proxy_used: Optional[str] = None) -> None:
    extra = dict(account.extra_data or {})
    extra["openai_auth_state"] = "mfa_required"
    extra["openai_auth_state_reason"] = reason
    extra["openai_auth_state_marked_at"] = datetime.utcnow().isoformat()
    crud.update_account(
        db,
        account.id,
        status=AccountStatus.FAILED.value,
        proxy_used=proxy_used or account.proxy_used,
        extra_data=extra,
    )


# ============== Pydantic Models ==============

class AccountResponse(BaseModel):
    """账号响应模型"""
    id: int
    email: str
    password: Optional[str] = None
    client_id: Optional[str] = None
    email_service: str
    account_id: Optional[str] = None
    workspace_id: Optional[str] = None
    registered_at: Optional[str] = None
    last_refresh: Optional[str] = None
    expires_at: Optional[str] = None
    has_tokens: bool = False
    status: str
    proxy_used: Optional[str] = None
    cpa_uploaded: bool = False
    cpa_uploaded_at: Optional[str] = None
    cookies: Optional[str] = None
    extra_data: Optional[dict] = None
    mfa_secret: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class AccountListResponse(BaseModel):
    """账号列表响应"""
    total: int
    accounts: List[AccountResponse]


class AccountUpdateRequest(BaseModel):
    """账号更新请求"""
    status: Optional[str] = None
    metadata: Optional[dict] = None
    cookies: Optional[str] = None  # 完整 cookie 字符串，用于支付请求
    mfa_secret: Optional[str] = None  # MFA TOTP 密钥；空字符串表示清空


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""
    ids: List[int] = []
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None


class BatchUpdateRequest(BaseModel):
    """批量更新请求"""
    ids: List[int]
    status: str


# ============== Helper Functions ==============

def resolve_account_ids(
    db,
    ids: List[int],
    select_all: bool = False,
    status_filter: Optional[str] = None,
    email_service_filter: Optional[str] = None,
    search_filter: Optional[str] = None,
) -> List[int]:
    """当 select_all=True 时查询全部符合条件的 ID，否则直接返回传入的 ids"""
    if not select_all:
        return ids
    query = db.query(Account.id)
    if status_filter:
        if status_filter == "deleted_or_deactivated":
            query = query.filter(
                Account.extra_data.is_not(None),
                _extra_data_flag_filter(Account.extra_data, "openai_account_state", "deleted_or_deactivated")
            )
        elif status_filter == "oauth_recovery_required":
            query = query.filter(
                Account.extra_data.is_not(None),
                _extra_data_bool_filter(Account.extra_data, "oauth_recovery_required", True)
            )
        elif status_filter == "mfa_required":
            query = query.filter(
                Account.extra_data.is_not(None),
                _extra_data_flag_filter(Account.extra_data, "openai_auth_state", "mfa_required")
            )
        elif status_filter == "failed":
            query = query.filter(Account.status == status_filter)
            query = query.filter(
                ~_extra_data_bool_filter(Account.extra_data, "oauth_recovery_required", True),
                ~_extra_data_flag_filter(Account.extra_data, "openai_auth_state", "mfa_required"),
                ~_extra_data_flag_filter(Account.extra_data, "openai_account_state", "deleted_or_deactivated"),
            )
        else:
            query = query.filter(Account.status == status_filter)
    if email_service_filter:
        query = query.filter(Account.email_service == email_service_filter)
    if search_filter:
        pattern = f"%{search_filter}%"
        query = query.filter(
            (Account.email.ilike(pattern)) | (Account.account_id.ilike(pattern))
        )
    return [row[0] for row in query.all()]


def account_to_response(account: Account, include_mfa_secret: bool = False) -> AccountResponse:
    """转换 Account 模型为响应模型"""
    extra_data = dict(account.extra_data or {})
    mfa_secret = str(extra_data.get("mfa_totp_secret") or "").strip()
    response_extra = dict(extra_data)
    response_extra.pop("mfa_totp_secret", None)
    return AccountResponse(
        id=account.id,
        email=account.email,
        password=account.password,
        client_id=account.client_id,
        email_service=account.email_service,
        account_id=account.account_id,
        workspace_id=account.workspace_id,
        registered_at=account.registered_at.isoformat() if account.registered_at else None,
        last_refresh=account.last_refresh.isoformat() if account.last_refresh else None,
        expires_at=account.expires_at.isoformat() if account.expires_at else None,
        has_tokens=bool(account.access_token and account.refresh_token),
        status=account.status,
        proxy_used=account.proxy_used,
        cpa_uploaded=account.cpa_uploaded or False,
        cpa_uploaded_at=account.cpa_uploaded_at.isoformat() if account.cpa_uploaded_at else None,
        cookies=account.cookies,
        extra_data=response_extra,
        mfa_secret=mfa_secret if include_mfa_secret else None,
        created_at=account.created_at.isoformat() if account.created_at else None,
        updated_at=account.updated_at.isoformat() if account.updated_at else None,
    )


# ============== API Endpoints ==============

@router.get("", response_model=AccountListResponse)
async def list_accounts(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    status: Optional[str] = Query(None, description="状态筛选"),
    email_service: Optional[str] = Query(None, description="邮箱服务筛选"),
    token_status: Optional[str] = Query(None, description="Token 筛选"),
    search: Optional[str] = Query(None, description="搜索关键词"),
):
    """
    获取账号列表

    支持分页、状态筛选、邮箱服务筛选和搜索
    """
    with get_db() as db:
        # 构建查询
        query = db.query(Account)

        # 状态筛选
        if status:
            if status == "deleted_or_deactivated":
                query = query.filter(
                    Account.extra_data.is_not(None),
                    _extra_data_flag_filter(Account.extra_data, "openai_account_state", "deleted_or_deactivated")
                )
            elif status == "oauth_recovery_required":
                query = query.filter(
                    Account.extra_data.is_not(None),
                    _extra_data_bool_filter(Account.extra_data, "oauth_recovery_required", True)
                )
            elif status == "mfa_required":
                query = query.filter(
                    Account.extra_data.is_not(None),
                    _extra_data_flag_filter(Account.extra_data, "openai_auth_state", "mfa_required")
                )
            elif status == "failed":
                query = query.filter(Account.status == status)
                query = query.filter(
                    ~_extra_data_bool_filter(Account.extra_data, "oauth_recovery_required", True),
                    ~_extra_data_flag_filter(Account.extra_data, "openai_auth_state", "mfa_required"),
                    ~_extra_data_flag_filter(Account.extra_data, "openai_account_state", "deleted_or_deactivated"),
                )
            else:
                query = query.filter(Account.status == status)

        # 邮箱服务筛选
        if email_service:
            query = query.filter(Account.email_service == email_service)

        # Token 状态筛选
        if token_status == "missing":
            query = query.filter(
                (Account.access_token.is_(None)) | (Account.access_token == "") |
                (Account.refresh_token.is_(None)) | (Account.refresh_token == "")
            )
        elif token_status == "ok":
            query = query.filter(
                Account.access_token.is_not(None),
                Account.access_token != "",
                Account.refresh_token.is_not(None),
                Account.refresh_token != "",
                Account.status != "expired"
            )
        elif token_status == "expired":
            query = query.filter(Account.status == "expired")

        # 搜索
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Account.email.ilike(search_pattern)) |
                (Account.account_id.ilike(search_pattern))
            )

        # 统计总数
        total = query.count()

        # 分页
        offset = (page - 1) * page_size
        accounts = query.order_by(Account.created_at.desc()).offset(offset).limit(page_size).all()

        return AccountListResponse(
            total=total,
            accounts=[account_to_response(acc) for acc in accounts]
        )


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(account_id: int):
    """获取单个账号详情"""
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        return account_to_response(account, include_mfa_secret=True)


@router.get("/{account_id}/tokens")
async def get_account_tokens(account_id: int):
    """获取账号的 Token 信息"""
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        return {
            "id": account.id,
            "email": account.email,
            "access_token": account.access_token,
            "refresh_token": account.refresh_token,
            "id_token": account.id_token,
            "has_tokens": bool(account.access_token and account.refresh_token),
        }


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(account_id: int, request: AccountUpdateRequest):
    """更新账号状态"""
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        update_data = {}
        if request.status:
            if request.status not in [e.value for e in AccountStatus]:
                raise HTTPException(status_code=400, detail="无效的状态值")
            update_data["status"] = request.status

        extra = dict(account.extra_data or {})
        if request.metadata:
            extra.update(request.metadata)
        if request.mfa_secret is not None:
            secret = str(request.mfa_secret or "").strip()
            if secret:
                extra["mfa_totp_secret"] = secret
            else:
                extra.pop("mfa_totp_secret", None)
        if request.metadata is not None or request.mfa_secret is not None:
            update_data["extra_data"] = extra

        if request.cookies is not None:
            # 留空则清空，非空则更新
            update_data["cookies"] = request.cookies or None

        account = crud.update_account(db, account_id, **update_data)
        return account_to_response(account, include_mfa_secret=True)


@router.get("/{account_id}/cookies")
async def get_account_cookies(account_id: int):
    """获取账号的 cookie 字符串（仅供支付使用）"""
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        return {"account_id": account_id, "cookies": account.cookies or ""}


@router.delete("/{account_id}")
async def delete_account(account_id: int):
    """删除单个账号"""
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        crud.delete_account(db, account_id)
        return {"success": True, "message": f"账号 {account.email} 已删除"}


@router.post("/batch-delete")
async def batch_delete_accounts(request: BatchDeleteRequest):
    """批量删除账号"""
    with get_db() as db:
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )
        deleted_count = 0
        errors = []

        for account_id in ids:
            try:
                account = crud.get_account_by_id(db, account_id)
                if account:
                    crud.delete_account(db, account_id)
                    deleted_count += 1
            except Exception as e:
                errors.append(f"ID {account_id}: {str(e)}")

        return {
            "success": True,
            "deleted_count": deleted_count,
            "errors": errors if errors else None
        }


@router.post("/batch-update")
async def batch_update_accounts(request: BatchUpdateRequest):
    """批量更新账号状态"""
    if request.status not in [e.value for e in AccountStatus]:
        raise HTTPException(status_code=400, detail="无效的状态值")

    with get_db() as db:
        updated_count = 0
        errors = []

        for account_id in request.ids:
            try:
                account = crud.get_account_by_id(db, account_id)
                if account:
                    crud.update_account(db, account_id, status=request.status)
                    updated_count += 1
            except Exception as e:
                errors.append(f"ID {account_id}: {str(e)}")

        return {
            "success": True,
            "updated_count": updated_count,
            "errors": errors if errors else None
        }


class BatchExportRequest(BaseModel):
    """批量导出请求"""
    ids: List[int] = []
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None


def _parse_optional_datetime(value: Optional[str]) -> Optional[datetime]:
    """解析可选时间字段。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError(f"无效的时间格式: {value}")


def _normalize_import_header(header: str) -> str:
    return str(header or "").strip().lower().replace("_", " ").replace("-", " ")


ACCOUNT_IMPORT_FIELD_ALIASES = {
    "email": {"email", "邮箱"},
    "password": {"password", "密码"},
    "client_id": {"client id", "client_id"},
    "account_id": {"account id", "account_id"},
    "workspace_id": {"workspace id", "workspace_id"},
    "access_token": {"access token", "access_token"},
    "refresh_token": {"refresh token", "refresh_token"},
    "id_token": {"id token", "id_token"},
    "session_token": {"session token", "session_token"},
    "email_service": {"email service", "email_service"},
    "email_service_id": {"email service id", "email_service_id"},
    "proxy_used": {"proxy used", "proxy_used"},
    "status": {"status", "状态"},
    "registered_at": {"registered at", "registered_at"},
    "last_refresh": {"last refresh", "last_refresh"},
    "expires_at": {"expires at", "expires_at"},
    "cookies": {"cookies", "cookie"},
    "source": {"source", "来源"},
    "subscription_type": {"subscription type", "subscription_type"},
    "subscription_at": {"subscription at", "subscription_at"},
}


def _normalize_account_import_record(record: Dict[str, object]) -> Dict[str, object]:
    """标准化导入账号记录。"""
    normalized: Dict[str, object] = {}
    for raw_key, raw_value in (record or {}).items():
        header = _normalize_import_header(str(raw_key))
        target_key = None
        for candidate, aliases in ACCOUNT_IMPORT_FIELD_ALIASES.items():
            if header in aliases:
                target_key = candidate
                break
        if not target_key:
            continue
        normalized[target_key] = raw_value

    email = str(normalized.get("email") or "").strip()
    if not email:
        raise ValueError("缺少邮箱字段")

    parsed = {
        "email": email,
        "password": str(normalized.get("password") or "").strip() or None,
        "client_id": str(normalized.get("client_id") or "").strip() or None,
        "account_id": str(normalized.get("account_id") or "").strip() or None,
        "workspace_id": str(normalized.get("workspace_id") or "").strip() or None,
        "access_token": str(normalized.get("access_token") or "").strip() or None,
        "refresh_token": str(normalized.get("refresh_token") or "").strip() or None,
        "id_token": str(normalized.get("id_token") or "").strip() or None,
        "session_token": str(normalized.get("session_token") or "").strip() or None,
        "email_service": str(normalized.get("email_service") or "").strip() or None,
        "email_service_id": str(normalized.get("email_service_id") or "").strip() or None,
        "proxy_used": str(normalized.get("proxy_used") or "").strip() or None,
        "status": str(normalized.get("status") or "").strip() or None,
        "cookies": str(normalized.get("cookies") or "").strip() or None,
        "source": str(normalized.get("source") or "").strip() or None,
        "subscription_type": str(normalized.get("subscription_type") or "").strip() or None,
        "registered_at": _parse_optional_datetime(normalized.get("registered_at")),
        "last_refresh": _parse_optional_datetime(normalized.get("last_refresh")),
        "expires_at": _parse_optional_datetime(normalized.get("expires_at")),
        "subscription_at": _parse_optional_datetime(normalized.get("subscription_at")),
    }
    return parsed


def _upsert_imported_accounts(db, records: List[Dict[str, object]]) -> Dict[str, object]:
    """按邮箱导入账号，存在则更新，不存在则创建。"""
    created = 0
    updated = 0
    errors: List[str] = []

    create_keys = {
        "password",
        "client_id",
        "session_token",
        "email_service_id",
        "account_id",
        "workspace_id",
        "access_token",
        "refresh_token",
        "id_token",
        "proxy_used",
        "expires_at",
        "status",
        "source",
    }

    for index, raw_record in enumerate(records, start=1):
        try:
            record = _normalize_account_import_record(raw_record)
            email = record.pop("email")
            existing = crud.get_account_by_email(db, email)

            if existing:
                if not record.get("email_service"):
                    record["email_service"] = existing.email_service
                crud.update_account(db, existing.id, **record)
                updated += 1
                continue

            email_service = record.get("email_service")
            if not email_service:
                raise ValueError("新账号缺少 email_service 字段")

            create_kwargs = {key: record.get(key) for key in create_keys}
            create_kwargs["email_service"] = email_service
            db_account = crud.create_account(db, email=email, **create_kwargs)

            extra_updates = {
                key: value
                for key, value in record.items()
                if key not in create_keys and key != "email_service"
            }
            if extra_updates:
                crud.update_account(db, db_account.id, **extra_updates)
            created += 1
        except Exception as e:
            errors.append(f"第 {index} 条记录导入失败: {e}")

    return {
        "success": len(errors) == 0,
        "created_count": created,
        "updated_count": updated,
        "failed_count": len(errors),
        "errors": errors,
    }


@router.post("/import")
async def import_accounts(file: UploadFile = File(...)):
    """导入账号，支持当前导出的 JSON / CSV 格式。"""
    suffix = Path(file.filename or "").suffix.lower()
    raw_content = await file.read()
    if not raw_content:
        raise HTTPException(status_code=400, detail="导入文件为空")

    try:
        if suffix == ".json":
            payload = json.loads(raw_content.decode("utf-8-sig"))
            if isinstance(payload, dict):
                records = payload.get("accounts")
                if records is None:
                    raise ValueError("JSON 文件缺少 accounts 数组")
            elif isinstance(payload, list):
                records = payload
            else:
                raise ValueError("JSON 文件格式不正确")
        elif suffix == ".csv":
            text = raw_content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            records = list(reader)
        else:
            raise ValueError("仅支持导入 .json 或 .csv 文件")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析导入文件失败: {e}")

    if not records:
        raise HTTPException(status_code=400, detail="导入文件中没有可用记录")

    with get_db() as db:
        result = _upsert_imported_accounts(db, records)
    return result


@router.post("/export/json")
async def export_accounts_json(request: BatchExportRequest):
    """导出账号为 JSON 格式"""
    with get_db() as db:
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )
        accounts = db.query(Account).filter(Account.id.in_(ids)).all()

        export_data = []
        for acc in accounts:
            export_data.append({
                "email": acc.email,
                "password": acc.password,
                "client_id": acc.client_id,
                "account_id": acc.account_id,
                "workspace_id": acc.workspace_id,
                "access_token": acc.access_token,
                "refresh_token": acc.refresh_token,
                "id_token": acc.id_token,
                "session_token": acc.session_token,
                "email_service": acc.email_service,
                "registered_at": acc.registered_at.isoformat() if acc.registered_at else None,
                "last_refresh": acc.last_refresh.isoformat() if acc.last_refresh else None,
                "expires_at": acc.expires_at.isoformat() if acc.expires_at else None,
                "status": acc.status,
            })

        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"accounts_{timestamp}.json"

        # 返回 JSON 响应
        content = json.dumps(export_data, ensure_ascii=False, indent=2)

        return StreamingResponse(
            iter([content]),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )


@router.post("/export/csv")
async def export_accounts_csv(request: BatchExportRequest):
    """导出账号为 CSV 格式"""
    import csv
    import io

    with get_db() as db:
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )
        accounts = db.query(Account).filter(Account.id.in_(ids)).all()

        # 创建 CSV 内容
        output = io.StringIO()
        writer = csv.writer(output)

        # 写入表头
        writer.writerow([
            "ID", "Email", "Password", "Client ID",
            "Account ID", "Workspace ID",
            "Access Token", "Refresh Token", "ID Token", "Session Token",
            "Email Service", "Status", "Registered At", "Last Refresh", "Expires At"
        ])

        # 写入数据
        for acc in accounts:
            writer.writerow([
                acc.id,
                acc.email,
                acc.password or "",
                acc.client_id or "",
                acc.account_id or "",
                acc.workspace_id or "",
                acc.access_token or "",
                acc.refresh_token or "",
                acc.id_token or "",
                acc.session_token or "",
                acc.email_service,
                acc.status,
                acc.registered_at.isoformat() if acc.registered_at else "",
                acc.last_refresh.isoformat() if acc.last_refresh else "",
                acc.expires_at.isoformat() if acc.expires_at else ""
            ])

        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"accounts_{timestamp}.csv"

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )


@router.post("/export/sub2api")
async def export_accounts_sub2api(request: BatchExportRequest):
    """导出账号为 Sub2Api 格式（所有选中账号合并到一个 JSON 的 accounts 数组中）"""

    def make_account_entry(acc) -> dict:
        expires_at = int(acc.expires_at.timestamp()) if acc.expires_at else 0
        return {
            "name": acc.email,
            "platform": "openai",
            "type": "oauth",
            "credentials": {
                "access_token": acc.access_token or "",
                "chatgpt_account_id": acc.account_id or "",
                "chatgpt_user_id": "",
                "client_id": acc.client_id or "",
                "expires_at": expires_at,
                "expires_in": 863999,
                "model_mapping": {
                    "gpt-5.1": "gpt-5.1",
                    "gpt-5.1-codex": "gpt-5.1-codex",
                    "gpt-5.1-codex-max": "gpt-5.1-codex-max",
                    "gpt-5.1-codex-mini": "gpt-5.1-codex-mini",
                    "gpt-5.2": "gpt-5.2",
                    "gpt-5.2-codex": "gpt-5.2-codex",
                    "gpt-5.3": "gpt-5.3",
                    "gpt-5.3-codex": "gpt-5.3-codex",
                    "gpt-5.4": "gpt-5.4"
                },
                "organization_id": acc.workspace_id or "",
                "refresh_token": acc.refresh_token or ""
            },
            "extra": {},
            "concurrency": 10,
            "priority": 1,
            "rate_multiplier": 1,
            "auto_pause_on_expired": True
        }

    with get_db() as db:
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )
        accounts = db.query(Account).filter(Account.id.in_(ids)).all()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        payload = {
            "proxies": [],
            "accounts": [make_account_entry(acc) for acc in accounts]
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2)

        if len(accounts) == 1:
            filename = f"{accounts[0].email}_sub2api.json"
        else:
            filename = f"sub2api_tokens_{timestamp}.json"

        return StreamingResponse(
            iter([content]),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )


@router.post("/export/cpa")
async def export_accounts_cpa(request: BatchExportRequest):
    """导出账号为 CPA Token JSON 格式（每个账号单独一个 JSON 文件，打包为 ZIP）"""
    with get_db() as db:
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )
        accounts = db.query(Account).filter(Account.id.in_(ids)).all()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if len(accounts) == 1:
            # 单个账号直接返回 JSON 文件
            acc = accounts[0]
            token_data = generate_token_json(acc)
            content = json.dumps(token_data, ensure_ascii=False, indent=2)
            filename = f"{acc.email}.json"
            return StreamingResponse(
                iter([content]),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

        # 多个账号打包为 ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for acc in accounts:
                token_data = generate_token_json(acc)
                content = json.dumps(token_data, ensure_ascii=False, indent=2)
                zf.writestr(f"{acc.email}.json", content)

        zip_buffer.seek(0)
        zip_filename = f"cpa_tokens_{timestamp}.zip"
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={zip_filename}"}
        )


@router.get("/stats/summary")
async def get_accounts_stats():
    """获取账号统计信息"""
    with get_db() as db:
        from sqlalchemy import func

        # 总数
        total = db.query(func.count(Account.id)).scalar()

        # 按状态统计
        status_stats = db.query(
            Account.status,
            func.count(Account.id)
        ).group_by(Account.status).all()

        # 按邮箱服务统计
        service_stats = db.query(
            Account.email_service,
            func.count(Account.id)
        ).group_by(Account.email_service).all()

        return {
            "total": total,
            "by_status": {status: count for status, count in status_stats},
            "by_email_service": {service: count for service, count in service_stats}
        }


# ============== Token 刷新相关 ==============

class TokenRefreshRequest(BaseModel):
    """Token 刷新请求"""
    proxy: Optional[str] = None


class OAuthRecoveryRequest(BaseModel):
    """补录 OAuth 请求"""
    proxy: Optional[str] = None


class BatchOAuthRecoveryRequest(BaseModel):
    """批量补录 OAuth 请求"""
    ids: List[int] = []
    proxy: Optional[str] = None
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None


class BatchRefreshRequest(BaseModel):
    """批量刷新请求"""
    ids: List[int] = []
    proxy: Optional[str] = None
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None


class TokenValidateRequest(BaseModel):
    """Token 验证请求"""
    proxy: Optional[str] = None


class BatchValidateRequest(BaseModel):
    """批量验证请求"""
    ids: List[int] = []
    proxy: Optional[str] = None
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None


def _get_recovery_proxy(request_proxy: Optional[str] = None, log_callback=None) -> Optional[str]:
    """补录 OAuth 的代理选择：手动代理 > 动态代理 > 代理池/静态配置。"""
    if request_proxy:
        if log_callback:
            log_callback(f"[系统] 使用请求指定代理: {request_proxy}")
        return request_proxy

    settings = get_settings()
    if settings.proxy_dynamic_enabled and settings.proxy_dynamic_api_url:
        if log_callback:
            log_callback("[系统] 检测到已启用动态代理，开始获取动态 IP...")

        api_key = settings.proxy_dynamic_api_key.get_secret_value() if settings.proxy_dynamic_api_key else ""
        proxy_url = fetch_dynamic_proxy(
            api_url=settings.proxy_dynamic_api_url,
            api_key=api_key,
            api_key_header=settings.proxy_dynamic_api_key_header,
            result_field=settings.proxy_dynamic_result_field,
        )
        if proxy_url:
            if log_callback:
                log_callback(f"[系统] 动态代理可用，将使用: {proxy_url}")
            return proxy_url

        if log_callback:
            log_callback("[系统] 动态代理不可用，回退到代理池/静态代理")

    fallback_proxy = _get_proxy(None)
    if fallback_proxy and log_callback:
        log_callback(f"[系统] 使用回退代理: {fallback_proxy}")
    elif log_callback:
        log_callback("[系统] 当前未配置可用代理，将直连补录")
    return fallback_proxy


def _is_proxy_connect_aborted(error: Exception | str) -> bool:
    text = str(error or "").lower()
    return "proxy connect aborted" in text or "curl: (56)" in text and "connect aborted" in text


def _is_retryable_oauth_network_failure(text: Exception | str) -> bool:
    body = str(text or "").lower()
    return any(marker in body for marker in (
        "proxy connect aborted",
        "curl: (56)",
        "curl: (35)",
        "tls connect error",
        "token exchange failed: network error",
        "operation timed out",
        "connection reset",
    ))


def _has_available_proxy_source(request_proxy: Optional[str] = None) -> bool:
    if request_proxy:
        return True
    settings = get_settings()
    if settings.proxy_dynamic_enabled and settings.proxy_dynamic_api_url:
        return True
    if settings.proxy_url:
        return True
    try:
        with get_db() as db:
            proxy = crud.get_random_proxy(db)
            if proxy and proxy.proxy_url:
                return True
    except Exception:
        pass
    return False


def _find_mailbox_service_for_account(db, account: Account) -> Optional[EmailServiceModel]:
    """根据账号邮箱匹配可用于补录的邮箱服务配置。"""
    supported_types = {"outlook", "imap_mail", "tempmail", "temp_mail", "freemail", "moe_mail"}
    if account.email_service not in supported_types:
        return None

    services = (
        db.query(EmailServiceModel)
        .filter(
            EmailServiceModel.service_type == account.email_service,
            EmailServiceModel.enabled == True,
        )
        .order_by(
            EmailServiceModel.priority.asc(),
            EmailServiceModel.last_used.desc(),
            EmailServiceModel.id.asc(),
        )
        .all()
    )
    if not services:
        return None

    email_lower = (account.email or "").strip().lower()
    email_domain = email_lower.split("@", 1)[1] if "@" in email_lower else ""

    for service in services:
        config = service.config or {}
        if account.email_service == "outlook":
            accounts = config.get("accounts") or []
            for item in accounts:
                if str((item or {}).get("email") or "").strip().lower() == email_lower:
                    return service
            if str(config.get("email") or "").strip().lower() == email_lower:
                return service
        elif account.email_service == "imap_mail":
            if str(config.get("email") or "").strip().lower() == email_lower:
                return service
        elif account.email_service == "temp_mail":
            if str(config.get("domain") or "").strip().lower() == email_domain:
                return service
        elif account.email_service == "freemail":
            if str(config.get("domain") or "").strip().lower() == email_domain:
                return service
        elif account.email_service == "moe_mail":
            if str(config.get("default_domain") or config.get("domain") or "").strip().lower() == email_domain:
                return service

    return None


def _prepare_recovery_email_info(
    db,
    account: Account,
    email_service,
    log_callback=None,
) -> Optional[Dict[str, str]]:
    """为补录/查码准备邮箱凭据标识。"""
    email_info = None
    if account.email_service_id:
        email_info = {"service_id": account.email_service_id, "email": account.email}

    if account.email_service != "moe_mail":
        return email_info

    email_value = (account.email or "").strip()
    if "@" not in email_value:
        raise ValueError("MoeMail 账号邮箱格式无效，无法重建临时邮箱")

    if log_callback:
        log_callback(f"[系统] MoeMail 邮箱为临时资源，正在确保邮箱存在: {email_value}")

    if not hasattr(email_service, "ensure_mailbox"):
        raise ValueError("当前 MoeMail 服务版本不支持自动恢复邮箱")

    rebuilt_info = email_service.ensure_mailbox(email_value, account.email_service_id)
    rebuilt_email = str((rebuilt_info or {}).get("email") or "").strip()
    rebuilt_service_id = str((rebuilt_info or {}).get("service_id") or "").strip()

    if not rebuilt_email or not rebuilt_service_id:
        raise ValueError("MoeMail 重建邮箱失败：未返回有效的邮箱地址或 service_id")
    if rebuilt_email.lower() != email_value.lower():
        raise ValueError(f"MoeMail 重建邮箱返回地址不匹配：{rebuilt_email}")

    crud.update_account(db, account.id, email_service_id=rebuilt_service_id)
    account.email_service_id = rebuilt_service_id

    rebuilt_email_info = {"service_id": rebuilt_service_id, "email": rebuilt_email}
    if log_callback:
        log_callback(f"[系统] MoeMail 邮箱已重建，新的凭据标识: {rebuilt_service_id}")
    return rebuilt_email_info


def _run_sync_recover_oauth_task(
    task_uuid: str,
    account_id: int,
    proxy: Optional[str],
    log_prefix: str = "",
    batch_id: str = "",
):
    """在线程池里执行 OAuth 补录。"""
    callback = task_manager.create_log_callback(task_uuid, prefix=log_prefix, batch_id=batch_id)
    full_prefix = f"{log_prefix} " if log_prefix else ""

    with get_db() as db:
        crud.update_registration_task(db, task_uuid, status="running", started_at=datetime.utcnow())

    task_manager.update_status(task_uuid, "running")
    callback(f"{full_prefix}[系统] 补录任务开始")

    try:
        with get_db() as db:
            account = crud.get_account_by_id(db, account_id)
            if not account:
                raise ValueError("账号不存在")
            supported_recovery_types = {"outlook", "imap_mail", "tempmail", "temp_mail", "freemail", "moe_mail"}
            if account.email_service not in supported_recovery_types:
                raise ValueError("当前仅支持 Outlook / IMAP / Tempmail / Temp-Mail / Freemail / MoeMail 补录")
            if not account.password:
                raise ValueError("账号缺少登录密码，无法补录")

            email_service_model = _find_mailbox_service_for_account(db, account)
            if not email_service_model:
                raise ValueError("未找到匹配的邮箱配置，无法收取登录验证码")

            email_service = EmailServiceFactory.create(
                EmailServiceType(email_service_model.service_type),
                email_service_model.config,
                email_service_model.name,
            )
            recovery_email_info = _prepare_recovery_email_info(db, account, email_service, callback)
            settings = get_settings()
            max_proxy_retries = 3 if _has_available_proxy_source(proxy) else 1
            token_info = None
            actual_proxy = None
            last_recover_error: Optional[Exception] = None
            engine = None
            for proxy_attempt in range(1, max_proxy_retries + 1):
                actual_proxy = _get_recovery_proxy(proxy, log_callback=callback)
                engine = RegistrationEngine(
                    email_service=email_service,
                    proxy_url=actual_proxy,
                    callback_logger=callback,
                    task_uuid=task_uuid,
                )
                engine.account_extra_data = dict(account.extra_data or {})
                engine.mfa_secret = str((account.extra_data or {}).get("mfa_totp_secret") or "").strip()
                if recovery_email_info:
                    engine.email_info = recovery_email_info
                    callback(f"{full_prefix}[系统] 已注入邮箱凭据标识: {recovery_email_info['service_id']}")
                callback(f"{full_prefix}[系统] 将使用全新登录会话，不复用注册阶段 session/cookie/device_id")
                try:
                    token_info = engine.recover_oauth_tokens(account.email, account.password)
                    last_recover_error = None
                    if not token_info:
                        recent_logs = "\n".join(str(x or "") for x in getattr(engine, "logs", [])[-20:])
                        if _is_retryable_oauth_network_failure(recent_logs):
                            retry_callback = getattr(engine, "retry_last_oauth_callback_token_exchange", None)
                            if callable(retry_callback):
                                callback(f"{full_prefix}[系统] 检测到 OAuth 最终换 token 阶段失败，先仅重试 token exchange")
                                retry_proxy = actual_proxy
                                if proxy_attempt < max_proxy_retries and not proxy:
                                    retry_proxy = _get_recovery_proxy(proxy, log_callback=callback)
                                token_info = retry_callback(retry_proxy)
                            if token_info:
                                last_recover_error = None
                                break
                            if proxy_attempt < max_proxy_retries:
                                callback(f"{full_prefix}[系统] OAuth 最终换 token 阶段仍失败，自动更换/重试代理重跑 ({proxy_attempt}/{max_proxy_retries})")
                                continue
                    break
                except Exception as exc:
                    last_recover_error = exc
                    if proxy_attempt < max_proxy_retries and _is_retryable_oauth_network_failure(exc):
                        callback(f"{full_prefix}[系统] 当前代理网络/TLS 异常，自动更换/重试代理 ({proxy_attempt}/{max_proxy_retries})")
                        continue
                    raise
            if token_info is None and last_recover_error is not None:
                raise last_recover_error
            if not token_info:
                deleted_reason = _detect_deleted_or_deactivated_account(engine)
                mfa_reason = _detect_mfa_required_account(engine)
                if deleted_reason:
                    _mark_account_deleted_or_deactivated(db, account, deleted_reason, proxy_used=actual_proxy)
                    callback(f"{full_prefix}[系统] 已将账号标记为封禁：{deleted_reason}")
                elif mfa_reason:
                    _mark_account_mfa_required(db, account, mfa_reason, proxy_used=actual_proxy)
                    callback(f"{full_prefix}[系统] 已将账号标记为需要 MFA：{mfa_reason}")
                    raise RuntimeError("补录失败：该账号需要 MFA 二次验证，请在账号详情中填写 MFA 密钥后重试")
                raise RuntimeError("补录失败：未获取到 OAuth Token")

            extra = dict(account.extra_data or {})
            for key in (
                "oauth_recovery_required", "oauth_recovery_required_reason", "oauth_recovery_required_marked_at",
                "token_validation_state", "token_validation_reason", "token_validation_marked_at",
                "openai_auth_state", "openai_auth_state_reason", "openai_auth_state_marked_at",
            ):
                extra.pop(key, None)
            if str(extra.get("openai_account_state") or "").strip().lower() == "forbidden_or_banned":
                for key in ("openai_account_state", "openai_account_state_reason", "openai_account_state_marked_at"):
                    extra.pop(key, None)

            update_data = {
                "access_token": token_info.get("access_token"),
                "refresh_token": token_info.get("refresh_token"),
                "id_token": token_info.get("id_token"),
                "session_token": token_info.get("session_token"),
                "account_id": token_info.get("account_id") or account.account_id,
                "workspace_id": token_info.get("workspace_id") or account.workspace_id,
                "last_refresh": datetime.utcnow(),
                "status": AccountStatus.ACTIVE.value,
                "proxy_used": actual_proxy,
                "extra_data": extra,
            }
            expires_at = token_info.get("expires_at")
            if expires_at:
                update_data["expires_at"] = expires_at

            crud.update_account(db, account.id, **update_data)
            result_payload = {
                "success": True,
                "account_id": account.id,
                "email": account.email,
                "workspace_id": update_data.get("workspace_id"),
                "has_access_token": bool(update_data.get("access_token")),
            }
            crud.update_registration_task(
                db,
                task_uuid,
                status="completed",
                completed_at=datetime.utcnow(),
                result=result_payload,
            )

        callback(f"{full_prefix}[成功] OAuth 补录完成")
        task_manager.update_status(task_uuid, "completed", result={"account_id": account_id})
    except Exception as e:
        error_message = str(e)
        logger.warning(f"补录任务失败: {task_uuid}, 原因: {error_message}")
        with get_db() as db:
            crud.update_registration_task(
                db,
                task_uuid,
                status="failed",
                completed_at=datetime.utcnow(),
                error_message=error_message,
            )
        callback(f"{full_prefix}[失败] {error_message}")
        task_manager.update_status(task_uuid, "failed", error=error_message)


async def run_recover_oauth_task(
    task_uuid: str,
    account_id: int,
    proxy: Optional[str],
    log_prefix: str = "",
    batch_id: str = "",
):
    """异步包装补录任务。"""
    loop = task_manager.get_loop()
    if loop is None:
        loop = asyncio.get_event_loop()
        task_manager.set_loop(loop)

    task_manager.update_status(task_uuid, "pending")
    queue_msg = f"{log_prefix} [系统] 补录任务 {task_uuid[:8]} 已加入队列" if log_prefix else f"[系统] 补录任务 {task_uuid[:8]} 已加入队列"
    task_manager.add_log(task_uuid, queue_msg)
    if batch_id:
        task_manager.add_batch_log(batch_id, queue_msg)
    await loop.run_in_executor(
        task_manager.executor,
        _run_sync_recover_oauth_task,
        task_uuid,
        account_id,
        proxy,
        log_prefix,
        batch_id,
    )


async def run_batch_recover_oauth(batch_id: str, task_account_pairs: List[dict], proxy: Optional[str]):
    """顺序执行批量补录，并向批量 WS 推送日志。"""
    task_manager.init_batch(batch_id, len(task_account_pairs))
    recovery_batches[batch_id] = {
        "status": "running",
        "total": len(task_account_pairs),
        "completed": 0,
        "success": 0,
        "failed": 0,
        "finished": False,
        "tasks": task_account_pairs,
    }
    task_manager.add_batch_log(batch_id, f"[系统] 批量补录开始，共 {len(task_account_pairs)} 个账号")

    for index, item in enumerate(task_account_pairs, start=1):
        prefix = f"[任务{index}]"
        task_manager.add_batch_log(batch_id, f"{prefix} 启动补录: {item['email']}")
        await run_recover_oauth_task(
            item["task_uuid"],
            item["account_id"],
            proxy,
            log_prefix=prefix,
            batch_id=batch_id,
        )

        with get_db() as db:
            task = crud.get_registration_task_by_uuid(db, item["task_uuid"])
            ok = bool(task and task.status == "completed")

        recovery_batches[batch_id]["completed"] += 1
        if ok:
            recovery_batches[batch_id]["success"] += 1
            task_manager.add_batch_log(batch_id, f"{prefix} 补录成功: {item['email']}")
        else:
            recovery_batches[batch_id]["failed"] += 1
            error_detail = (task.error_message if task else "") or "未知错误"
            task_manager.add_batch_log(batch_id, f"{prefix} 补录失败: {item['email']} | 原因: {error_detail}")

        task_manager.update_batch_status(
            batch_id,
            completed=recovery_batches[batch_id]["completed"],
            success=recovery_batches[batch_id]["success"],
            failed=recovery_batches[batch_id]["failed"],
            current_index=index,
        )

    recovery_batches[batch_id]["status"] = "completed"
    recovery_batches[batch_id]["finished"] = True
    task_manager.update_batch_status(batch_id, status="completed", finished=True)
    task_manager.add_batch_log(
        batch_id,
        f"[系统] 批量补录结束，成功 {recovery_batches[batch_id]['success']}，失败 {recovery_batches[batch_id]['failed']}",
    )


def _run_single_refresh(account_id: int, proxy: Optional[str]):
    return do_refresh(account_id, proxy)


def _run_sync_refresh_task(task_uuid: str, account_id: int, request_proxy: Optional[str]):
    callback = task_manager.create_log_callback(task_uuid)
    task_manager.update_status(task_uuid, "running")
    callback("[系统] 刷新任务开始")
    try:
        proxy = _get_proxy(request_proxy, purpose="refresh")
        if proxy:
            callback(f"[系统] 本次刷新将使用代理: {proxy}")
        else:
            callback("[系统] 本次刷新将直连")
        result = do_refresh(account_id, proxy)
        if result.success:
            callback("[成功] Token 刷新完成")
            task_manager.update_status(task_uuid, "completed", result={
                "account_id": account_id,
                "expires_at": result.expires_at.isoformat() if result.expires_at else None,
            })
        else:
            callback(f"[失败] {result.error_message}")
            task_manager.update_status(task_uuid, "failed", error=result.error_message)
    except Exception as exc:
        callback(f"[失败] {exc}")
        task_manager.update_status(task_uuid, "failed", error=str(exc))


def _run_sync_validate_task(task_uuid: str, account_id: int, request_proxy: Optional[str]):
    callback = task_manager.create_log_callback(task_uuid)
    task_manager.update_status(task_uuid, "running")
    callback("[系统] 验证任务开始")
    try:
        proxy = _get_proxy(request_proxy, purpose="validate")
        if proxy:
            callback(f"[系统] 本次验证将使用代理: {proxy}")
        else:
            callback("[系统] 本次验证将直连")
        ok, error = do_validate(account_id, proxy)
        if ok:
            callback("[成功] Token 验证通过")
            task_manager.update_status(task_uuid, "completed", result={"account_id": account_id})
        else:
            callback(f"[失败] {error or 'Token 验证失败'}")
            task_manager.update_status(task_uuid, "failed", error=error or 'Token 验证失败')
    except Exception as exc:
        callback(f"[失败] {exc}")
        task_manager.update_status(task_uuid, "failed", error=str(exc))


async def run_validate_task(task_uuid: str, account_id: int, request_proxy: Optional[str]):
    loop = task_manager.get_loop()
    if loop is None:
        loop = asyncio.get_event_loop()
        task_manager.set_loop(loop)
    task_manager.update_status(task_uuid, "pending")
    task_manager.add_log(task_uuid, f"[系统] 验证任务 {task_uuid[:8]} 已加入队列")
    await loop.run_in_executor(task_manager.executor, _run_sync_validate_task, task_uuid, account_id, request_proxy)


async def run_refresh_task(task_uuid: str, account_id: int, request_proxy: Optional[str]):
    loop = task_manager.get_loop()
    if loop is None:
        loop = asyncio.get_event_loop()
        task_manager.set_loop(loop)
    task_manager.update_status(task_uuid, "pending")
    task_manager.add_log(task_uuid, f"[系统] 刷新任务 {task_uuid[:8]} 已加入队列")
    await loop.run_in_executor(task_manager.executor, _run_sync_refresh_task, task_uuid, account_id, request_proxy)


async def run_batch_refresh(batch_id: str, account_ids: List[int], request_proxy: Optional[str]):
    settings = get_settings()
    concurrency = max(1, min(5, int(getattr(settings, "registration_max_retries", 3) or 3)))
    task_manager.init_batch(batch_id, len(account_ids))
    refresh_batches[batch_id] = {
        "status": "running",
        "total": len(account_ids),
        "completed": 0,
        "success": 0,
        "failed": 0,
        "finished": False,
        "mode": "batch_refresh",
    }
    task_manager.add_batch_log(batch_id, f"[系统] 批量刷新开始，共 {len(account_ids)} 个账号，并发 {concurrency}")

    loop = task_manager.get_loop() or asyncio.get_event_loop()
    semaphore = asyncio.Semaphore(concurrency)

    async def worker(index: int, account_id: int):
        async with semaphore:
            prefix = f"[任务{index}]"
            task_manager.add_batch_log(batch_id, f"{prefix} 开始刷新账号 ID={account_id}")
            try:
                proxy = _get_proxy(request_proxy, purpose="refresh")
                result = await loop.run_in_executor(task_manager.executor, _run_single_refresh, account_id, proxy)
                ok = bool(result and result.success)
                if ok:
                    refresh_batches[batch_id]["success"] += 1
                    task_manager.add_batch_log(batch_id, f"{prefix} 刷新成功 ID={account_id}")
                else:
                    refresh_batches[batch_id]["failed"] += 1
                    err = getattr(result, "error_message", None) or "未知错误"
                    task_manager.add_batch_log(batch_id, f"{prefix} 刷新失败 ID={account_id} | 原因: {err}")
            except Exception as exc:
                refresh_batches[batch_id]["failed"] += 1
                task_manager.add_batch_log(batch_id, f"{prefix} 刷新异常 ID={account_id} | 原因: {exc}")
            finally:
                refresh_batches[batch_id]["completed"] += 1
                task_manager.update_batch_status(
                    batch_id,
                    completed=refresh_batches[batch_id]["completed"],
                    success=refresh_batches[batch_id]["success"],
                    failed=refresh_batches[batch_id]["failed"],
                    current_index=refresh_batches[batch_id]["completed"],
                )

    await asyncio.gather(*(worker(i, aid) for i, aid in enumerate(account_ids, start=1)))
    refresh_batches[batch_id]["status"] = "completed"
    refresh_batches[batch_id]["finished"] = True
    task_manager.update_batch_status(batch_id, status="completed", finished=True)
    task_manager.add_batch_log(batch_id, f"[系统] 批量刷新结束，成功 {refresh_batches[batch_id]['success']}，失败 {refresh_batches[batch_id]['failed']}")


@router.post("/batch-refresh")
async def batch_refresh_tokens(request: BatchRefreshRequest, background_tasks: BackgroundTasks):
    """批量刷新账号 Token（后台任务化）。"""
    with get_db() as db:
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )

    batch_id = str(uuid.uuid4())
    background_tasks.add_task(run_batch_refresh, batch_id, ids, request.proxy)
    return {
        "success": True,
        "batch_id": batch_id,
        "count": len(ids),
    }


@router.post("/{account_id}/refresh")
async def refresh_account_token(account_id: int, background_tasks: BackgroundTasks, request: Optional[TokenRefreshRequest] = Body(default=None)):
    """刷新单个账号的 Token（后台任务化）。"""
    task_uuid = str(uuid.uuid4())
    background_tasks.add_task(run_refresh_task, task_uuid, account_id, request.proxy if request else None)
    return {
        "success": True,
        "task_uuid": task_uuid,
        "status": "pending",
    }


@router.get("/batch-refresh/{batch_id}")
async def get_batch_refresh_status(batch_id: str):
    batch = refresh_batches.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    return batch


@router.get("/validate/task/{task_uuid}")
async def get_validate_task(task_uuid: str):
    status = task_manager.get_status(task_uuid)
    if not status:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task_uuid": task_uuid, **status}


@router.get("/refresh/task/{task_uuid}")
async def get_refresh_task(task_uuid: str):
    status = task_manager.get_status(task_uuid)
    if not status:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "task_uuid": task_uuid,
        **status,
    }


@router.post("/batch-recover-oauth")
async def batch_recover_oauth(request: BatchOAuthRecoveryRequest, background_tasks: BackgroundTasks):
    """批量补录 OAuth。"""
    with get_db() as db:
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )
        accounts = db.query(Account).filter(Account.id.in_(ids)).all()

        task_account_pairs = []
        for account in accounts:
            task_uuid = str(uuid.uuid4())
            crud.create_registration_task(db, task_uuid, proxy=request.proxy)
            task_account_pairs.append({
                "task_uuid": task_uuid,
                "account_id": account.id,
                "email": account.email,
            })

    batch_id = str(uuid.uuid4())
    background_tasks.add_task(run_batch_recover_oauth, batch_id, task_account_pairs, request.proxy)
    return {
        "success": True,
        "batch_id": batch_id,
        "count": len(task_account_pairs),
        "tasks": task_account_pairs,
    }


@router.post("/{account_id}/recover-oauth")
async def recover_account_oauth(
    account_id: int,
    background_tasks: BackgroundTasks,
    request: Optional[OAuthRecoveryRequest] = Body(default=None),
):
    """对单个账号执行 OAuth 补录。"""
    task_uuid = str(uuid.uuid4())
    proxy = request.proxy if request else None

    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        crud.create_registration_task(db, task_uuid, proxy=proxy)

    background_tasks.add_task(run_recover_oauth_task, task_uuid, account_id, proxy)
    return {
        "success": True,
        "task_uuid": task_uuid,
        "status": "pending",
    }


@router.get("/recover-oauth/task/{task_uuid}")
async def get_recover_oauth_task(task_uuid: str):
    """获取单个补录任务状态。"""
    with get_db() as db:
        task = crud.get_registration_task_by_uuid(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {
            "task_uuid": task.task_uuid,
            "status": task.status,
            "error_message": task.error_message,
            "result": task.result,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        }


@router.get("/recover-oauth/batch/{batch_id}")
async def get_recover_oauth_batch(batch_id: str):
    """获取批量补录任务状态。"""
    batch = recovery_batches.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    return batch


@router.post("/batch-validate")
async def batch_validate_tokens(request: BatchValidateRequest):
    """批量验证账号 Token 有效性"""
    proxy = _get_proxy(request.proxy, purpose="validate")

    results = {
        "valid_count": 0,
        "invalid_count": 0,
        "details": []
    }

    with get_db() as db:
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )

    for account_id in ids:
        try:
            is_valid, error = do_validate(account_id, proxy)
            results["details"].append({
                "id": account_id,
                "valid": is_valid,
                "error": error
            })
            if is_valid:
                results["valid_count"] += 1
            else:
                results["invalid_count"] += 1
        except Exception as e:
            results["invalid_count"] += 1
            results["details"].append({
                "id": account_id,
                "valid": False,
                "error": str(e)
            })

    return results


@router.post("/{account_id}/validate")
async def validate_account_token(account_id: int, background_tasks: BackgroundTasks, request: Optional[TokenValidateRequest] = Body(default=None)):
    """验证单个账号的 Token 有效性（后台任务化）。"""
    task_uuid = str(uuid.uuid4())
    background_tasks.add_task(run_validate_task, task_uuid, account_id, request.proxy if request else None)
    return {
        "success": True,
        "task_uuid": task_uuid,
        "status": "pending",
    }


# ============== CPA 上传相关 ==============

class CPAUploadRequest(BaseModel):
    """CPA 上传请求"""
    proxy: Optional[str] = None
    cpa_service_id: Optional[int] = None  # 指定 CPA 服务 ID，不传则使用全局配置


class BatchCPAUploadRequest(BaseModel):
    """批量 CPA 上传请求"""
    ids: List[int] = []
    proxy: Optional[str] = None
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None
    cpa_service_id: Optional[int] = None  # 指定 CPA 服务 ID，不传则使用全局配置


@router.post("/batch-upload-cpa")
async def batch_upload_accounts_to_cpa(request: BatchCPAUploadRequest):
    """批量上传账号到 CPA"""

    proxy = request.proxy

    # 解析指定的 CPA 服务
    cpa_api_url = None
    cpa_api_token = None
    include_proxy_url = False
    if request.cpa_service_id:
        with get_db() as db:
            svc = crud.get_cpa_service_by_id(db, request.cpa_service_id)
            if not svc:
                raise HTTPException(status_code=404, detail="指定的 CPA 服务不存在")
            cpa_api_url = svc.api_url
            cpa_api_token = svc.api_token
            include_proxy_url = bool(svc.include_proxy_url)

    with get_db() as db:
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )

    results = batch_upload_to_cpa(
        ids,
        proxy,
        api_url=cpa_api_url,
        api_token=cpa_api_token,
        include_proxy_url=include_proxy_url,
    )
    return results


@router.post("/{account_id}/upload-cpa")
async def upload_account_to_cpa(account_id: int, request: Optional[CPAUploadRequest] = Body(default=None)):
    """上传单个账号到 CPA"""

    proxy = request.proxy if request else None
    cpa_service_id = request.cpa_service_id if request else None

    # 解析指定的 CPA 服务
    cpa_api_url = None
    cpa_api_token = None
    include_proxy_url = False
    if cpa_service_id:
        with get_db() as db:
            svc = crud.get_cpa_service_by_id(db, cpa_service_id)
            if not svc:
                raise HTTPException(status_code=404, detail="指定的 CPA 服务不存在")
            cpa_api_url = svc.api_url
            cpa_api_token = svc.api_token
            include_proxy_url = bool(svc.include_proxy_url)

    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        if not account.access_token:
            return {
                "success": False,
                "error": "账号缺少 Token，无法上传"
            }

        # 生成 Token JSON
        token_data = generate_token_json(
            account,
            include_proxy_url=include_proxy_url,
            proxy_url=proxy,
        )

        # 上传
        success, message = upload_to_cpa(token_data, proxy, api_url=cpa_api_url, api_token=cpa_api_token)

        if success:
            account.cpa_uploaded = True
            account.cpa_uploaded_at = datetime.utcnow()
            db.commit()
            return {"success": True, "message": message}
        else:
            return {"success": False, "error": message}


class Sub2ApiUploadRequest(BaseModel):
    """单账号 Sub2API 上传请求"""
    service_id: Optional[int] = None
    concurrency: int = 3
    priority: int = 50


class BatchSub2ApiUploadRequest(BaseModel):
    """批量 Sub2API 上传请求"""
    ids: List[int] = []
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None
    service_id: Optional[int] = None  # 指定 Sub2API 服务 ID，不传则使用第一个启用的
    concurrency: int = 3
    priority: int = 50


@router.post("/batch-upload-sub2api")
async def batch_upload_accounts_to_sub2api(request: BatchSub2ApiUploadRequest):
    """批量上传账号到 Sub2API"""

    # 解析指定的 Sub2API 服务
    api_url = None
    api_key = None
    if request.service_id:
        with get_db() as db:
            svc = crud.get_sub2api_service_by_id(db, request.service_id)
            if not svc:
                raise HTTPException(status_code=404, detail="指定的 Sub2API 服务不存在")
            api_url = svc.api_url
            api_key = svc.api_key
    else:
        with get_db() as db:
            svcs = crud.get_sub2api_services(db, enabled=True)
            if svcs:
                api_url = svcs[0].api_url
                api_key = svcs[0].api_key

    if not api_url or not api_key:
        raise HTTPException(status_code=400, detail="未找到可用的 Sub2API 服务，请先在设置中配置")

    with get_db() as db:
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )

    results = batch_upload_to_sub2api(
        ids, api_url, api_key,
        concurrency=request.concurrency,
        priority=request.priority,
    )
    return results


@router.post("/{account_id}/upload-sub2api")
async def upload_account_to_sub2api(account_id: int, request: Optional[Sub2ApiUploadRequest] = Body(default=None)):
    """上传单个账号到 Sub2API"""

    service_id = request.service_id if request else None
    concurrency = request.concurrency if request else 3
    priority = request.priority if request else 50

    api_url = None
    api_key = None
    if service_id:
        with get_db() as db:
            svc = crud.get_sub2api_service_by_id(db, service_id)
            if not svc:
                raise HTTPException(status_code=404, detail="指定的 Sub2API 服务不存在")
            api_url = svc.api_url
            api_key = svc.api_key
    else:
        with get_db() as db:
            svcs = crud.get_sub2api_services(db, enabled=True)
            if svcs:
                api_url = svcs[0].api_url
                api_key = svcs[0].api_key

    if not api_url or not api_key:
        raise HTTPException(status_code=400, detail="未找到可用的 Sub2API 服务，请先在设置中配置")

    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        if not account.access_token:
            return {"success": False, "error": "账号缺少 Token，无法上传"}

        success, message = upload_to_sub2api(
            [account], api_url, api_key,
            concurrency=concurrency, priority=priority
        )
        if success:
            return {"success": True, "message": message}
        else:
            return {"success": False, "error": message}


# ============== Team Manager 上传 ==============

class UploadTMRequest(BaseModel):
    service_id: Optional[int] = None


class BatchUploadTMRequest(BaseModel):
    ids: List[int] = []
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None
    service_id: Optional[int] = None


@router.post("/batch-upload-tm")
async def batch_upload_accounts_to_tm(request: BatchUploadTMRequest):
    """批量上传账号到 Team Manager"""

    with get_db() as db:
        if request.service_id:
            svc = crud.get_tm_service_by_id(db, request.service_id)
        else:
            svcs = crud.get_tm_services(db, enabled=True)
            svc = svcs[0] if svcs else None

        if not svc:
            raise HTTPException(status_code=400, detail="未找到可用的 Team Manager 服务，请先在设置中配置")

        api_url = svc.api_url
        api_key = svc.api_key

        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )

    results = batch_upload_to_team_manager(ids, api_url, api_key)
    return results


@router.post("/{account_id}/upload-tm")
async def upload_account_to_tm(account_id: int, request: Optional[UploadTMRequest] = Body(default=None)):
    """上传单账号到 Team Manager"""

    service_id = request.service_id if request else None

    with get_db() as db:
        if service_id:
            svc = crud.get_tm_service_by_id(db, service_id)
        else:
            svcs = crud.get_tm_services(db, enabled=True)
            svc = svcs[0] if svcs else None

        if not svc:
            raise HTTPException(status_code=400, detail="未找到可用的 Team Manager 服务，请先在设置中配置")

        api_url = svc.api_url
        api_key = svc.api_key

        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        success, message = upload_to_team_manager(account, api_url, api_key)

    return {"success": success, "message": message}


# ============== Inbox Code ==============

def _build_inbox_config(db, service_type, email: str) -> dict:
    """根据账号邮箱服务类型从数据库构建服务配置（不传 proxy_url）"""
    from ...database.models import EmailService as EmailServiceModel
    from ...services import EmailServiceType as EST

    if service_type == EST.TEMPMAIL:
        settings = get_settings()
        return {
            "base_url": settings.tempmail_base_url,
            "timeout": settings.tempmail_timeout,
            "max_retries": settings.tempmail_max_retries,
        }

    if service_type == EST.MOE_MAIL:
        # 按域名后缀匹配，找不到则取 priority 最小的
        domain = email.split("@")[1] if "@" in email else ""
        services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "moe_mail",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()
        svc = None
        for s in services:
            cfg = s.config or {}
            if cfg.get("default_domain") == domain or cfg.get("domain") == domain:
                svc = s
                break
        if not svc and services:
            svc = services[0]
        if not svc:
            return None
        cfg = svc.config.copy()
        if "api_url" in cfg and "base_url" not in cfg:
            cfg["base_url"] = cfg.pop("api_url")
        return cfg

    # 其余服务类型：直接按 service_type 查数据库
    type_map = {
        EST.TEMP_MAIL: "temp_mail",
        EST.DUCK_MAIL: "duck_mail",
        EST.FREEMAIL: "freemail",
        EST.IMAP_MAIL: "imap_mail",
        EST.OUTLOOK: "outlook",
    }
    db_type = type_map.get(service_type)
    if not db_type:
        return None

    query = db.query(EmailServiceModel).filter(
        EmailServiceModel.service_type == db_type,
        EmailServiceModel.enabled == True
    )
    if service_type == EST.OUTLOOK:
        # 按 config.email 匹配账号 email
        services = query.all()
        svc = next((s for s in services if (s.config or {}).get("email") == email), None)
    else:
        svc = query.order_by(EmailServiceModel.priority.asc()).first()

    if not svc:
        return None
    cfg = svc.config.copy() if svc.config else {}
    if "api_url" in cfg and "base_url" not in cfg:
        cfg["base_url"] = cfg.pop("api_url")
    return cfg


@router.post("/{account_id}/inbox-code")
async def get_account_inbox_code(account_id: int):
    """查询账号邮箱收件箱最新验证码"""
    from ...services import EmailServiceFactory, EmailServiceType

    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        try:
            service_type = EmailServiceType(account.email_service)
        except ValueError:
            return {"success": False, "error": "不支持的邮箱服务类型"}

        config = _build_inbox_config(db, service_type, account.email)
        if config is None:
            return {"success": False, "error": "未找到可用的邮箱服务配置"}

        try:
            svc = EmailServiceFactory.create(service_type, config)
            email_info = _prepare_recovery_email_info(db, account, svc)
            code = svc.get_verification_code(
                account.email,
                email_id=(email_info or {}).get("service_id"),
                timeout=12
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

        if not code:
            return {"success": False, "error": "未收到验证码邮件"}

        return {"success": True, "code": code, "email": account.email}
