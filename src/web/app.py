"""
FastAPI 应用主文件
轻量级 Web UI，支持注册、账号管理、设置
"""

import logging
import sys
import secrets
import hmac
import hashlib
from datetime import datetime
from typing import Optional
from pathlib import Path
import inspect

from fastapi import FastAPI, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config.settings import get_settings
from .routes import api_router
from .routes.websocket import router as ws_router
from .task_manager import task_manager

logger = logging.getLogger(__name__)

# 获取项目根目录
# PyInstaller 打包后静态资源在 sys._MEIPASS，开发时在源码根目录
if getattr(sys, 'frozen', False):
    _RESOURCE_ROOT = Path(sys._MEIPASS)
else:
    _RESOURCE_ROOT = Path(__file__).parent.parent.parent

# 静态文件和模板目录
STATIC_DIR = _RESOURCE_ROOT / "static"
TEMPLATES_DIR = _RESOURCE_ROOT / "templates"


def _build_static_asset_version(static_dir: Path) -> str:
    """基于静态文件最后修改时间生成版本号，避免部署后浏览器继续使用旧缓存。"""
    latest_mtime = 0
    if static_dir.exists():
        for path in static_dir.rglob("*"):
            if path.is_file():
                latest_mtime = max(latest_mtime, int(path.stat().st_mtime))
    return str(latest_mtime or 1)


def _recover_interrupted_tasks():
    """启动时恢复中断的任务：标记批量任务为 interrupted，并恢复等待短信的手机验证。"""
    try:
        from ..database.session import get_db
        from ..database import crud

        # 1. 标记中断的批量任务
        with get_db() as db:
            interrupted = crud.get_interrupted_batch_tasks(db)
            for bt in interrupted:
                crud.update_batch_task(db, bt.batch_id, status="interrupted", finished=True)
                logger.warning(
                    "标记中断批量任务: batch_id=%s type=%s completed=%s/%s",
                    bt.batch_id, bt.batch_type, bt.completed, bt.total,
                )
        if interrupted:
            logger.info("已标记 %s 个中断的批量任务", len(interrupted))

        # 2. 恢复等待短信的手机验证
        with get_db() as db:
            pending = crud.get_pending_sms_verifications(db)
            if pending:
                logger.info("发现 %s 个等待短信中的手机验证记录，尝试恢复...", len(pending))
                _resume_phone_verifications(pending)
    except Exception as e:
        logger.warning("恢复中断任务时出错: %s", e)


