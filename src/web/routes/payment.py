"""
支付相关 API 路由
"""

import logging
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from ...database.session import get_db
from ...database.models import Account
from ...database import crud
from ...config.settings import get_settings
from .accounts import resolve_account_ids
from ..task_manager import task_manager
from ...core.openai.payment import (
    generate_plus_link,
    generate_team_link,
    open_url_incognito,
    check_subscription_status,
)

logger = logging.getLogger(__name__)
router = APIRouter()
subscription_batches: dict[str, dict] = {}


# ============== Pydantic Models ==============

class GenerateLinkRequest(BaseModel):
    account_id: int
    plan_type: str  # 'plus' or 'team'
    workspace_name: str = "MyTeam"
    price_interval: str = "month"
    seat_quantity: int = 5
    proxy: Optional[str] = None
    auto_open: bool = False  # 生成后是否自动无痕打开
    country: str = "SG"  # 计费国家，决定货币  # 生成后是否自动无痕打开


class OpenIncognitoRequest(BaseModel):
    url: str
    account_id: Optional[int] = None  # 可选，用于注入账号 cookie


class MarkSubscriptionRequest(BaseModel):
    subscription_type: str  # 'free' / 'plus' / 'team'


class BatchCheckSubscriptionRequest(BaseModel):
    ids: List[int] = []
    proxy: Optional[str] = None
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None


def _run_sync_check_subscription_task(task_uuid: str, account_id: int, request_proxy: Optional[str]):
    callback = task_manager.create_log_callback(task_uuid)
    task_manager.update_status(task_uuid, "running")
    callback("[系统] 订阅检测任务开始")
    try:
        with get_db() as db:
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                raise ValueError("账号不存在")
            proxy = request_proxy or get_settings().proxy_url
            if proxy:
                callback(f"[系统] 本次检测将使用代理: {proxy}")
            else:
                callback("[系统] 本次检测将直连")
            status = check_subscription_status(account, proxy)
            account.subscription_type = None if status == "free" else status
            account.subscription_at = datetime.utcnow() if status != "free" else account.subscription_at
            db.commit()
        callback(f"[成功] 订阅检测完成: {status}")
        task_manager.update_status(task_uuid, "completed", result={"account_id": account_id, "subscription_type": status})
    except Exception as exc:
        callback(f"[失败] {exc}")
        task_manager.update_status(task_uuid, "failed", error=str(exc))


async def run_check_subscription_task(task_uuid: str, account_id: int, request_proxy: Optional[str]):
    import asyncio
    loop = task_manager.get_loop()
    if loop is None:
        loop = asyncio.get_event_loop()
        task_manager.set_loop(loop)
    task_manager.update_status(task_uuid, "pending")
    task_manager.add_log(task_uuid, f"[系统] 订阅检测任务 {task_uuid[:8]} 已加入队列")
    await loop.run_in_executor(task_manager.executor, _run_sync_check_subscription_task, task_uuid, account_id, request_proxy)


async def run_batch_check_subscription(batch_id: str, account_ids: List[int], request_proxy: Optional[str]):
    import asyncio
    settings = get_settings()
    concurrency = max(1, min(5, int(getattr(settings, "registration_max_retries", 3) or 3)))
    task_manager.init_batch(batch_id, len(account_ids))
    subscription_batches[batch_id] = {
        "status": "running",
        "total": len(account_ids),
        "completed": 0,
        "success": 0,
        "failed": 0,
        "finished": False,
        "mode": "batch_subscription",
    }
    task_manager.add_batch_log(batch_id, f"[系统] 批量订阅检测开始，共 {len(account_ids)} 个账号，并发 {concurrency}")

    loop = task_manager.get_loop() or asyncio.get_event_loop()
    semaphore = asyncio.Semaphore(concurrency)

    async def worker(index: int, account_id: int):
        async with semaphore:
            prefix = f"[任务{index}]"
            task_manager.add_batch_log(batch_id, f"{prefix} 开始检测订阅 ID={account_id}")
            try:
                await loop.run_in_executor(task_manager.executor, _run_sync_check_subscription_task, f"batch-{batch_id}-{index}", account_id, request_proxy)
                with get_db() as db:
                    account = db.query(Account).filter(Account.id == account_id).first()
                    sub = (account.subscription_type if account else None) or 'free'
                subscription_batches[batch_id]["success"] += 1
                task_manager.add_batch_log(batch_id, f"{prefix} 检测完成 ID={account_id} | 订阅: {sub}")
            except Exception as exc:
                subscription_batches[batch_id]["failed"] += 1
                task_manager.add_batch_log(batch_id, f"{prefix} 检测异常 ID={account_id} | 原因: {exc}")
            finally:
                subscription_batches[batch_id]["completed"] += 1
                task_manager.update_batch_status(batch_id, completed=subscription_batches[batch_id]["completed"], success=subscription_batches[batch_id]["success"], failed=subscription_batches[batch_id]["failed"], current_index=subscription_batches[batch_id]["completed"])

    await asyncio.gather(*(worker(i, aid) for i, aid in enumerate(account_ids, start=1)))
    subscription_batches[batch_id]["status"] = "completed"
    subscription_batches[batch_id]["finished"] = True
    task_manager.update_batch_status(batch_id, status="completed", finished=True)
    task_manager.add_batch_log(batch_id, f"[系统] 批量订阅检测结束，成功 {subscription_batches[batch_id]['success']}，失败 {subscription_batches[batch_id]['failed']}")


