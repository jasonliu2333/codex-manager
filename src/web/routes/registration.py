"""
注册任务 API 路由
"""

import asyncio
import logging
import uuid
import random
from datetime import datetime
from typing import List, Optional, Dict, Tuple

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from ...database import crud
from ...database.session import get_db
from ...database.models import RegistrationTask, Proxy
from ...core.register import RegistrationEngine, RegistrationResult
from ...services import EmailServiceFactory, EmailServiceType
from ...config.settings import get_settings
from ..task_manager import task_manager

logger = logging.getLogger(__name__)
router = APIRouter()

# 任务存储（简单的内存存储，生产环境应使用 Redis）
running_tasks: dict = {}
# 批量任务存储
batch_tasks: Dict[str, dict] = {}


# ============== Proxy Helper Functions ==============

def get_proxy_for_registration(db) -> Tuple[Optional[str], Optional[int]]:
    """
    获取用于注册的代理

    策略：
    1. 优先从代理列表中随机选择一个启用的代理
    2. 如果代理列表为空，使用系统设置中的默认代理

    Returns:
        Tuple[proxy_url, proxy_id]: 代理 URL 和代理 ID（如果来自代理列表）
    """
    # 先尝试从代理列表中获取
    proxy = crud.get_random_proxy(db)
    if proxy:
        return proxy.proxy_url, proxy.id

    # 代理列表为空，使用系统设置中的默认代理
    settings = get_settings()
    if settings.proxy_enabled and settings.proxy_url:
        return settings.proxy_url, None

    return None, None


def update_proxy_usage(db, proxy_id: Optional[int]):
    """更新代理的使用时间"""
    if proxy_id:
        crud.update_proxy_last_used(db, proxy_id)


# ============== Pydantic Models ==============

class RegistrationTaskCreate(BaseModel):
    """创建注册任务请求"""
    email_service_type: str = "tempmail"
    proxy: Optional[str] = None
    email_service_config: Optional[dict] = None
    email_service_id: Optional[int] = None  # 使用数据库中已配置的邮箱服务 ID


class BatchRegistrationRequest(BaseModel):
    """批量注册请求"""
    count: int = 1  # 注册数量
    email_service_type: str = "tempmail"
    proxy: Optional[str] = None
    email_service_config: Optional[dict] = None
    email_service_id: Optional[int] = None  # 使用数据库中已配置的邮箱服务 ID
    interval_min: int = 5  # 最小间隔秒数
    interval_max: int = 30  # 最大间隔秒数


class RegistrationTaskResponse(BaseModel):
    """注册任务响应"""
    id: int
    task_uuid: str
    status: str
    email_service_id: Optional[int] = None
    proxy: Optional[str] = None
    logs: Optional[str] = None
    result: Optional[dict] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    class Config:
        from_attributes = True


class BatchRegistrationResponse(BaseModel):
    """批量注册响应"""
    batch_id: str
    count: int
    tasks: List[RegistrationTaskResponse]


class TaskListResponse(BaseModel):
    """任务列表响应"""
    total: int
    tasks: List[RegistrationTaskResponse]


# ============== Outlook 批量注册模型 ==============

class OutlookAccountForRegistration(BaseModel):
    """可用于注册的 Outlook 账户"""
    id: int                      # EmailService 表的 ID
    email: str
    name: str
    has_oauth: bool              # 是否有 OAuth 配置
    is_registered: bool          # 是否已注册
    registered_account_id: Optional[int] = None


class OutlookAccountsListResponse(BaseModel):
    """Outlook 账户列表响应"""
    total: int
    registered_count: int        # 已注册数量
    unregistered_count: int      # 未注册数量
    accounts: List[OutlookAccountForRegistration]


class OutlookBatchRegistrationRequest(BaseModel):
    """Outlook 批量注册请求"""
    service_ids: List[int]       # 选中的 EmailService ID
    skip_registered: bool = True  # 自动跳过已注册邮箱
    proxy: Optional[str] = None
    interval_min: int = 5
    interval_max: int = 30