def _resume_phone_verifications(attempts: list):
    """尝试恢复等待短信中的手机验证任务。"""
    import threading
    from ..core.sms import SMSProviderConfig, get_sms_provider
    from ..config.settings import get_settings, normalize_sms_provider_name

    settings = get_settings()
    provider_name = normalize_sms_provider_name(getattr(settings, "sms_provider", "herosms") or "herosms")

    # 获取短信平台 API Key
    try:
        from ..database.session import get_db
        from ..database import crud
        db_key = {
            "herosms": "herosms.api_key",
            "smsbower": "smsbower.api_key",
            "5sim": "fivesim.api_key",
        }.get(provider_name, "herosms.api_key")
        with get_db() as db:
            ks = crud.get_setting(db, db_key)
            api_key = str(ks.value or "").strip() if ks else ""
        if not api_key:
            settings_field = {"herosms": "herosms_api_key", "smsbower": "smsbower_api_key", "5sim": "fivesim_api_key"}.get(provider_name, "herosms_api_key")
            secret = getattr(settings, settings_field, None)
            if secret and hasattr(secret, "get_secret_value"):
                api_key = secret.get_secret_value()
    except Exception:
        api_key = ""

    if not api_key:
        logger.warning("未配置短信平台 API Key，跳过手机验证恢复")
        return

    def _resume_one(attempt):
        try:
            from ..database.session import get_db
            from ..database import crud
            activation_id = attempt.activation_id
            if not activation_id:
                return
            timeout = max(10, min(300, int(attempt.wait_timeout_seconds or 150)))
            poll_interval = 3

            cfg = SMSProviderConfig(
                api_key=api_key,
                provider=attempt.sms_provider or provider_name,
                service=attempt.service or "dr",
                country=int(attempt.country or 187),
                country_key=str(attempt.country_key or ""),
                timeout=30,
            )
            client = get_sms_provider(cfg)

            logger.info("恢复手机验证: activation=%s phone=%s", activation_id, attempt.phone_number)
            code = client.wait_for_code(
                activation_id,
                timeout=min(timeout, 180),
                poll_interval=poll_interval,
            )
            if code:
                with get_db() as db2:
                    crud.update_phone_attempt_stage(
                        db2,
                        int(attempt.id),
                        "sms_received",
                        sms_code=code,
                        sms_received_at=datetime.utcnow(),
                    )
                logger.info("恢复成功，收到验证码: activation=%s", activation_id)
            else:
                with get_db() as db:
                    crud.update_phone_attempt_stage(
                        db,
                        int(attempt.id),
                        "failed",
                        failure_stage="wait_sms_code",
                        error_code="sms_code_timeout_recovery",
                        error_message="恢复后等待短信验证码超时",
                        invalid=True,
                    )
                logger.warning("恢复失败，短信超时: activation=%s", activation_id)
        except Exception as e:
            logger.warning("恢复手机验证失败 activation=%s: %s", getattr(attempt, "activation_id", None), e)

    for attempt in attempts:
        t = threading.Thread(target=_resume_one, args=(attempt,), daemon=True, name=f"sms_resume_{attempt.id}")
        t.start()
    logger.info("已启动 %s 个手机验证恢复线程", len(attempts))


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="OpenAI/Codex CLI 自动注册系统 Web UI",
        docs_url="/api/docs" if settings.debug else None,
        redoc_url="/api/redoc" if settings.debug else None,
    )

    # CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 挂载静态文件
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
        logger.info(f"静态文件目录: {STATIC_DIR}")
    else:
        # 创建静态目录
        STATIC_DIR.mkdir(parents=True, exist_ok=True)
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
        logger.info(f"创建静态文件目录: {STATIC_DIR}")

    # 创建模板目录
    if not TEMPLATES_DIR.exists():
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"创建模板目录: {TEMPLATES_DIR}")

    # 注册 API 路由
    app.include_router(api_router, prefix="/api")

    # 注册 WebSocket 路由
    app.include_router(ws_router, prefix="/api")

    # 模板引擎
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["static_version"] = _build_static_asset_version(STATIC_DIR)

    def _render_template(name: str, request: Request, context: Optional[dict] = None, **kwargs):
        ctx = {"request": request}
        if context:
            ctx.update(context)
        try:
            params = list(inspect.signature(templates.TemplateResponse).parameters.keys())
            if params and params[0] == "request":
                return templates.TemplateResponse(request, name, ctx, **kwargs)
        except Exception:
            pass
        return templates.TemplateResponse(name, ctx, **kwargs)

    def _auth_token(password: str) -> str:
        secret = get_settings().webui_secret_key.get_secret_value().encode("utf-8")
        return hmac.new(secret, password.encode("utf-8"), hashlib.sha256).hexdigest()

    def _is_authenticated(request: Request) -> bool:
        cookie = request.cookies.get("webui_auth")
        expected = _auth_token(get_settings().webui_access_password.get_secret_value())
        return bool(cookie) and secrets.compare_digest(cookie, expected)

    def _redirect_to_login(request: Request) -> RedirectResponse:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: Optional[str] = "/"):
        """登录页面"""
        return _render_template(
            "login.html",
            request,
            {"error": "", "next": next or "/"},
        )

    @app.post("/login")
    async def login_submit(request: Request, password: str = Form(...), next: Optional[str] = "/"):
        """处理登录提交"""
        expected = get_settings().webui_access_password.get_secret_value()
        if not secrets.compare_digest(password, expected):
            return _render_template(
                "login.html",
                request,
                {"error": "密码错误", "next": next or "/"},
                status_code=401,
            )

        response = RedirectResponse(url=next or "/", status_code=302)
        response.set_cookie("webui_auth", _auth_token(expected), httponly=True, samesite="lax")
        return response

    @app.get("/logout")
    async def logout(request: Request, next: Optional[str] = "/login"):
        """退出登录"""
        response = RedirectResponse(url=next or "/login", status_code=302)
        response.delete_cookie("webui_auth")
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """首页 - 注册页面"""
        if not _is_authenticated(request):
            return _redirect_to_login(request)
        return _render_template("index.html", request)

    @app.get("/accounts", response_class=HTMLResponse)
    async def accounts_page(request: Request):
        """账号管理页面"""
        if not _is_authenticated(request):
            return _redirect_to_login(request)
        return _render_template("accounts.html", request)

    @app.get("/email-services", response_class=HTMLResponse)
    async def email_services_page(request: Request):
        """邮箱服务管理页面"""
        if not _is_authenticated(request):
            return _redirect_to_login(request)
        return _render_template("email_services.html", request)

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        """设置页面"""
        if not _is_authenticated(request):
            return _redirect_to_login(request)
        return _render_template("settings.html", request)

    @app.get("/payment", response_class=HTMLResponse)
    async def payment_page(request: Request):
        """支付页面"""
        return _render_template("payment.html", request)

    @app.on_event("startup")
    async def startup_event():
        """应用启动事件"""
        import asyncio
        from ..database.init_db import initialize_database

        # 确保数据库已初始化（reload 模式下子进程也需要初始化）
        try:
            initialize_database()
        except Exception as e:
            logger.warning(f"数据库初始化: {e}")

        # 设置 TaskManager 的事件循环
        loop = asyncio.get_event_loop()
        task_manager.set_loop(loop)

        # 标记中断的批量任务并尝试恢复 waiting_sms 的手机验证
        _recover_interrupted_tasks()

        logger.info("=" * 50)
        logger.info(f"{settings.app_name} v{settings.app_version} 启动中...")
        logger.info(f"调试模式: {settings.debug}")
        logger.info(f"数据库: {settings.database_url}")
        logger.info("=" * 50)

    @app.on_event("shutdown")
    async def shutdown_event():
        """应用关闭事件"""
        import os as _os
        import traceback as _tb
        pid = _os.getpid()
        ppid = _os.getppid()
        stack = ''.join(_tb.format_stack())
        logger.warning(
            "应用关闭 | PID=%s PPID=%s | 调用栈:\n%s",
            pid, ppid, stack[:2000]
        )
        # 同时写入 stderr 确保容器日志可见
        print(f"[SHUTDOWN] FastAPI shutdown PID={pid} PPID={ppid}", flush=True)

    return app


# 创建全局应用实例
app = create_app()
