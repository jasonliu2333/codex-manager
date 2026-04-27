"""
设置 API 路由
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...config.settings import get_database_url, get_settings, update_settings
from ...core.registration_flow_templates import get_registration_flow_templates, normalize_flow_template
from ...database import crud
from ...database.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


HEROSMS_COUNTRY_ZH = {
    "Afghanistan": "阿富汗", "Albania": "阿尔巴尼亚", "Algeria": "阿尔及利亚",
    "Angola": "安哥拉", "Argentina": "阿根廷", "Armenia": "亚美尼亚",
    "Australia": "澳大利亚", "Austria": "奥地利", "Azerbaijan": "阿塞拜疆",
    "Bahrain": "巴林", "Bangladesh": "孟加拉国", "Belarus": "白俄罗斯",
    "Belgium": "比利时", "Bolivia": "玻利维亚", "Brazil": "巴西",
    "Bulgaria": "保加利亚", "Cambodia": "柬埔寨", "Cameroon": "喀麦隆",
    "Canada": "加拿大", "Chile": "智利", "China": "中国", "Colombia": "哥伦比亚",
    "Croatia": "克罗地亚", "Cyprus": "塞浦路斯", "Czech": "捷克",
    "Czech Republic": "捷克", "Denmark": "丹麦", "Ecuador": "厄瓜多尔",
    "Egypt": "埃及", "Estonia": "爱沙尼亚", "Ethiopia": "埃塞俄比亚",
    "Finland": "芬兰", "France": "法国", "Georgia": "格鲁吉亚",
    "Germany": "德国", "Ghana": "加纳", "Greece": "希腊", "Hong Kong": "中国香港",
    "Hungary": "匈牙利", "India": "印度", "Indonesia": "印度尼西亚",
    "Iran": "伊朗", "Iraq": "伊拉克", "Ireland": "爱尔兰", "Israel": "以色列",
    "Italy": "意大利", "Japan": "日本", "Jordan": "约旦", "Kazakhstan": "哈萨克斯坦",
    "Kenya": "肯尼亚", "Kuwait": "科威特", "Kyrgyzstan": "吉尔吉斯斯坦",
    "Laos": "老挝", "Latvia": "拉脱维亚", "Lithuania": "立陶宛",
    "Malaysia": "马来西亚", "Mexico": "墨西哥", "Moldova": "摩尔多瓦",
    "Morocco": "摩洛哥", "Myanmar": "缅甸", "Netherlands": "荷兰",
    "New Zealand": "新西兰", "Nigeria": "尼日利亚", "Norway": "挪威",
    "Pakistan": "巴基斯坦", "Paraguay": "巴拉圭", "Peru": "秘鲁",
    "Philippines": "菲律宾", "Poland": "波兰", "Portugal": "葡萄牙",
    "Romania": "罗马尼亚", "Russia": "俄罗斯", "Saudi Arabia": "沙特阿拉伯",
    "Singapore": "新加坡", "Slovakia": "斯洛伐克", "Slovenia": "斯洛文尼亚",
    "South Africa": "南非", "South Korea": "韩国", "Spain": "西班牙",
    "Sri Lanka": "斯里兰卡", "Sweden": "瑞典", "Switzerland": "瑞士",
    "Taiwan": "中国台湾", "Tajikistan": "塔吉克斯坦", "Tanzania": "坦桑尼亚",
    "Thailand": "泰国", "Turkey": "土耳其", "UAE": "阿联酋",
    "Ukraine": "乌克兰", "United Arab Emirates": "阿联酋",
    "United Kingdom": "英国", "England": "英国", "UK": "英国",
    "United States": "美国", "USA": "美国", "Uruguay": "乌拉圭",
    "Uzbekistan": "乌兹别克斯坦", "Venezuela": "委内瑞拉", "Vietnam": "越南",
    "Yemen": "也门", "Zimbabwe": "津巴布韦",
}


def _get_saved_herosms_api_key() -> str:
    """优先从数据库直接读取 HeroSMS API Key，避免单例配置缓存不一致。"""
    try:
        with get_db() as db:
            setting = crud.get_setting(db, "herosms.api_key")
            value = str(setting.value or "").strip() if setting else ""
            if value:
                return value
    except Exception:
        pass
    try:
        settings = get_settings()
        if settings.herosms_api_key:
            return settings.herosms_api_key.get_secret_value().strip()
    except Exception:
        pass
    return ""


def _parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _parse_float(value, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _load_herosms_settings_from_db() -> dict:
    """
    HeroSMS 设置直接从数据库读取，避免单例缓存或初始化时序导致页面回退默认值。
    """
    settings = get_settings()
    defaults = {
        "enabled": bool(getattr(settings, "herosms_enabled", False)),
        "has_api_key": bool(_get_saved_herosms_api_key()),
        "service": str(getattr(settings, "herosms_service", "dr") or "dr"),
        "country": int(getattr(settings, "herosms_country", 187) or 187),
        "max_price": float(getattr(settings, "herosms_max_price", -1) or -1),
        "proxy": str(getattr(settings, "herosms_proxy", "") or ""),
        "timeout": int(getattr(settings, "herosms_timeout", 30) or 30),
        "verify_timeout": int(getattr(settings, "herosms_verify_timeout", 180) or 180),
        "poll_interval": int(getattr(settings, "herosms_poll_interval", 3) or 3),
        "lowest_price_first": bool(getattr(settings, "herosms_lowest_price_first", True)),
        "max_number_attempts": int(getattr(settings, "herosms_max_number_attempts", 1) or 1),
        "target_number_index": int(getattr(settings, "herosms_target_number_index", 1) or 1),
        "price_relax_enabled": bool(getattr(settings, "herosms_price_relax_enabled", True)),
        "price_relax_max_multiplier": int(getattr(settings, "herosms_price_relax_max_multiplier", 5) or 5),
        "reuse_enabled": bool(getattr(settings, "herosms_reuse_enabled", False)),
        "reuse_max_uses": int(getattr(settings, "herosms_reuse_max_uses", 2) or 2),
    }
    db_key_map = {
        "enabled": ("herosms.enabled", lambda v: _parse_bool(v, defaults["enabled"])),
        "service": ("herosms.service", lambda v: str(v).strip() or defaults["service"]),
        "country": ("herosms.country", lambda v: _parse_int(v, defaults["country"])),
        "max_price": ("herosms.max_price", lambda v: _parse_float(v, defaults["max_price"])),
        "proxy": ("herosms.proxy", lambda v: str(v or "").strip()),
        "timeout": ("herosms.timeout", lambda v: _parse_int(v, defaults["timeout"])),
        "verify_timeout": ("herosms.verify_timeout", lambda v: _parse_int(v, defaults["verify_timeout"])),
        "poll_interval": ("herosms.poll_interval", lambda v: _parse_int(v, defaults["poll_interval"])),
        "lowest_price_first": ("herosms.lowest_price_first", lambda v: _parse_bool(v, defaults["lowest_price_first"])),
        "max_number_attempts": ("herosms.max_number_attempts", lambda v: _parse_int(v, defaults["max_number_attempts"])),
        "target_number_index": ("herosms.target_number_index", lambda v: _parse_int(v, defaults["target_number_index"])),
        "price_relax_enabled": ("herosms.price_relax_enabled", lambda v: _parse_bool(v, defaults["price_relax_enabled"])),
        "price_relax_max_multiplier": ("herosms.price_relax_max_multiplier", lambda v: _parse_int(v, defaults["price_relax_max_multiplier"])),
        "reuse_enabled": ("herosms.reuse_enabled", lambda v: _parse_bool(v, defaults["reuse_enabled"])),
        "reuse_max_uses": ("herosms.reuse_max_uses", lambda v: _parse_int(v, defaults["reuse_max_uses"])),
    }
    try:
        with get_db() as db:
            for field, (db_key, parser) in db_key_map.items():
                setting = crud.get_setting(db, db_key)
                if setting and setting.value not in (None, ""):
                    defaults[field] = parser(setting.value)
    except Exception as exc:
        logger.warning("直接读取 HeroSMS 设置失败，回退到缓存配置: %s", exc)
    return defaults


def _get_saved_dynamic_proxy_api_key() -> str:
    """优先从数据库直接读取动态代理 API Key。"""
    try:
        with get_db() as db:
            setting = crud.get_setting(db, "proxy.dynamic_api_key")
            value = str(setting.value or "").strip() if setting else ""
            if value:
                return value
    except Exception:
        pass
    try:
        settings = get_settings()
        secret = getattr(settings, "proxy_dynamic_api_key", None)
        if secret:
            return secret.get_secret_value().strip() if hasattr(secret, "get_secret_value") else str(secret).strip()
    except Exception:
        pass
    return ""


def _load_proxy_settings_from_db() -> dict:
    """
    代理设置直接从数据库读取，避免页面显示依赖内存缓存。
    """
    settings = get_settings()
    defaults = {
        "enabled": bool(getattr(settings, "proxy_enabled", False)),
        "type": str(getattr(settings, "proxy_type", "http") or "http"),
        "host": str(getattr(settings, "proxy_host", "127.0.0.1") or "127.0.0.1"),
        "port": int(getattr(settings, "proxy_port", 7890) or 7890),
        "username": getattr(settings, "proxy_username", None),
        "has_password": bool(getattr(settings, "proxy_password", None)),
        "dynamic_enabled": bool(getattr(settings, "proxy_dynamic_enabled", False)),
        "dynamic_api_url": str(getattr(settings, "proxy_dynamic_api_url", "") or ""),
        "dynamic_api_key_header": str(getattr(settings, "proxy_dynamic_api_key_header", "X-API-Key") or "X-API-Key"),
        "dynamic_result_field": str(getattr(settings, "proxy_dynamic_result_field", "") or ""),
        "refresh_use_proxy": bool(getattr(settings, "proxy_refresh_use_proxy", False)),
        "validate_use_proxy": bool(getattr(settings, "proxy_validate_use_proxy", False)),
        "has_dynamic_api_key": bool(_get_saved_dynamic_proxy_api_key()),
    }
    db_key_map = {
        "enabled": ("proxy.enabled", lambda v: _parse_bool(v, defaults["enabled"])),
        "type": ("proxy.type", lambda v: str(v).strip() or defaults["type"]),
        "host": ("proxy.host", lambda v: str(v).strip() or defaults["host"]),
        "port": ("proxy.port", lambda v: _parse_int(v, defaults["port"])),
        "username": ("proxy.username", lambda v: str(v).strip() or None),
        "dynamic_enabled": ("proxy.dynamic_enabled", lambda v: _parse_bool(v, defaults["dynamic_enabled"])),
        "dynamic_api_url": ("proxy.dynamic_api_url", lambda v: str(v).strip()),
        "dynamic_api_key_header": ("proxy.dynamic_api_key_header", lambda v: str(v).strip() or defaults["dynamic_api_key_header"]),
        "dynamic_result_field": ("proxy.dynamic_result_field", lambda v: str(v).strip()),
        "refresh_use_proxy": ("proxy.refresh_use_proxy", lambda v: _parse_bool(v, defaults["refresh_use_proxy"])),
        "validate_use_proxy": ("proxy.validate_use_proxy", lambda v: _parse_bool(v, defaults["validate_use_proxy"])),
    }
    try:
        with get_db() as db:
            for field, (db_key, parser) in db_key_map.items():
                setting = crud.get_setting(db, db_key)
                if setting and setting.value not in (None, ""):
                    defaults[field] = parser(setting.value)
            password_setting = crud.get_setting(db, "proxy.password")
            defaults["has_password"] = bool(str(password_setting.value or "").strip()) if password_setting else defaults["has_password"]
    except Exception as exc:
        logger.warning("直接读取代理设置失败，回退到缓存配置: %s", exc)
    return defaults


def _build_proxy_runtime_diagnostics() -> dict:
    database_url = get_database_url()
    database_path = database_url
    if isinstance(database_url, str) and database_url.startswith("sqlite:///"):
        database_path = database_url.replace("sqlite:///", "", 1)
    return {
        "settings_source": "database",
        "database_url": database_url,
        "database_path": database_path,
        "has_dynamic_api_key": bool(_get_saved_dynamic_proxy_api_key()),
        "has_static_proxy_password": _load_proxy_settings_from_db().get("has_password", False),
    }


def _build_proxy_test_mapping(proxy_url: str, *, include_https: bool) -> dict:
    proxies = {"http": proxy_url}
    if include_https:
        proxies["https"] = proxy_url
    return proxies


def _test_proxy_http_basic(proxy_url: str) -> dict:
    import time
    from curl_cffi import requests as cffi_requests

    start = time.time()
    resp = cffi_requests.get(
        "http://api.ipify.org?format=json",
        proxies=_build_proxy_test_mapping(proxy_url, include_https=False),
        timeout=10,
        impersonate="chrome110",
    )
    elapsed = round((time.time() - start) * 1000)
    if resp.status_code != 200:
        return {"success": False, "message": f"HTTP 代理测试失败: HTTP {resp.status_code}"}
    ip = ""
    try:
        ip = resp.json().get("ip", "")
    except Exception:
        pass
    return {"success": True, "ip": ip, "response_time": elapsed, "message": f"HTTP 代理可用，出口 IP: {ip or 'unknown'}，响应时间: {elapsed}ms"}


def _test_proxy_https_openai(proxy_url: str) -> dict:
    from curl_cffi import requests as cffi_requests
    try:
        resp = cffi_requests.get(
            "https://auth.openai.com/",
            proxies=_build_proxy_test_mapping(proxy_url, include_https=True),
            timeout=10,
            impersonate="chrome110",
            allow_redirects=False,
        )
        return {"success": True, "status_code": resp.status_code, "message": f"HTTPS CONNECT 可用（auth.openai.com 返回 {resp.status_code}）"}
    except Exception as exc:
        return {"success": False, "message": f"HTTPS CONNECT 不可用: {exc}"}


# ============== Pydantic Models ==============

class SettingItem(BaseModel):
    """设置项"""
    key: str
    value: str
    description: Optional[str] = None
    category: str = "general"


class SettingUpdateRequest(BaseModel):
    """设置更新请求"""
    value: str


class ProxySettings(BaseModel):
    """代理设置"""
    enabled: bool = False
    type: str = "http"  # http, socks5
    host: str = "127.0.0.1"
    port: int = 7890
    username: Optional[str] = None
    password: Optional[str] = None


class RegistrationSettings(BaseModel):
    """注册设置"""
    max_retries: int = 3
    timeout: int = 120
    default_password_length: int = 12
    flow_template: str = "default"
    sleep_min: int = 5
    sleep_max: int = 30


class WebUISettings(BaseModel):
    """Web UI 设置"""
    host: Optional[str] = None
    port: Optional[int] = None
    debug: Optional[bool] = None
    access_password: Optional[str] = None


class AllSettings(BaseModel):
    """所有设置"""
    proxy: ProxySettings
    registration: RegistrationSettings
    webui: WebUISettings


# ============== API Endpoints ==============

@router.get("")
async def get_all_settings():
    """获取所有设置"""
    settings = get_settings()
    herosms_settings = _load_herosms_settings_from_db()
    proxy_settings = _load_proxy_settings_from_db()

    return {
        "proxy": {
            **proxy_settings,
            "diagnostics": _build_proxy_runtime_diagnostics(),
        },
        "registration": {
            "max_retries": settings.registration_max_retries,
            "timeout": settings.registration_timeout,
            "default_password_length": settings.registration_default_password_length,
            "flow_template": normalize_flow_template(settings.registration_flow_template),
            "templates": get_registration_flow_templates(),
            "sleep_min": settings.registration_sleep_min,
            "sleep_max": settings.registration_sleep_max,
        },
        "webui": {
            "host": settings.webui_host,
            "port": settings.webui_port,
            "debug": settings.debug,
            "has_access_password": bool(settings.webui_access_password and settings.webui_access_password.get_secret_value()),
        },
        "tempmail": {
            "base_url": settings.tempmail_base_url,
            "timeout": settings.tempmail_timeout,
            "max_retries": settings.tempmail_max_retries,
        },
        "email_code": {
            "timeout": settings.email_code_timeout,
            "poll_interval": settings.email_code_poll_interval,
        },
        "herosms": herosms_settings,
    }


@router.get("/proxy/dynamic")
async def get_dynamic_proxy_settings():
    """获取动态代理设置"""
    proxy_settings = _load_proxy_settings_from_db()
    return {
        "enabled": proxy_settings["dynamic_enabled"],
        "api_url": proxy_settings["dynamic_api_url"],
        "api_key_header": proxy_settings["dynamic_api_key_header"],
        "result_field": proxy_settings["dynamic_result_field"],
        "refresh_use_proxy": proxy_settings["refresh_use_proxy"],
        "validate_use_proxy": proxy_settings["validate_use_proxy"],
        "has_api_key": proxy_settings["has_dynamic_api_key"],
        "diagnostics": _build_proxy_runtime_diagnostics(),
    }


class DynamicProxySettings(BaseModel):
    """动态代理设置"""
    enabled: bool = False
    api_url: str = ""
    api_key: Optional[str] = None
    api_key_header: str = "X-API-Key"
    result_field: str = ""
    refresh_use_proxy: bool = False
    validate_use_proxy: bool = False


@router.post("/proxy/dynamic")
async def update_dynamic_proxy_settings(request: DynamicProxySettings):
    """更新动态代理设置"""
    update_dict = {
        "proxy_dynamic_enabled": request.enabled,
        "proxy_dynamic_api_url": request.api_url,
        "proxy_dynamic_api_key_header": request.api_key_header,
        "proxy_dynamic_result_field": request.result_field,
        "proxy_refresh_use_proxy": request.refresh_use_proxy,
        "proxy_validate_use_proxy": request.validate_use_proxy,
    }
    if request.api_key is not None:
        update_dict["proxy_dynamic_api_key"] = request.api_key

    update_settings(**update_dict)
    return {"success": True, "message": "动态代理设置已更新"}


@router.post("/proxy/dynamic/test")
async def test_dynamic_proxy(request: DynamicProxySettings):
    """测试动态代理 API"""
    from ...core.dynamic_proxy import fetch_dynamic_proxy

    if not request.api_url:
        raise HTTPException(status_code=400, detail="请填写动态代理 API 地址")

    # 若未传入 api_key，使用已保存的
    api_key = request.api_key or ""
    if not api_key:
        api_key = _get_saved_dynamic_proxy_api_key()

    proxy_url = fetch_dynamic_proxy(
        api_url=request.api_url,
        api_key=api_key,
        api_key_header=request.api_key_header,
        result_field=request.result_field,
    )

    if not proxy_url:
        return {"success": False, "message": "动态代理 API 返回为空或请求失败"}

    # 先按服务商文档测试 HTTP 代理基础可用性，再额外测试是否适合 OpenAI HTTPS。
    try:
        basic = _test_proxy_http_basic(proxy_url)
        if not basic.get("success"):
            return {"success": False, "proxy_url": proxy_url, "message": basic.get("message", "HTTP 代理测试失败")}
        https_test = _test_proxy_https_openai(proxy_url)
        return {
            "success": True,
            "proxy_url": proxy_url,
            "ip": basic.get("ip", ""),
            "response_time": basic.get("response_time"),
            "https_openai_ok": bool(https_test.get("success")),
            "https_openai_message": https_test.get("message", ""),
            "message": basic.get("message", "动态代理 HTTP 可用"),
        }
    except Exception as e:
        return {"success": False, "proxy_url": proxy_url, "message": f"代理连接失败: {e}"}


@router.get("/registration")
async def get_registration_settings():
    """获取注册设置"""
    settings = get_settings()

    return {
        "max_retries": settings.registration_max_retries,
        "timeout": settings.registration_timeout,
        "default_password_length": settings.registration_default_password_length,
        "flow_template": normalize_flow_template(settings.registration_flow_template),
        "templates": get_registration_flow_templates(),
        "sleep_min": settings.registration_sleep_min,
        "sleep_max": settings.registration_sleep_max,
    }


@router.post("/registration")
async def update_registration_settings(request: RegistrationSettings):
    """更新注册设置"""
    update_settings(
        registration_max_retries=request.max_retries,
        registration_timeout=request.timeout,
        registration_default_password_length=request.default_password_length,
        registration_flow_template=normalize_flow_template(request.flow_template),
        registration_sleep_min=request.sleep_min,
        registration_sleep_max=request.sleep_max,
    )

    return {"success": True, "message": "注册设置已更新"}


@router.post("/webui")
async def update_webui_settings(request: WebUISettings):
    """更新 Web UI 设置"""
    update_dict = {}
    if request.host is not None:
        update_dict["webui_host"] = request.host
    if request.port is not None:
        update_dict["webui_port"] = request.port
    if request.debug is not None:
        update_dict["debug"] = request.debug
    if request.access_password:
        update_dict["webui_access_password"] = request.access_password

    update_settings(**update_dict)
    return {"success": True, "message": "Web UI 设置已更新"}


@router.get("/database")
async def get_database_info():
    """获取数据库信息"""
    settings = get_settings()

    import os
    from pathlib import Path

    db_path = settings.database_url
    if db_path.startswith("sqlite:///"):
        db_path = db_path[10:]

    db_file = Path(db_path) if os.path.isabs(db_path) else Path(db_path)
    db_size = db_file.stat().st_size if db_file.exists() else 0

    with get_db() as db:
        from ...database.models import Account, EmailService, RegistrationTask

        account_count = db.query(Account).count()
        service_count = db.query(EmailService).count()
        task_count = db.query(RegistrationTask).count()

    return {
        "database_url": settings.database_url,
        "database_size_bytes": db_size,
        "database_size_mb": round(db_size / (1024 * 1024), 2),
        "accounts_count": account_count,
        "email_services_count": service_count,
        "tasks_count": task_count,
    }


@router.post("/database/backup")
async def backup_database():
    """备份数据库"""
    import shutil
    from datetime import datetime

    settings = get_settings()

    db_path = settings.database_url
    if db_path.startswith("sqlite:///"):
        db_path = db_path[10:]

    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="数据库文件不存在")

    # 创建备份目录
    from pathlib import Path as FilePath
    backup_dir = FilePath(db_path).parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    # 生成备份文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"database_backup_{timestamp}.db"

    # 复制数据库文件
    shutil.copy2(db_path, backup_path)

    return {
        "success": True,
        "message": "数据库备份成功",
        "backup_path": str(backup_path)
    }


@router.post("/database/cleanup")
async def cleanup_database(
    days: int = 30,
    keep_failed: bool = True
):
    """清理过期数据"""
    from datetime import datetime, timedelta

    cutoff_date = datetime.utcnow() - timedelta(days=days)

    with get_db() as db:
        from ...database.models import RegistrationTask
        from sqlalchemy import delete

        # 删除旧任务
        conditions = [RegistrationTask.created_at < cutoff_date]
        if not keep_failed:
            conditions.append(RegistrationTask.status != "failed")
        else:
            conditions.append(RegistrationTask.status.in_(["completed", "cancelled"]))

        result = db.execute(
            delete(RegistrationTask).where(*conditions)
        )
        db.commit()

        deleted_count = result.rowcount

    return {
        "success": True,
        "message": f"已清理 {deleted_count} 条过期任务记录",
        "deleted_count": deleted_count
    }


@router.get("/logs")
async def get_recent_logs(
    lines: int = 100,
    level: str = "INFO"
):
    """获取最近日志"""
    settings = get_settings()

    log_file = settings.log_file
    if not log_file:
        return {"logs": [], "message": "日志文件未配置"}

    from pathlib import Path
    log_path = Path(log_file)

    if not log_path.exists():
        return {"logs": [], "message": "日志文件不存在"}

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:]

        return {
            "logs": [line.strip() for line in recent_lines],
            "total_lines": len(all_lines)
        }
    except Exception as e:
        return {"logs": [], "error": str(e)}


# ============== 临时邮箱设置 ==============

class TempmailSettings(BaseModel):
    """临时邮箱设置"""
    api_url: Optional[str] = None
    enabled: bool = True


class EmailCodeSettings(BaseModel):
    """验证码等待设置"""
    timeout: int = 120  # 验证码等待超时（秒）
    poll_interval: int = 3  # 验证码轮询间隔（秒）


class HeroSMSSettings(BaseModel):
    """HeroSMS 接码设置"""
    enabled: bool = False
    api_key: Optional[str] = None
    service: str = "dr"
    country: int = 187
    max_price: str = "-1"
    proxy: str = ""
    timeout: int = 30
    verify_timeout: int = 180
    poll_interval: int = 3
    lowest_price_first: bool = True
    max_number_attempts: int = 1
    target_number_index: int = 1
    price_relax_enabled: bool = True
    price_relax_max_multiplier: int = 5
    reuse_enabled: bool = False
    reuse_max_uses: int = 2


class HeroSMSTestRequest(BaseModel):
    """HeroSMS 测试请求"""
    api_key: Optional[str] = None
    proxy: str = ""


@router.get("/tempmail")
async def get_tempmail_settings():
    """获取临时邮箱设置"""
    settings = get_settings()

    return {
        "api_url": settings.tempmail_base_url,
        "timeout": settings.tempmail_timeout,
        "max_retries": settings.tempmail_max_retries,
        "enabled": True  # 临时邮箱默认可用
    }


@router.post("/tempmail")
async def update_tempmail_settings(request: TempmailSettings):
    """更新临时邮箱设置"""
    update_dict = {}

    if request.api_url:
        update_dict["tempmail_base_url"] = request.api_url

    update_settings(**update_dict)

    return {"success": True, "message": "临时邮箱设置已更新"}


# ============== 验证码等待设置 ==============

@router.get("/email-code")
async def get_email_code_settings():
    """获取验证码等待设置"""
    settings = get_settings()
    return {
        "timeout": settings.email_code_timeout,
        "poll_interval": settings.email_code_poll_interval,
    }


@router.post("/email-code")
async def update_email_code_settings(request: EmailCodeSettings):
    """更新验证码等待设置"""
    # 验证参数范围
    if request.timeout < 30 or request.timeout > 600:
        raise HTTPException(status_code=400, detail="超时时间必须在 30-600 秒之间")
    if request.poll_interval < 1 or request.poll_interval > 30:
        raise HTTPException(status_code=400, detail="轮询间隔必须在 1-30 秒之间")

    update_settings(
        email_code_timeout=request.timeout,
        email_code_poll_interval=request.poll_interval,
    )

    return {"success": True, "message": "验证码等待设置已更新"}


@router.get("/herosms")
async def get_herosms_settings():
    """获取 HeroSMS 接码设置"""
    return _load_herosms_settings_from_db()


@router.post("/herosms")
async def update_herosms_settings(request: HeroSMSSettings):
    """更新 HeroSMS 接码设置"""
    if request.country <= 0:
        raise HTTPException(status_code=400, detail="国家代码必须大于 0")
    if request.timeout < 5 or request.timeout > 120:
        raise HTTPException(status_code=400, detail="API 超时必须在 5-120 秒之间")
    if request.verify_timeout < 30 or request.verify_timeout > 600:
        raise HTTPException(status_code=400, detail="验证码等待超时必须在 30-600 秒之间")
    if request.poll_interval < 1 or request.poll_interval > 30:
        raise HTTPException(status_code=400, detail="轮询间隔必须在 1-30 秒之间")
    if request.max_number_attempts < 1 or request.max_number_attempts > 20:
        raise HTTPException(status_code=400, detail="最大换号次数必须在 1-20 之间")
    if request.target_number_index < 1 or request.target_number_index > request.max_number_attempts:
        raise HTTPException(status_code=400, detail="使用第 N 个号码必须在 1 到最大换号次数之间")
    if request.price_relax_max_multiplier < 1 or request.price_relax_max_multiplier > 20:
        raise HTTPException(status_code=400, detail="价格放宽最大倍数必须在 1-20 之间")
    if request.reuse_max_uses < 1 or request.reuse_max_uses > 5:
        raise HTTPException(status_code=400, detail="号码复用次数必须在 1-5 之间")

    update_dict = {
        "herosms_enabled": request.enabled,
        "herosms_service": request.service,
        "herosms_country": request.country,
        "herosms_max_price": request.max_price,
        "herosms_proxy": request.proxy,
        "herosms_timeout": request.timeout,
        "herosms_verify_timeout": request.verify_timeout,
        "herosms_poll_interval": request.poll_interval,
        "herosms_lowest_price_first": request.lowest_price_first,
        "herosms_max_number_attempts": request.max_number_attempts,
        "herosms_target_number_index": request.target_number_index,
        "herosms_price_relax_enabled": request.price_relax_enabled,
        "herosms_price_relax_max_multiplier": request.price_relax_max_multiplier,
        "herosms_reuse_enabled": request.reuse_enabled,
        "herosms_reuse_max_uses": request.reuse_max_uses,
    }
    # API Key 是密码字段：前端留空/传 null/传空字符串时都表示保持原值。
    if request.api_key is not None and request.api_key.strip():
        update_dict["herosms_api_key"] = request.api_key.strip()

    update_settings(**update_dict)
    return {"success": True, "message": "HeroSMS 设置已更新"}


@router.post("/herosms/test")
async def test_herosms_settings(request: HeroSMSTestRequest):
    """测试 HeroSMS API Key 是否可用。"""
    try:
        from ...core.herosms_client import HeroSMSClient, HeroSMSConfig

        api_key = (request.api_key or "").strip()
        if not api_key:
            api_key = _get_saved_herosms_api_key()
        if not api_key:
            raise HTTPException(status_code=400, detail="未提供 HeroSMS API Key，且系统中没有已保存的 Key")

        settings = get_settings()
        proxy = (request.proxy or "").strip() or (settings.herosms_proxy or "").strip() or None
        client = HeroSMSClient(HeroSMSConfig(api_key=api_key, proxy=proxy))
        balance = client.get_balance()
        return {
            "success": True,
            "balance": balance,
            "message": f"HeroSMS 连接成功，当前余额: {balance}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("测试 HeroSMS 连接失败: %s", e)
        return {
            "success": False,
            "message": f"HeroSMS 连接失败: {e}"
        }


@router.get("/herosms/countries")
async def get_herosms_countries():
    """获取 HeroSMS 国家列表，用于前端可搜索选择。"""
    try:
        from ...core.herosms_client import HeroSMSClient, HeroSMSConfig

        client = HeroSMSClient(HeroSMSConfig(api_key=""))
        countries = client.get_countries()
        normalized = []
        for item in countries:
            if not isinstance(item, dict):
                continue
            code = item.get("id") or item.get("country") or item.get("code")
            if code is None:
                continue
            name = str(item.get("name") or item.get("eng") or item.get("title") or str(code)).strip()
            en_name = str(item.get("eng") or item.get("english") or name).strip()
            zh_name = str(
                item.get("cn")
                or item.get("zh")
                or item.get("chn")
                or item.get("name_cn")
                or HEROSMS_COUNTRY_ZH.get(en_name)
                or HEROSMS_COUNTRY_ZH.get(name)
                or name
            ).strip()
            display = f"{zh_name}({en_name}) - {code}" if en_name and en_name != zh_name else f"{zh_name} - {code}"
            normalized.append({
                "code": int(code),
                "name": name,
                "zh_name": zh_name,
                "en_name": en_name,
                "display": display,
                "raw": item,
            })
        normalized.sort(key=lambda x: (x["zh_name"], x["en_name"].lower(), x["code"]))
        return {"countries": normalized}
    except Exception as e:
        logger.warning("获取 HeroSMS 国家列表失败: %s", e)
        raise HTTPException(status_code=502, detail=f"获取 HeroSMS 国家列表失败: {e}")


# ============== 代理列表 CRUD ==============

class ProxyCreateRequest(BaseModel):
    """创建代理请求"""
    name: str
    type: str = "http"  # http, socks5
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    enabled: bool = True
    priority: int = 0


class ProxyUpdateRequest(BaseModel):
    """更新代理请求"""
    name: Optional[str] = None
    type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None


@router.get("/proxies")
async def get_proxies_list(enabled: Optional[bool] = None):
    """获取代理列表"""
    with get_db() as db:
        proxies = crud.get_proxies(db, enabled=enabled)
        return {
            "proxies": [p.to_dict() for p in proxies],
            "total": len(proxies)
        }


@router.post("/proxies")
async def create_proxy_item(request: ProxyCreateRequest):
    """创建代理"""
    with get_db() as db:
        proxy = crud.create_proxy(
            db,
            name=request.name,
            type=request.type,
            host=request.host,
            port=request.port,
            username=request.username,
            password=request.password,
            enabled=request.enabled,
            priority=request.priority
        )
        return {"success": True, "proxy": proxy.to_dict()}


@router.get("/proxies/{proxy_id}")
async def get_proxy_item(proxy_id: int):
    """获取单个代理"""
    with get_db() as db:
        proxy = crud.get_proxy_by_id(db, proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return proxy.to_dict(include_password=True)


@router.patch("/proxies/{proxy_id}")
async def update_proxy_item(proxy_id: int, request: ProxyUpdateRequest):
    """更新代理"""
    with get_db() as db:
        update_data = {}
        if request.name is not None:
            update_data["name"] = request.name
        if request.type is not None:
            update_data["type"] = request.type
        if request.host is not None:
            update_data["host"] = request.host
        if request.port is not None:
            update_data["port"] = request.port
        if request.username is not None:
            update_data["username"] = request.username
        if request.password is not None:
            update_data["password"] = request.password
        if request.enabled is not None:
            update_data["enabled"] = request.enabled
        if request.priority is not None:
            update_data["priority"] = request.priority

        proxy = crud.update_proxy(db, proxy_id, **update_data)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "proxy": proxy.to_dict()}


@router.delete("/proxies/{proxy_id}")
async def delete_proxy_item(proxy_id: int):
    """删除代理"""
    with get_db() as db:
        success = crud.delete_proxy(db, proxy_id)
        if not success:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "message": "代理已删除"}


@router.post("/proxies/{proxy_id}/set-default")
async def set_proxy_default(proxy_id: int):
    """将指定代理设为默认"""
    with get_db() as db:
        proxy = crud.set_proxy_default(db, proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "proxy": proxy.to_dict()}


@router.post("/proxies/{proxy_id}/test")
async def test_proxy_item(proxy_id: int):
    """测试单个代理"""
    import time
    from curl_cffi import requests as cffi_requests

    with get_db() as db:
        proxy = crud.get_proxy_by_id(db, proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")

        proxy_url = proxy.proxy_url
        try:
            basic = _test_proxy_http_basic(proxy_url)
            if not basic.get("success"):
                return {"success": False, "message": basic.get("message", "HTTP 代理测试失败")}
            https_test = _test_proxy_https_openai(proxy_url)
            return {
                "success": True,
                "ip": basic.get("ip", ""),
                "response_time": basic.get("response_time"),
                "https_openai_ok": bool(https_test.get("success")),
                "https_openai_message": https_test.get("message", ""),
                "message": basic.get("message", "代理连接成功")
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"代理连接失败: {str(e)}"
            }


@router.post("/proxies/test-all")
async def test_all_proxies():
    """测试所有启用的代理"""
    import time
    from curl_cffi import requests as cffi_requests

    with get_db() as db:
        proxies = crud.get_enabled_proxies(db)

        results = []
        for proxy in proxies:
            proxy_url = proxy.proxy_url
            try:
                basic = _test_proxy_http_basic(proxy_url)
                https_test = _test_proxy_https_openai(proxy_url) if basic.get("success") else {"success": False, "message": "未执行"}
                results.append({
                    "id": proxy.id,
                    "name": proxy.name,
                    "success": bool(basic.get("success")),
                    "ip": basic.get("ip", ""),
                    "response_time": basic.get("response_time"),
                    "message": basic.get("message", ""),
                    "https_openai_ok": bool(https_test.get("success")),
                    "https_openai_message": https_test.get("message", ""),
                })
            except Exception as e:
                results.append({
                    "id": proxy.id,
                    "name": proxy.name,
                    "success": False,
                    "message": str(e)
                })

        success_count = sum(1 for r in results if r["success"])
        return {
            "total": len(proxies),
            "success": success_count,
            "failed": len(proxies) - success_count,
            "results": results
        }


@router.post("/proxies/{proxy_id}/enable")
async def enable_proxy(proxy_id: int):
    """启用代理"""
    with get_db() as db:
        proxy = crud.update_proxy(db, proxy_id, enabled=True)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "message": "代理已启用"}


@router.post("/proxies/{proxy_id}/disable")
async def disable_proxy(proxy_id: int):
    """禁用代理"""
    with get_db() as db:
        proxy = crud.update_proxy(db, proxy_id, enabled=False)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "message": "代理已禁用"}


# ============== Outlook 设置 ==============

class OutlookSettings(BaseModel):
    """Outlook 设置"""
    default_client_id: Optional[str] = None


@router.get("/outlook")
async def get_outlook_settings():
    """获取 Outlook 设置"""
    settings = get_settings()

    return {
        "default_client_id": settings.outlook_default_client_id,
        "provider_priority": settings.outlook_provider_priority,
        "health_failure_threshold": settings.outlook_health_failure_threshold,
        "health_disable_duration": settings.outlook_health_disable_duration,
    }


@router.post("/outlook")
async def update_outlook_settings(request: OutlookSettings):
    """更新 Outlook 设置"""
    update_dict = {}

    if request.default_client_id is not None:
        update_dict["outlook_default_client_id"] = request.default_client_id

    if update_dict:
        update_settings(**update_dict)

    return {"success": True, "message": "Outlook 设置已更新"}


# ============== Team Manager 设置 ==============

class TeamManagerSettings(BaseModel):
    """Team Manager 设置"""
    enabled: bool = False
    api_url: str = ""
    api_key: str = ""


class TeamManagerTestRequest(BaseModel):
    """Team Manager 测试请求"""
    api_url: str
    api_key: str


@router.get("/team-manager")
async def get_team_manager_settings():
    """获取 Team Manager 设置"""
    settings = get_settings()
    return {
        "enabled": settings.tm_enabled,
        "api_url": settings.tm_api_url,
        "has_api_key": bool(settings.tm_api_key and settings.tm_api_key.get_secret_value()),
    }


@router.post("/team-manager")
async def update_team_manager_settings(request: TeamManagerSettings):
    """更新 Team Manager 设置"""
    update_dict = {
        "tm_enabled": request.enabled,
        "tm_api_url": request.api_url,
    }
    if request.api_key:
        update_dict["tm_api_key"] = request.api_key
    update_settings(**update_dict)
    return {"success": True, "message": "Team Manager 设置已更新"}


@router.post("/team-manager/test")
async def test_team_manager_connection(request: TeamManagerTestRequest):
    """测试 Team Manager 连接"""
    from ...core.upload.team_manager_upload import test_team_manager_connection as do_test

    settings = get_settings()
    api_key = request.api_key
    if api_key == 'use_saved_key' or not api_key:
        if settings.tm_api_key:
            api_key = settings.tm_api_key.get_secret_value()
        else:
            return {"success": False, "message": "未配置 API Key"}

    success, message = do_test(request.api_url, api_key)
    return {"success": success, "message": message}