class OutlookBatchRegistrationResponse(BaseModel):
    """Outlook 批量注册响应"""
    batch_id: str
    total: int                   # 总数
    skipped: int                 # 跳过数（已注册）
    to_register: int             # 待注册数
    service_ids: List[int]       # 实际要注册的服务 ID


# ============== Helper Functions ==============

def task_to_response(task: RegistrationTask) -> RegistrationTaskResponse:
    """转换任务模型为响应"""
    return RegistrationTaskResponse(
        id=task.id,
        task_uuid=task.task_uuid,
        status=task.status,
        email_service_id=task.email_service_id,
        proxy=task.proxy,
        logs=task.logs,
        result=task.result,
        error_message=task.error_message,
        created_at=task.created_at.isoformat() if task.created_at else None,
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
    )


def _run_sync_registration_task(task_uuid: str, email_service_type: str, proxy: Optional[str], email_service_config: Optional[dict], email_service_id: Optional[int] = None):
    """
    在线程池中执行的同步注册任务

    这个函数会被 run_in_executor 调用，运行在独立线程中
    """
    with get_db() as db:
        try:
            # 检查是否已取消
            if task_manager.is_cancelled(task_uuid):
                logger.info(f"任务 {task_uuid} 已取消，跳过执行")
                return

            # 更新任务状态为运行中
            task = crud.update_registration_task(
                db, task_uuid,
                status="running",
                started_at=datetime.utcnow()
            )

            if not task:
                logger.error(f"任务不存在: {task_uuid}")
                return

            # 更新 TaskManager 状态
            task_manager.update_status(task_uuid, "running")

            # 确定使用的代理
            # 如果前端传入了代理参数，使用传入的
            # 否则从代理列表或系统设置中获取
            actual_proxy_url = proxy
            proxy_id = None

            if not actual_proxy_url:
                actual_proxy_url, proxy_id = get_proxy_for_registration(db)
                if actual_proxy_url:
                    logger.info(f"任务 {task_uuid} 使用代理: {actual_proxy_url[:50]}...")

            # 更新任务的代理记录
            crud.update_registration_task(db, task_uuid, proxy=actual_proxy_url)

            # 创建邮箱服务
            service_type = EmailServiceType(email_service_type)
            settings = get_settings()

            # 优先使用数据库中配置的邮箱服务
            if email_service_id:
                from ...database.models import EmailService as EmailServiceModel
                db_service = db.query(EmailServiceModel).filter(
                    EmailServiceModel.id == email_service_id,
                    EmailServiceModel.enabled == True
                ).first()

                if db_service:
                    config = db_service.config.copy() if db_service.config else {}
                    # 更新任务关联的邮箱服务
                    crud.update_registration_task(db, task_uuid, email_service_id=db_service.id)
                    logger.info(f"使用数据库邮箱服务: {db_service.name} (ID: {db_service.id})")
                else:
                    raise ValueError(f"邮箱服务不存在或已禁用: {email_service_id}")
            else:
                # 使用默认配置或传入的配置
                if service_type == EmailServiceType.TEMPMAIL:
                    config = {
                        "base_url": settings.tempmail_base_url,
                        "timeout": settings.tempmail_timeout,
                        "max_retries": settings.tempmail_max_retries,
                        "proxy_url": actual_proxy_url,
                    }
                elif service_type == EmailServiceType.CUSTOM_DOMAIN:
                    # 检查数据库中是否有可用的自定义域名服务
                    from ...database.models import EmailService as EmailServiceModel
                    db_service = db.query(EmailServiceModel).filter(
                        EmailServiceModel.service_type == "custom_domain",
                        EmailServiceModel.enabled == True
                    ).order_by(EmailServiceModel.priority.asc()).first()

                    if db_service and db_service.config:
                        config = db_service.config.copy()
                        crud.update_registration_task(db, task_uuid, email_service_id=db_service.id)
                        logger.info(f"使用数据库自定义域名服务: {db_service.name}")
                    elif settings.custom_domain_base_url and settings.custom_domain_api_key:
                        config = {
                            "base_url": settings.custom_domain_base_url,
                            "api_key": settings.custom_domain_api_key.get_secret_value() if settings.custom_domain_api_key else "",
                            "proxy_url": actual_proxy_url,
                        }
                    else:
                        raise ValueError("没有可用的自定义域名邮箱服务，请先在设置中配置")
                elif service_type == EmailServiceType.OUTLOOK:
                    # 检查数据库中是否有可用的 Outlook 账户
                    from ...database.models import EmailService as EmailServiceModel, Account
                    # 获取所有启用的 Outlook 服务
                    outlook_services = db.query(EmailServiceModel).filter(
                        EmailServiceModel.service_type == "outlook",
                        EmailServiceModel.enabled == True
                    ).order_by(EmailServiceModel.priority.asc()).all()

                    if not outlook_services:
                        raise ValueError("没有可用的 Outlook 账户，请先在设置中导入账户")

                    # 找到一个未注册的 Outlook 账户
                    selected_service = None
                    for svc in outlook_services:
                        email = svc.config.get("email") if svc.config else None
                        if not email:
                            continue
                        # 检查是否已在 accounts 表中注册
                        existing = db.query(Account).filter(Account.email == email).first()
                        if not existing:
                            selected_service = svc
                            logger.info(f"选择未注册的 Outlook 账户: {email}")
                            break
                        else:
                            logger.info(f"跳过已注册的 Outlook 账户: {email}")

                    if selected_service and selected_service.config:
                        config = selected_service.config.copy()
                        crud.update_registration_task(db, task_uuid, email_service_id=selected_service.id)
                        logger.info(f"使用数据库 Outlook 账户: {selected_service.name}")
                    else:
                        raise ValueError("所有 Outlook 账户都已注册过 OpenAI 账号，请添加新的 Outlook 账户")
                else:
                    config = email_service_config or {}

            email_service = EmailServiceFactory.create(service_type, config)

            # 创建注册引擎 - 使用 TaskManager 的日志回调
            log_callback = task_manager.create_log_callback(task_uuid)

            engine = RegistrationEngine(
                email_service=email_service,
                proxy_url=actual_proxy_url,
                callback_logger=log_callback,
                task_uuid=task_uuid
            )

            # 执行注册
            result = engine.run()

            if result.success:
                # 更新代理使用时间
                update_proxy_usage(db, proxy_id)

                # 保存到数据库
                engine.save_to_database(result)

                # 更新任务状态
                crud.update_registration_task(
                    db, task_uuid,
                    status="completed",
                    completed_at=datetime.utcnow(),
                    result=result.to_dict()
                )

                # 更新 TaskManager 状态
                task_manager.update_status(task_uuid, "completed", email=result.email)

                logger.info(f"注册任务完成: {task_uuid}, 邮箱: {result.email}")
            else:
                # 更新任务状态为失败
                crud.update_registration_task(
                    db, task_uuid,
                    status="failed",
                    completed_at=datetime.utcnow(),
                    error_message=result.error_message
                )

                # 更新 TaskManager 状态
                task_manager.update_status(task_uuid, "failed", error=result.error_message)

                logger.warning(f"注册任务失败: {task_uuid}, 原因: {result.error_message}")

        except Exception as e:
            logger.error(f"注册任务异常: {task_uuid}, 错误: {e}")

            try:
                with get_db() as db:
                    crud.update_registration_task(
                        db, task_uuid,
                        status="failed",
                        completed_at=datetime.utcnow(),
                        error_message=str(e)
                    )

                # 更新 TaskManager 状态
                task_manager.update_status(task_uuid, "failed", error=str(e))
            except:
                pass