# ============== 支付链接生成 ==============

@router.post("/generate-link")
def generate_payment_link(request: GenerateLinkRequest):
    """生成 Plus 或 Team 支付链接，可选自动无痕打开"""
    with get_db() as db:
        account = db.query(Account).filter(Account.id == request.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        proxy = request.proxy or get_settings().proxy_url

        try:
            if request.plan_type == "plus":
                link = generate_plus_link(account, proxy, country=request.country)
            elif request.plan_type == "team":
                link = generate_team_link(
                    account,
                    workspace_name=request.workspace_name,
                    price_interval=request.price_interval,
                    seat_quantity=request.seat_quantity,
                    proxy=proxy,
                    country=request.country,
                )
            else:
                raise HTTPException(status_code=400, detail="plan_type 必须为 plus 或 team")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"生成支付链接失败: {e}")
            raise HTTPException(status_code=500, detail=f"生成链接失败: {str(e)}")

    opened = False
    if request.auto_open and link:
        cookies_str = account.cookies if account else None
        opened = open_url_incognito(link, cookies_str)

    return {
        "success": True,
        "link": link,
        "plan_type": request.plan_type,
        "auto_opened": opened,
    }


@router.post("/open-incognito")
def open_browser_incognito(request: OpenIncognitoRequest):
    """后端以无痕模式打开指定 URL，可注入账号 cookie"""
    if not request.url:
        raise HTTPException(status_code=400, detail="URL 不能为空")

    cookies_str = None
    if request.account_id:
        with get_db() as db:
            account = db.query(Account).filter(Account.id == request.account_id).first()
            if account:
                cookies_str = account.cookies

    success = open_url_incognito(request.url, cookies_str)
    if success:
        return {"success": True, "message": "已在无痕模式打开浏览器"}
    return {"success": False, "message": "未找到可用的浏览器，请手动复制链接"}


# ============== 订阅状态 ==============

@router.get("/accounts/batch-check-subscription/{batch_id}")
def get_batch_check_subscription(batch_id: str):
    batch = subscription_batches.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    return batch


@router.get("/accounts/check-subscription/task/{task_uuid}")
def get_check_subscription_task(task_uuid: str):
    status = task_manager.get_status(task_uuid)
    if not status:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task_uuid": task_uuid, **status}


@router.post("/accounts/{account_id}/check-subscription")
async def check_subscription(account_id: int, background_tasks: BackgroundTasks, request: Optional[BatchCheckSubscriptionRequest] = None):
    task_uuid = __import__('uuid').uuid4().hex
    proxy = request.proxy if request else None
    background_tasks.add_task(run_check_subscription_task, task_uuid, account_id, proxy)
    return {"success": True, "task_uuid": task_uuid, "status": "pending"}


@router.post("/accounts/batch-check-subscription")
async def batch_check_subscription(request: BatchCheckSubscriptionRequest, background_tasks: BackgroundTasks):
    """批量检测账号订阅状态（后台任务化）。"""
    with get_db() as db:
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )
    batch_id = __import__('uuid').uuid4().hex
    background_tasks.add_task(run_batch_check_subscription, batch_id, ids, request.proxy)
    return {"success": True, "batch_id": batch_id, "count": len(ids)}


@router.post("/accounts/{account_id}/mark-subscription")
def mark_subscription(account_id: int, request: MarkSubscriptionRequest):
    """手动标记账号订阅类型"""
    allowed = ("free", "plus", "team")
    if request.subscription_type not in allowed:
        raise HTTPException(status_code=400, detail=f"subscription_type 必须为 {allowed}")

    with get_db() as db:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        account.subscription_type = None if request.subscription_type == "free" else request.subscription_type
        account.subscription_at = datetime.utcnow() if request.subscription_type != "free" else None
        db.commit()

    return {"success": True, "subscription_type": request.subscription_type}


