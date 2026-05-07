"""
数据库 CRUD 操作
"""

from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc, asc, func

from .models import Account, EmailService, RegistrationTask, Setting, Proxy, CpaService, Sub2ApiService, PhoneVerificationAttempt, PhoneNumberReputation, BatchTask


PHONE_REPUTATION_MAX_SUCCESS_USES = 3


# ============================================================================
# 账户 CRUD
# ============================================================================

def create_account(
    db: Session,
    email: str,
    email_service: str,
    password: Optional[str] = None,
    client_id: Optional[str] = None,
    session_token: Optional[str] = None,
    email_service_id: Optional[str] = None,
    account_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    id_token: Optional[str] = None,
    proxy_used: Optional[str] = None,
    expires_at: Optional['datetime'] = None,
    extra_data: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
    source: Optional[str] = None
) -> Account:
    """创建新账户"""
    db_account = Account(
        email=email,
        password=password,
        client_id=client_id,
        session_token=session_token,
        email_service=email_service,
        email_service_id=email_service_id,
        account_id=account_id,
        workspace_id=workspace_id,
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
        proxy_used=proxy_used,
        expires_at=expires_at,
        extra_data=extra_data or {},
        status=status or 'active',
        source=source or 'register',
        registered_at=datetime.utcnow()
    )
    db.add(db_account)
    db.commit()
    db.refresh(db_account)
    return db_account


def get_account_by_id(db: Session, account_id: int) -> Optional[Account]:
    """根据 ID 获取账户"""
    return db.query(Account).filter(Account.id == account_id).first()


def get_account_by_email(db: Session, email: str) -> Optional[Account]:
    """根据邮箱获取账户"""
    return db.query(Account).filter(Account.email == email).first()


def get_accounts(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    email_service: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None
) -> List[Account]:
    """获取账户列表（支持分页、筛选）"""
    query = db.query(Account)

    if email_service:
        query = query.filter(Account.email_service == email_service)

    if status:
        query = query.filter(Account.status == status)

    if search:
        search_filter = or_(
            Account.email.ilike(f"%{search}%"),
            Account.account_id.ilike(f"%{search}%"),
            Account.workspace_id.ilike(f"%{search}%")
        )
        query = query.filter(search_filter)

    query = query.order_by(desc(Account.created_at)).offset(skip).limit(limit)
    return query.all()


def update_account(
    db: Session,
    db_account_id: int,
    **kwargs
) -> Optional[Account]:
    """更新账户信息"""
    db_account = get_account_by_id(db, db_account_id)
    if not db_account:
        return None

    for key, value in kwargs.items():
        if hasattr(db_account, key):
            setattr(db_account, key, value)

    db.commit()
    db.refresh(db_account)
    return db_account


def update_accounts_batch(
    db: Session,
    account_ids: List[int],
    **fields
) -> int:
    """批量更新账户信息"""
    clean_fields = {key: value for key, value in fields.items() if hasattr(Account, key)}
    if not clean_fields or not account_ids:
        return 0
    result = db.query(Account).filter(Account.id.in_(account_ids)).update(
        clean_fields,
        synchronize_session=False,
    )
    db.commit()
    return result


def delete_account(db: Session, account_id: int) -> bool:
    """删除账户"""
    db_account = get_account_by_id(db, account_id)
    if not db_account:
        return False

    db.delete(db_account)
    db.commit()
    return True


def delete_accounts_batch(db: Session, account_ids: List[int]) -> int:
    """批量删除账户"""
    result = db.query(Account).filter(Account.id.in_(account_ids)).delete(synchronize_session=False)
    db.commit()
    return result


def get_accounts_count(
    db: Session,
    email_service: Optional[str] = None,
    status: Optional[str] = None
) -> int:
    """获取账户数量"""
    query = db.query(func.count(Account.id))

    if email_service:
        query = query.filter(Account.email_service == email_service)

    if status:
        query = query.filter(Account.status == status)

    return query.scalar()


# ============================================================================
# 邮箱服务 CRUD
# ============================================================================