async def run_registration_task(task_uuid: str, email_service_type: str, proxy: Optional[str], email_service_config: Optional[dict], email_service_id: Optional[int] = None):
    """
    异步执行注册任务

    使用 run_in_executor 将同步任务放入线程池执行，避免阻塞主事件循环
    """
    loop = task_manager.get_loop()
    if loop is None:
        loop = asyncio.get_event_loop()
        task_manager.set_loop(loop)

    # 初始化 TaskManager 状态
    task_manager.update_status(task_uuid, "pending")
    task_manager.add_log(task_uuid, f"[系统] 任务 {task_uuid[:8]} 已加入队列")

    try:
        # 在线程池中执行同步任务
        await loop.run_in_executor(
            task_manager.executor,
            _run_sync_registration_task,
            task_uuid,
            email_service_type,
            proxy,
            email_service_config,
            email_service_id
        )
    except Exception as e:
        logger.error(f"线程池执行异常: {task_uuid}, 错误: {e}")
        task_manager.add_log(task_uuid, f"[错误] 线程池执行异常: {str(e)}")
        task_manager.update_status(task_uuid, "failed", error=str(e))


async def run_batch_registration(
    batch_id: str,
    task_uuids: List[str],
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    interval_min: int,
    interval_max: int
):
    """
    异步执行批量注册任务

    使用线程池执行每个注册任务，避免阻塞主事件循环
    """
    batch_tasks[batch_id] = {
        "total": len(task_uuids),
        "completed": 0,
        "success": 0,
        "failed": 0,
        "cancelled": False,
        "task_uuids": task_uuids,
        "current_index": 0
    }

    try:
        for i, task_uuid in enumerate(task_uuids):
            # 检查是否已取消
            if batch_tasks[batch_id]["cancelled"]:
                # 取消剩余任务
                with get_db() as db:
                    for remaining_uuid in task_uuids[i:]:
                        crud.update_registration_task(db, remaining_uuid, status="cancelled")
                logger.info(f"批量任务 {batch_id} 已取消")
                break

            batch_tasks[batch_id]["current_index"] = i

            # 运行单个注册任务（使用线程池）
            await run_registration_task(
                task_uuid, email_service_type, proxy, email_service_config, email_service_id
            )

            # 更新统计
            with get_db() as db:
                task = crud.get_registration_task(db, task_uuid)
                if task:
                    batch_tasks[batch_id]["completed"] += 1
                    if task.status == "completed":
                        batch_tasks[batch_id]["success"] += 1
                    elif task.status == "failed":
                        batch_tasks[batch_id]["failed"] += 1

            # 如果不是最后一个任务，等待随机间隔
            if i < len(task_uuids) - 1 and not batch_tasks[batch_id]["cancelled"]:
                wait_time = random.randint(interval_min, interval_max)
                logger.info(f"批量任务 {batch_id}: 等待 {wait_time} 秒后继续下一个任务")
                await asyncio.sleep(wait_time)

        logger.info(f"批量任务 {batch_id} 完成: 成功 {batch_tasks[batch_id]['success']}, 失败 {batch_tasks[batch_id]['failed']}")

    except Exception as e:
        logger.error(f"批量任务 {batch_id} 异常: {e}")
    finally:
        batch_tasks[batch_id]["finished"] = True


# ============== API Endpoints ==============

@router.post("/start", response_model=RegistrationTaskResponse)
async def start_registration(
    request: RegistrationTaskCreate,
    background_tasks: BackgroundTasks
):
    """
    启动注册任务

    - email_service_type: 邮箱服务类型 (tempmail, outlook, custom_domain)
    - proxy: 代理地址
    - email_service_config: 邮箱服务配置（outlook 需要提供账户信息）
    """
    # 验证邮箱服务类型
    try:
        EmailServiceType(request.email_service_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"无效的邮箱服务类型: {request.email_service_type}"
        )

    # 创建任务
    task_uuid = str(uuid.uuid4())

    with get_db() as db:
        task = crud.create_registration_task(
            db,
            task_uuid=task_uuid,
            proxy=request.proxy
        )

    # 在后台运行注册任务
    background_tasks.add_task(
        run_registration_task,
        task_uuid,
        request.email_service_type,
        request.proxy,
        request.email_service_config,
        request.email_service_id
    )

    return task_to_response(task)


@router.post("/batch", response_model=BatchRegistrationResponse)
async def start_batch_registration(
    request: BatchRegistrationRequest,
    background_tasks: BackgroundTasks
):
    """
    启动批量注册任务

    - count: 注册数量 (1-100)
    - email_service_type: 邮箱服务类型
    - proxy: 代理地址
    - interval_min: 最小间隔秒数
    - interval_max: 最大间隔秒数
    """
    # 验证参数
    if request.count < 1 or request.count > 100:
        raise HTTPException(status_code=400, detail="注册数量必须在 1-100 之间")

    try:
        EmailServiceType(request.email_service_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"无效的邮箱服务类型: {request.email_service_type}"
        )

    if request.interval_min < 0 or request.interval_max < request.interval_min:
        raise HTTPException(status_code=400, detail="间隔时间参数无效")

    # 创建批量任务
    batch_id = str(uuid.uuid4())
    task_uuids = []

    with get_db() as db:
        for _ in range(request.count):
            task_uuid = str(uuid.uuid4())
            task = crud.create_registration_task(
                db,
                task_uuid=task_uuid,
                proxy=request.proxy
            )
            task_uuids.append(task_uuid)

    # 获取所有任务
    with get_db() as db:
        tasks = [crud.get_registration_task(db, uuid) for uuid in task_uuids]

    # 在后台运行批量注册
    background_tasks.add_task(
        run_batch_registration,
        batch_id,
        task_uuids,
        request.email_service_type,
        request.proxy,
        request.email_service_config,
        request.email_service_id,
        request.interval_min,
        request.interval_max
    )

    return BatchRegistrationResponse(
        batch_id=batch_id,
        count=request.count,
        tasks=[task_to_response(t) for t in tasks if t]
    )