def create_email_service(
    db: Session,
    service_type: str,
    name: str,
    config: Dict[str, Any],
    enabled: bool = True,
    priority: int = 0
) -> EmailService:
    """创建邮箱服务配置"""
    db_service = EmailService(
        service_type=service_type,
        name=name,
        config=config,
        enabled=enabled,
        priority=priority
    )
    db.add(db_service)
    db.commit()
    db.refresh(db_service)
    return db_service


def get_email_service_by_id(db: Session, service_id: int) -> Optional[EmailService]:
    """根据 ID 获取邮箱服务"""
    return db.query(EmailService).filter(EmailService.id == service_id).first()


def get_email_services(
    db: Session,
    service_type: Optional[str] = None,
    enabled: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100
) -> List[EmailService]:
    """获取邮箱服务列表"""
    query = db.query(EmailService)

    if service_type:
        query = query.filter(EmailService.service_type == service_type)

    if enabled is not None:
        query = query.filter(EmailService.enabled == enabled)

    query = query.order_by(
        asc(EmailService.priority),
        desc(EmailService.last_used)
    ).offset(skip).limit(limit)

    return query.all()


def update_email_service(
    db: Session,
    service_id: int,
    **kwargs
) -> Optional[EmailService]:
    """更新邮箱服务配置"""
    db_service = get_email_service_by_id(db, service_id)
    if not db_service:
        return None

    for key, value in kwargs.items():
        if hasattr(db_service, key) and value is not None:
            setattr(db_service, key, value)

    db.commit()
    db.refresh(db_service)
    return db_service


def delete_email_service(db: Session, service_id: int) -> bool:
    """删除邮箱服务配置"""
    db_service = get_email_service_by_id(db, service_id)
    if not db_service:
        return False

    db.delete(db_service)
    db.commit()
    return True


# ============================================================================
# 注册任务 CRUD
# ============================================================================