@router.get("/batch/{batch_id}")
async def get_batch_status(batch_id: str):
    """获取批量任务状态"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    return {
        "batch_id": batch_id,
        "total": batch["total"],
        "completed": batch["completed"],
        "success": batch["success"],
        "failed": batch["failed"],
        "current_index": batch["current_index"],
        "cancelled": batch["cancelled"],
        "finished": batch.get("finished", False),
        "progress": f"{batch['completed']}/{batch['total']}"
    }


@router.post("/batch/{batch_id}/cancel")
async def cancel_batch(batch_id: str):
    """取消批量任务"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    if batch.get("finished"):
        raise HTTPException(status_code=400, detail="批量任务已完成")

    batch["cancelled"] = True
    return {"success": True, "message": "批量任务取消请求已提交"}


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
):
    """获取任务列表"""
    with get_db() as db:
        query = db.query(RegistrationTask)

        if status:
            query = query.filter(RegistrationTask.status == status)

        total = query.count()
        offset = (page - 1) * page_size
        tasks = query.order_by(RegistrationTask.created_at.desc()).offset(offset).limit(page_size).all()

        return TaskListResponse(
            total=total,
            tasks=[task_to_response(t) for t in tasks]
        )


@router.get("/tasks/{task_uuid}", response_model=RegistrationTaskResponse)
async def get_task(task_uuid: str):
    """获取任务详情"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task_to_response(task)


@router.get("/tasks/{task_uuid}/logs")
async def get_task_logs(task_uuid: str):
    """获取任务日志"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        logs = task.logs or ""
        return {
            "task_uuid": task_uuid,
            "status": task.status,
            "logs": logs.split("\n") if logs else []
        }


@router.post("/tasks/{task_uuid}/cancel")
async def cancel_task(task_uuid: str):
    """取消任务"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        if task.status not in ["pending", "running"]:
            raise HTTPException(status_code=400, detail="任务已完成或已取消")

        task = crud.update_registration_task(db, task_uuid, status="cancelled")

        return {"success": True, "message": "任务已取消"}


@router.delete("/tasks/{task_uuid}")
async def delete_task(task_uuid: str):
    """删除任务"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        if task.status == "running":
            raise HTTPException(status_code=400, detail="无法删除运行中的任务")

        crud.delete_registration_task(db, task_uuid)

        return {"success": True, "message": "任务已删除"}


@router.get("/stats")
async def get_registration_stats():
    """获取注册统计信息"""
    with get_db() as db:
        from sqlalchemy import func

        # 按状态统计
        status_stats = db.query(
            RegistrationTask.status,
            func.count(RegistrationTask.id)
        ).group_by(RegistrationTask.status).all()

        # 今日注册数
        today = datetime.utcnow().date()
        today_count = db.query(func.count(RegistrationTask.id)).filter(
            func.date(RegistrationTask.created_at) == today
        ).scalar()

        return {
            "by_status": {status: count for status, count in status_stats},
            "today_count": today_count
        }