def create_registration_task(
    db: Session,
    task_uuid: str,
    email_service_id: Optional[int] = None,
    proxy: Optional[str] = None
) -> RegistrationTask:
    """创建注册任务"""
    db_task = RegistrationTask(
        task_uuid=task_uuid,
        email_service_id=email_service_id,
        proxy=proxy,
        status='pending'
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


def get_registration_task_by_uuid(db: Session, task_uuid: str) -> Optional[RegistrationTask]:
    """根据 UUID 获取注册任务"""
    return db.query(RegistrationTask).filter(RegistrationTask.task_uuid == task_uuid).first()


def get_registration_tasks(
    db: Session,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> List[RegistrationTask]:
    """获取注册任务列表"""
    query = db.query(RegistrationTask)

    if status:
        query = query.filter(RegistrationTask.status == status)

    query = query.order_by(desc(RegistrationTask.created_at)).offset(skip).limit(limit)
    return query.all()


def update_registration_task(
    db: Session,
    task_uuid: str,
    **kwargs
) -> Optional[RegistrationTask]:
    """更新注册任务状态"""
    db_task = get_registration_task_by_uuid(db, task_uuid)
    if not db_task:
        return None

    for key, value in kwargs.items():
        if hasattr(db_task, key):
            setattr(db_task, key, value)

    db.commit()
    db.refresh(db_task)
    return db_task


def append_task_log(db: Session, task_uuid: str, log_message: str) -> bool:
    """追加任务日志"""
    db_task = get_registration_task_by_uuid(db, task_uuid)
    if not db_task:
        return False

    if db_task.logs:
        db_task.logs += f"\n{log_message}"
    else:
        db_task.logs = log_message

    db.commit()
    return True


def delete_registration_task(db: Session, task_uuid: str) -> bool:
    """删除注册任务"""
    db_task = get_registration_task_by_uuid(db, task_uuid)
    if not db_task:
        return False

    db.delete(db_task)
    db.commit()
    return True


# 为 API 路由添加别名
get_account = get_account_by_id
get_registration_task = get_registration_task_by_uuid


# ============================================================================
# 设置 CRUD
# ============================================================================

def get_setting(db: Session, key: str) -> Optional[Setting]:
    """获取设置"""
    return db.query(Setting).filter(Setting.key == key).first()


def get_settings_by_category(db: Session, category: str) -> List[Setting]:
    """根据分类获取设置"""
    return db.query(Setting).filter(Setting.category == category).all()


def set_setting(
    db: Session,
    key: str,
    value: str,
    description: Optional[str] = None,
    category: str = 'general'
) -> Setting:
    """设置或更新配置项"""
    db_setting = get_setting(db, key)
    if db_setting:
        db_setting.value = value
        db_setting.description = description or db_setting.description
        db_setting.category = category
        db_setting.updated_at = datetime.utcnow()
    else:
        db_setting = Setting(
            key=key,
            value=value,
            description=description,
            category=category
        )
        db.add(db_setting)

    db.commit()
    db.refresh(db_setting)
    return db_setting


def delete_setting(db: Session, key: str) -> bool:
    """删除设置"""
    db_setting = get_setting(db, key)
    if not db_setting:
        return False

    db.delete(db_setting)
    db.commit()
    return True


# ============================================================================
# 手机验证统计 CRUD
# ============================================================================

def create_phone_verification_attempt(
    db: Session,
    **kwargs
) -> PhoneVerificationAttempt:
    record = PhoneVerificationAttempt(**kwargs)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def update_phone_verification_attempt(
    db: Session,
    attempt_id: int,
    **kwargs
) -> Optional[PhoneVerificationAttempt]:
    record = db.query(PhoneVerificationAttempt).filter(PhoneVerificationAttempt.id == attempt_id).first()
    if not record:
        return None
    for key, value in kwargs.items():
        if hasattr(record, key):
            setattr(record, key, value)
    db.commit()
    db.refresh(record)
    return record


def get_phone_number_reputation(db: Session, sms_provider: str, phone_number: str) -> Optional[PhoneNumberReputation]:
    sms_provider = str(sms_provider or "").strip().lower() or "herosms"
    phone_number = str(phone_number or "").strip()
    return db.query(PhoneNumberReputation).filter(
        PhoneNumberReputation.sms_provider == sms_provider,
        PhoneNumberReputation.phone_number == phone_number,
    ).first()


def upsert_phone_number_reputation(
    db: Session,
    *,
    sms_provider: str,
    phone_number: str,
    service: Optional[str] = None,
    country: Optional[int] = None,
    country_key: Optional[str] = None,
    provider_slot: Optional[str] = None,
    success: bool = False,
    blacklisted: bool = False,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    activation_cost: Optional[float] = None,
    result_label: Optional[str] = None,
) -> PhoneNumberReputation:
    sms_provider = str(sms_provider or "").strip().lower() or "herosms"
    phone_number = str(phone_number or "").strip()
    record = get_phone_number_reputation(db, sms_provider, phone_number)
    now = datetime.utcnow()
    if not record:
        record = PhoneNumberReputation(
            sms_provider=sms_provider,
            phone_number=phone_number,
            first_seen_at=now,
        )
        db.add(record)
    record.service = service or record.service
    record.country = country if country is not None else record.country
    record.country_key = country_key or record.country_key
    record.provider_slot = provider_slot or record.provider_slot
    record.last_seen_at = now
    record.last_activation_cost = activation_cost if activation_cost is not None else record.last_activation_cost
    if success:
        record.success_count = int(record.success_count or 0) + 1
        record.last_result = result_label or "success"
        if int(record.success_count or 0) >= PHONE_REPUTATION_MAX_SUCCESS_USES:
            record.blacklisted = True
            record.last_error_code = record.last_error_code or "phone_success_usage_limit"
            record.last_error_message = record.last_error_message or f"号码成功使用已达到 {PHONE_REPUTATION_MAX_SUCCESS_USES} 次上限"
    else:
        record.failure_count = int(record.failure_count or 0) + 1
        record.last_result = result_label or "failed"
    if blacklisted:
        record.blacklisted = True
    if error_code:
        record.last_error_code = error_code
    if error_message:
        record.last_error_message = error_message
    db.commit()
    db.refresh(record)
    return record


# ============================================================================
# 批量任务 CRUD
# ============================================================================

def create_batch_task(
    db: Session,
    batch_id: str,
    batch_type: str,
    total: int = 0,
    **kwargs
) -> BatchTask:
    """创建或更新批量任务记录"""
    existing = db.query(BatchTask).filter(BatchTask.batch_id == batch_id).first()
    if existing:
        for key, value in dict(kwargs, batch_type=batch_type, total=total, status='running').items():
            if hasattr(existing, key) and value is not None:
                setattr(existing, key, value)
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    bt = BatchTask(batch_id=batch_id, batch_type=batch_type, total=total, **kwargs)
    db.add(bt)
    db.commit()
    db.refresh(bt)
    return bt


def update_batch_task(
    db: Session,
    batch_id: str,
    **kwargs
) -> Optional[BatchTask]:
    """更新批量任务状态"""
    bt = db.query(BatchTask).filter(BatchTask.batch_id == batch_id).first()
    if not bt:
        return None
    for key, value in kwargs.items():
        if hasattr(bt, key) and value is not None:
            setattr(bt, key, value)
    bt.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(bt)
    return bt


def get_batch_task(db: Session, batch_id: str) -> Optional[BatchTask]:
    """查询批量任务"""
    return db.query(BatchTask).filter(BatchTask.batch_id == batch_id).first()


def get_interrupted_batch_tasks(db: Session) -> list:
    """查询所有未完成的批量任务（用于启动恢复）"""
    return db.query(BatchTask).filter(
        BatchTask.status.in_(['running', 'pending'])
    ).all()


def append_batch_task_log(db: Session, batch_id: str, log_message: str) -> None:
    """追加批量任务日志（线程安全尽量简单：直接拼接到 DB 中的 logs 字段）"""
    import json as _json
    bt = db.query(BatchTask).filter(BatchTask.batch_id == batch_id).first()
    if not bt:
        return
    lines = [line for line in (bt.logs or "").split("\n") if line.strip()]
    lines.append(log_message)
    bt.logs = "\n".join(lines[-500:])
    bt.updated_at = datetime.utcnow()
    db.commit()


def update_phone_attempt_stage(
    db: Session,
    attempt_id: int,
    stage: str,
    wait_timeout_seconds: Optional[int] = None,
    task_uuid: Optional[str] = None,
    batch_id: Optional[str] = None,
    **extra_fields
) -> None:
    """更新手机验证的阶段标记"""
    record = db.query(PhoneVerificationAttempt).filter(PhoneVerificationAttempt.id == attempt_id).first()
    if not record:
        return
    record.stage = stage
    if wait_timeout_seconds is not None:
        record.wait_timeout_seconds = wait_timeout_seconds
    if task_uuid is not None:
        record.task_uuid = task_uuid
    if batch_id is not None:
        record.batch_id = batch_id
    if stage == 'waiting_sms':
        record.wait_started_at = datetime.utcnow()
    for key, value in extra_fields.items():
        if hasattr(record, key) and value is not None:
            setattr(record, key, value)
    db.commit()


def get_pending_sms_verifications(db: Session) -> list:
    """查询所有等待短信中且未超时的记录（用于启动恢复）"""
    now = datetime.utcnow()
    candidates = db.query(PhoneVerificationAttempt).filter(
        PhoneVerificationAttempt.stage == 'waiting_sms',
        PhoneVerificationAttempt.success == False,
    ).all()
    active = []
    for r in candidates:
        if r.wait_started_at and r.wait_timeout_seconds:
            elapsed = (now - r.wait_started_at).total_seconds()
            if elapsed < r.wait_timeout_seconds + 30:
                active.append(r)
    return active


# ============================================================================
# 代理 CRUD
# ============================================================================

def create_proxy(
    db: Session,
    name: str,
    type: str,
    host: str,
    port: int,
    username: Optional[str] = None,
    password: Optional[str] = None,
    enabled: bool = True,
    priority: int = 0
) -> Proxy:
    """创建代理配置"""
    db_proxy = Proxy(
        name=name,
        type=type,
        host=host,
        port=port,
        username=username,
        password=password,
        enabled=enabled,
        priority=priority
    )
    db.add(db_proxy)
    db.commit()
    db.refresh(db_proxy)
    return db_proxy


def get_proxy_by_id(db: Session, proxy_id: int) -> Optional[Proxy]:
    """根据 ID 获取代理"""
    return db.query(Proxy).filter(Proxy.id == proxy_id).first()


def get_proxies(
    db: Session,
    enabled: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100
) -> List[Proxy]:
    """获取代理列表"""
    query = db.query(Proxy)

    if enabled is not None:
        query = query.filter(Proxy.enabled == enabled)

    query = query.order_by(desc(Proxy.created_at)).offset(skip).limit(limit)
    return query.all()


def get_enabled_proxies(db: Session) -> List[Proxy]:
    """获取所有启用的代理"""
    return db.query(Proxy).filter(Proxy.enabled == True).all()


def update_proxy(
    db: Session,
    proxy_id: int,
    **kwargs
) -> Optional[Proxy]:
    """更新代理配置"""
    db_proxy = get_proxy_by_id(db, proxy_id)
    if not db_proxy:
        return None

    for key, value in kwargs.items():
        if hasattr(db_proxy, key):
            setattr(db_proxy, key, value)

    db.commit()
    db.refresh(db_proxy)
    return db_proxy


def delete_proxy(db: Session, proxy_id: int) -> bool:
    """删除代理配置"""
    db_proxy = get_proxy_by_id(db, proxy_id)
    if not db_proxy:
        return False

    db.delete(db_proxy)
    db.commit()
    return True


def update_proxy_last_used(db: Session, proxy_id: int) -> bool:
    """更新代理最后使用时间"""
    db_proxy = get_proxy_by_id(db, proxy_id)
    if not db_proxy:
        return False

    db_proxy.last_used = datetime.utcnow()
    db.commit()
    return True


def get_random_proxy(db: Session) -> Optional[Proxy]:
    """随机获取一个启用的代理，优先返回 is_default=True 的代理"""
    import random
    # 优先返回默认代理
    default_proxy = db.query(Proxy).filter(Proxy.enabled == True, Proxy.is_default == True).first()
    if default_proxy:
        return default_proxy
    proxies = get_enabled_proxies(db)
    if not proxies:
        return None
    return random.choice(proxies)


def set_proxy_default(db: Session, proxy_id: int) -> Optional[Proxy]:
    """将指定代理设为默认，同时清除其他代理的默认标记"""
    # 清除所有默认标记
    db.query(Proxy).filter(Proxy.is_default == True).update({"is_default": False})
    # 设置新的默认代理
    proxy = db.query(Proxy).filter(Proxy.id == proxy_id).first()
    if proxy:
        proxy.is_default = True
        db.commit()
        db.refresh(proxy)
    return proxy


def get_proxies_count(db: Session, enabled: Optional[bool] = None) -> int:
    """获取代理数量"""
    query = db.query(func.count(Proxy.id))
    if enabled is not None:
        query = query.filter(Proxy.enabled == enabled)
    return query.scalar()


# ============================================================================
# CPA 服务 CRUD
# ============================================================================

def create_cpa_service(
    db: Session,
    name: str,
    api_url: str,
    api_token: str,
    enabled: bool = True,
    include_proxy_url: bool = False,
    priority: int = 0
) -> CpaService:
    """创建 CPA 服务配置"""
    db_service = CpaService(
        name=name,
        api_url=api_url,
        api_token=api_token,
        enabled=enabled,
        include_proxy_url=include_proxy_url,
        priority=priority
    )
    db.add(db_service)
    db.commit()
    db.refresh(db_service)
    return db_service


def get_cpa_service_by_id(db: Session, service_id: int) -> Optional[CpaService]:
    """根据 ID 获取 CPA 服务"""
    return db.query(CpaService).filter(CpaService.id == service_id).first()


def get_cpa_services(
    db: Session,
    enabled: Optional[bool] = None
) -> List[CpaService]:
    """获取 CPA 服务列表"""
    query = db.query(CpaService)
    if enabled is not None:
        query = query.filter(CpaService.enabled == enabled)
    return query.order_by(asc(CpaService.priority), asc(CpaService.id)).all()


def update_cpa_service(
    db: Session,
    service_id: int,
    **kwargs
) -> Optional[CpaService]:
    """更新 CPA 服务配置"""
    db_service = get_cpa_service_by_id(db, service_id)
    if not db_service:
        return None
    for key, value in kwargs.items():
        if hasattr(db_service, key):
            setattr(db_service, key, value)
    db.commit()
    db.refresh(db_service)
    return db_service


def delete_cpa_service(db: Session, service_id: int) -> bool:
    """删除 CPA 服务配置"""
    db_service = get_cpa_service_by_id(db, service_id)
    if not db_service:
        return False
    db.delete(db_service)
    db.commit()
    return True


# ============================================================================
# Sub2API 服务 CRUD
# ============================================================================

def create_sub2api_service(
    db: Session,
    name: str,
    api_url: str,
    api_key: str,
    enabled: bool = True,
    priority: int = 0
) -> Sub2ApiService:
    """创建 Sub2API 服务配置"""
    svc = Sub2ApiService(
        name=name,
        api_url=api_url,
        api_key=api_key,
        enabled=enabled,
        priority=priority,
    )
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return svc


def get_sub2api_service_by_id(db: Session, service_id: int) -> Optional[Sub2ApiService]:
    """按 ID 获取 Sub2API 服务"""
    return db.query(Sub2ApiService).filter(Sub2ApiService.id == service_id).first()


def get_sub2api_services(
    db: Session,
    enabled: Optional[bool] = None
) -> List[Sub2ApiService]:
    """获取 Sub2API 服务列表"""
    query = db.query(Sub2ApiService)
    if enabled is not None:
        query = query.filter(Sub2ApiService.enabled == enabled)
    return query.order_by(asc(Sub2ApiService.priority), asc(Sub2ApiService.id)).all()


def update_sub2api_service(db: Session, service_id: int, **kwargs) -> Optional[Sub2ApiService]:
    """更新 Sub2API 服务配置"""
    svc = get_sub2api_service_by_id(db, service_id)
    if not svc:
        return None
    for key, value in kwargs.items():
        setattr(svc, key, value)
    db.commit()
    db.refresh(svc)
    return svc


def delete_sub2api_service(db: Session, service_id: int) -> bool:
    """删除 Sub2API 服务配置"""
    svc = get_sub2api_service_by_id(db, service_id)
    if not svc:
        return False
    db.delete(svc)
    db.commit()
    return True


# ============================================================================
# Team Manager 服务 CRUD
# ============================================================================

def create_tm_service(
    db: Session,
    name: str,
    api_url: str,
    api_key: str,
    enabled: bool = True,
    priority: int = 0,
):
    """创建 Team Manager 服务配置"""
    from .models import TeamManagerService
    svc = TeamManagerService(
        name=name,
        api_url=api_url,
        api_key=api_key,
        enabled=enabled,
        priority=priority,
    )
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return svc


def get_tm_service_by_id(db: Session, service_id: int):
    """按 ID 获取 Team Manager 服务"""
    from .models import TeamManagerService
    return db.query(TeamManagerService).filter(TeamManagerService.id == service_id).first()


def get_tm_services(db: Session, enabled=None):
    """获取 Team Manager 服务列表"""
    from .models import TeamManagerService
    q = db.query(TeamManagerService)
    if enabled is not None:
        q = q.filter(TeamManagerService.enabled == enabled)
    return q.order_by(TeamManagerService.priority.asc(), TeamManagerService.id.asc()).all()


def update_tm_service(db: Session, service_id: int, **kwargs):
    """更新 Team Manager 服务配置"""
    svc = get_tm_service_by_id(db, service_id)
    if not svc:
        return None
    for k, v in kwargs.items():
        setattr(svc, k, v)
    db.commit()
    db.refresh(svc)
    return svc


def delete_tm_service(db: Session, service_id: int) -> bool:
    """删除 Team Manager 服务配置"""
    svc = get_tm_service_by_id(db, service_id)
    if not svc:
        return False
    db.delete(svc)
    db.commit()
    return True