@router.get("/available-services")
async def get_available_email_services():
    """
    获取可用于注册的邮箱服务列表

    返回所有已启用的邮箱服务，包括：
    - tempmail: 临时邮箱（无需配置）
    - outlook: 已导入的 Outlook 账户
    - custom_domain: 已配置的自定义域名服务
    """
    from ...database.models import EmailService as EmailServiceModel
    from ...config.settings import get_settings

    settings = get_settings()
    result = {
        "tempmail": {
            "available": True,
            "count": 1,
            "services": [{
                "id": None,
                "name": "Tempmail.lol",
                "type": "tempmail",
                "description": "临时邮箱，自动创建"
            }]
        },
        "outlook": {
            "available": False,
            "count": 0,
            "services": []
        },
        "custom_domain": {
            "available": False,
            "count": 0,
            "services": []
        }
    }

    with get_db() as db:
        # 获取 Outlook 账户
        outlook_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "outlook",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in outlook_services:
            config = service.config or {}
            result["outlook"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "outlook",
                "has_oauth": bool(config.get("client_id") and config.get("refresh_token")),
                "priority": service.priority
            })

        result["outlook"]["count"] = len(outlook_services)
        result["outlook"]["available"] = len(outlook_services) > 0

        # 获取自定义域名服务
        custom_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "custom_domain",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in custom_services:
            config = service.config or {}
            result["custom_domain"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "custom_domain",
                "default_domain": config.get("default_domain"),
                "priority": service.priority
            })

        result["custom_domain"]["count"] = len(custom_services)
        result["custom_domain"]["available"] = len(custom_services) > 0

        # 如果数据库中没有自定义域名服务，检查 settings
        if not result["custom_domain"]["available"]:
            if settings.custom_domain_base_url and settings.custom_domain_api_key:
                result["custom_domain"]["available"] = True
                result["custom_domain"]["count"] = 1
                result["custom_domain"]["services"].append({
                    "id": None,
                    "name": "默认自定义域名服务",
                    "type": "custom_domain",
                    "from_settings": True
                })

    return result


# ============== Outlook 批量注册 API ==============

@router.get("/outlook-accounts", response_model=OutlookAccountsListResponse)
async def get_outlook_accounts_for_registration():
    """
    获取可用于注册的 Outlook 账户列表

    返回所有已启用的 Outlook 服务，并检查每个邮箱是否已在 accounts 表中注册
    """
    from ...database.models import EmailService as EmailServiceModel
    from ...database.models import Account

    with get_db() as db:
        # 获取所有启用的 Outlook 服务
        outlook_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "outlook",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        accounts = []
        registered_count = 0
        unregistered_count = 0

        for service in outlook_services:
            config = service.config or {}
            email = config.get("email") or service.name

            # 检查是否已注册（查询 accounts 表）
            existing_account = db.query(Account).filter(
                Account.email == email
            ).first()

            is_registered = existing_account is not None
            if is_registered:
                registered_count += 1
            else:
                unregistered_count += 1

            accounts.append(OutlookAccountForRegistration(
                id=service.id,
                email=email,
                name=service.name,
                has_oauth=bool(config.get("client_id") and config.get("refresh_token")),
                is_registered=is_registered,
                registered_account_id=existing_account.id if existing_account else None
            ))

        return OutlookAccountsListResponse(
            total=len(accounts),
            registered_count=registered_count,
            unregistered_count=unregistered_count,
            accounts=accounts
        )


def _run_sync_outlook_batch_registration(
    batch_id: str,
    service_ids: List[int],
    skip_registered: bool,
    proxy: Optional[str],
    interval_min: int,
    interval_max: int
):
    """
    在线程池中执行的同步 Outlook 批量注册任务
    """
    from ...database.models import EmailService as EmailServiceModel
    from ...database.models import Account

    # 初始化 TaskManager 批量任务
    task_manager.init_batch(batch_id, len(service_ids))

    # 兼容旧版 batch_tasks（用于 REST API 轮询降级）
    batch_tasks[batch_id] = {
        "total": len(service_ids),
        "completed": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "cancelled": False,
        "service_ids": service_ids,
        "current_index": 0,
        "logs": []
    }

    def add_batch_log(msg: str):
        """同时添加日志到两个系统"""
        batch_tasks[batch_id]["logs"].append(msg)
        task_manager.add_batch_log(batch_id, msg)

    def update_batch_status(**kwargs):
        """同时更新两个系统的状态"""
        for key, value in kwargs.items():
            if key in batch_tasks[batch_id]:
                batch_tasks[batch_id][key] = value
        task_manager.update_batch_status(batch_id, **kwargs)

    try:
        for i, service_id in enumerate(service_ids):
            # 检查是否已取消
            if task_manager.is_batch_cancelled(batch_id):
                add_batch_log(f"[取消] 批量任务已取消")
                update_batch_status(finished=True, status="cancelled")
                logger.info(f"Outlook 批量任务 {batch_id} 已取消")
                break

            update_batch_status(current_index=i)

            with get_db() as db:
                # 获取邮箱服务
                service = db.query(EmailServiceModel).filter(
                    EmailServiceModel.id == service_id
                ).first()

                if not service:
                    add_batch_log(f"[跳过] 服务 ID {service_id} 不存在")
                    update_batch_status(skipped=batch_tasks[batch_id]["skipped"] + 1,
                                       completed=batch_tasks[batch_id]["completed"] + 1)
                    continue

                config = service.config or {}
                email = config.get("email") or service.name

                # 检查是否已注册
                if skip_registered:
                    existing_account = db.query(Account).filter(
                        Account.email == email
                    ).first()

                    if existing_account:
                        add_batch_log(f"[跳过] {email} 已注册 (账号 ID: {existing_account.id})")
                        update_batch_status(skipped=batch_tasks[batch_id]["skipped"] + 1,
                                           completed=batch_tasks[batch_id]["completed"] + 1)
                        continue

                # 创建注册任务
                task_uuid = str(uuid.uuid4())
                task = crud.create_registration_task(
                    db,
                    task_uuid=task_uuid,
                    proxy=proxy,
                    email_service_id=service_id
                )

            add_batch_log(f"[注册] 开始注册 {email}...")

            # 同步执行注册任务
            _run_sync_registration_task(task_uuid, "outlook", proxy, None, service_id)

            # 更新统计
            with get_db() as db:
                task = crud.get_registration_task(db, task_uuid)
                if task:
                    new_completed = batch_tasks[batch_id]["completed"] + 1
                    new_success = batch_tasks[batch_id]["success"]
                    new_failed = batch_tasks[batch_id]["failed"]

                    if task.status == "completed":
                        new_success += 1
                        add_batch_log(f"[成功] {email} 注册成功")
                    elif task.status == "failed":
                        new_failed += 1
                        add_batch_log(f"[失败] {email} 注册失败: {task.error_message}")

                    update_batch_status(
                        completed=new_completed,
                        success=new_success,
                        failed=new_failed
                    )

            # 如果不是最后一个任务，等待随机间隔
            if i < len(service_ids) - 1 and not task_manager.is_batch_cancelled(batch_id):
                wait_time = random.randint(interval_min, interval_max)
                logger.info(f"Outlook 批量任务 {batch_id}: 等待 {wait_time} 秒后继续下一个任务")
                import time
                time.sleep(wait_time)

        # 完成批量任务
        if not task_manager.is_batch_cancelled(batch_id):
            add_batch_log(f"[完成] 批量任务完成！成功: {batch_tasks[batch_id]['success']}, 失败: {batch_tasks[batch_id]['failed']}, 跳过: {batch_tasks[batch_id]['skipped']}")
            update_batch_status(finished=True, status="completed")
            logger.info(f"Outlook 批量任务 {batch_id} 完成: 成功 {batch_tasks[batch_id]['success']}, 失败 {batch_tasks[batch_id]['failed']}, 跳过 {batch_tasks[batch_id]['skipped']}")

    except Exception as e:
        logger.error(f"Outlook 批量任务 {batch_id} 异常: {e}")
        add_batch_log(f"[错误] 批量任务异常: {str(e)}")
        update_batch_status(finished=True, status="failed")


async def run_outlook_batch_registration(
    batch_id: str,
    service_ids: List[int],
    skip_registered: bool,
    proxy: Optional[str],
    interval_min: int,
    interval_max: int
):
    """
    异步执行 Outlook 批量注册任务

    使用线程池执行，避免阻塞主事件循环
    """
    loop = task_manager.get_loop()
    if loop is None:
        loop = asyncio.get_event_loop()
        task_manager.set_loop(loop)

    # 在线程池中执行
    await loop.run_in_executor(
        task_manager.executor,
        _run_sync_outlook_batch_registration,
        batch_id,
        service_ids,
        skip_registered,
        proxy,
        interval_min,
        interval_max
    )


@router.post("/outlook-batch", response_model=OutlookBatchRegistrationResponse)
async def start_outlook_batch_registration(
    request: OutlookBatchRegistrationRequest,
    background_tasks: BackgroundTasks
):
    """
    启动 Outlook 批量注册任务

    - service_ids: 选中的 EmailService ID 列表
    - skip_registered: 是否自动跳过已注册邮箱（默认 True）
    - proxy: 代理地址
    - interval_min: 最小间隔秒数
    - interval_max: 最大间隔秒数
    """
    from ...database.models import EmailService as EmailServiceModel
    from ...database.models import Account

    # 验证参数
    if not request.service_ids:
        raise HTTPException(status_code=400, detail="请选择至少一个 Outlook 账户")

    if request.interval_min < 0 or request.interval_max < request.interval_min:
        raise HTTPException(status_code=400, detail="间隔时间参数无效")

    # 过滤掉已注册的邮箱
    actual_service_ids = request.service_ids
    skipped_count = 0

    if request.skip_registered:
        actual_service_ids = []
        with get_db() as db:
            for service_id in request.service_ids:
                service = db.query(EmailServiceModel).filter(
                    EmailServiceModel.id == service_id
                ).first()

                if not service:
                    continue

                config = service.config or {}
                email = config.get("email") or service.name

                # 检查是否已注册
                existing_account = db.query(Account).filter(
                    Account.email == email
                ).first()

                if existing_account:
                    skipped_count += 1
                else:
                    actual_service_ids.append(service_id)

    if not actual_service_ids:
        return OutlookBatchRegistrationResponse(
            batch_id="",
            total=len(request.service_ids),
            skipped=skipped_count,
            to_register=0,
            service_ids=[]
        )

    # 创建批量任务
    batch_id = str(uuid.uuid4())

    # 初始化批量任务状态
    batch_tasks[batch_id] = {
        "total": len(actual_service_ids),
        "completed": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "cancelled": False,
        "service_ids": actual_service_ids,
        "current_index": 0,
        "logs": [],
        "finished": False
    }

    # 在后台运行批量注册
    background_tasks.add_task(
        run_outlook_batch_registration,
        batch_id,
        actual_service_ids,
        request.skip_registered,
        request.proxy,
        request.interval_min,
        request.interval_max
    )

    return OutlookBatchRegistrationResponse(
        batch_id=batch_id,
        total=len(request.service_ids),
        skipped=skipped_count,
        to_register=len(actual_service_ids),
        service_ids=actual_service_ids
    )


@router.get("/outlook-batch/{batch_id}")
async def get_outlook_batch_status(batch_id: str):
    """获取 Outlook 批量任务状态"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    return {
        "batch_id": batch_id,
        "total": batch["total"],
        "completed": batch["completed"],
        "success": batch["success"],
        "failed": batch["failed"],
        "skipped": batch.get("skipped", 0),
        "current_index": batch["current_index"],
        "cancelled": batch["cancelled"],
        "finished": batch.get("finished", False),
        "logs": batch.get("logs", []),
        "progress": f"{batch['completed']}/{batch['total']}"
    }


@router.post("/outlook-batch/{batch_id}/cancel")
async def cancel_outlook_batch(batch_id: str):
    """取消 Outlook 批量任务"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    if batch.get("finished"):
        raise HTTPException(status_code=400, detail="批量任务已完成")

    # 同时更新两个系统的取消状态
    batch["cancelled"] = True
    task_manager.cancel_batch(batch_id)

    return {"success": True, "message": "批量任务取消请求已提交"}
