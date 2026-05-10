"""
设置 API 路由
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...config.settings import (
    get_database_url,
    get_settings,
    update_settings,
    normalize_sms_provider_name,
    get_sms_provider_display_name,
    get_sms_provider_api_key_field,
    get_sms_provider_api_key_db_key,
)
from ...core.registration_flow_templates import get_registration_flow_templates, normalize_flow_template
from ...core.sms import SMSProviderConfig, get_sms_provider
from ...core.dynamic_proxy import build_proxy_requests_mapping
from ...database import crud
from ...database.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

SEEKPROXY_GEO_CACHE_TTL = 86400
SEEKPROXY_GEO_CACHE_PATH = Path("data/cache/seekproxy_geo_cache.json")
SMS_PROVIDER_CACHE_TTL = 7 * 86400
SMS_PROVIDER_CACHE_PATH = Path("data/cache/sms_provider_cache.json")


SMS_COUNTRY_ZH = {
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


def _get_saved_sms_api_key(provider: Optional[str] = None) -> str:
    """按 provider 读取已保存的短信平台 API Key，避免单例配置缓存不一致。"""
    provider_name = normalize_sms_provider_name(provider or getattr(get_settings(), "sms_provider", "herosms"))
    db_key = get_sms_provider_api_key_db_key(provider_name)
    settings_field = get_sms_provider_api_key_field(provider_name)
    try:
        with get_db() as db:
            setting = crud.get_setting(db, db_key)
            value = str(setting.value or "").strip() if setting else ""
            if value:
                return value
    except Exception:
        pass
    try:
        settings = get_settings()
        secret = getattr(settings, settings_field, None)
        if secret:
            return secret.get_secret_value().strip() if hasattr(secret, "get_secret_value") else str(secret).strip()
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


def _load_sms_settings_from_db() -> dict:
    """
    短信平台设置直接从数据库读取，避免单例缓存或初始化时序导致页面回退默认值。
    """
    settings = get_settings()
    provider_name = normalize_sms_provider_name(getattr(settings, "sms_provider", "herosms") or "herosms")
    defaults = {
        "provider": provider_name,
        "provider_display_name": get_sms_provider_display_name(provider_name),
        "operator": str(getattr(settings, "sms_operator", "") or ""),
        "provider_ids": str(getattr(settings, "sms_provider_ids", "") or ""),
        "except_provider_ids": str(getattr(settings, "sms_except_provider_ids", "") or ""),
        "phone_exception": str(getattr(settings, "sms_phone_exception", "") or ""),
        "country_key": str(getattr(settings, "sms_country_key", "") or ""),
        "min_price": float(getattr(settings, "sms_min_price", -1) or -1),
        "reuse_platform": bool(getattr(settings, "sms_reuse", False)),
        "voice": bool(getattr(settings, "sms_voice", False)),
        "forwarding": bool(getattr(settings, "sms_forwarding", False)),
        "forwarding_number": str(getattr(settings, "sms_forwarding_number", "") or ""),
        "provider_failover_enabled": bool(getattr(settings, "sms_provider_failover_enabled", True)),
        "provider_fail_threshold": int(getattr(settings, "sms_provider_fail_threshold", 3) or 3),
        "enabled": bool(getattr(settings, "herosms_enabled", False)),
        "has_api_key": bool(_get_saved_sms_api_key(provider_name)),
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
        "retry_per_provider": int(getattr(settings, "sms_retry_per_provider", 1) or 1),
        "reuse_enabled": bool(getattr(settings, "herosms_reuse_enabled", False)),
        "reuse_max_uses": int(getattr(settings, "herosms_reuse_max_uses", 2) or 2),
    }
    db_key_map = {
        "provider": ("sms.provider", lambda v: str(v).strip() or defaults["provider"]),
        "operator": ("sms.operator", lambda v: str(v or "").strip()),
        "provider_ids": ("sms.provider_ids", lambda v: str(v or "").strip()),
        "except_provider_ids": ("sms.except_provider_ids", lambda v: str(v or "").strip()),
        "phone_exception": ("sms.phone_exception", lambda v: str(v or "").strip()),
        "country_key": ("sms.country_key", lambda v: str(v or "").strip()),
        "min_price": ("sms.min_price", lambda v: _parse_float(v, defaults["min_price"])),
        "reuse_platform": ("sms.reuse", lambda v: _parse_bool(v, defaults["reuse_platform"])),
        "voice": ("sms.voice", lambda v: _parse_bool(v, defaults["voice"])),
        "forwarding": ("sms.forwarding", lambda v: _parse_bool(v, defaults["forwarding"])),
        "forwarding_number": ("sms.forwarding_number", lambda v: str(v or "").strip()),
        "provider_failover_enabled": ("sms.provider_failover_enabled", lambda v: _parse_bool(v, defaults["provider_failover_enabled"])),
        "provider_fail_threshold": ("sms.provider_fail_threshold", lambda v: _parse_int(v, defaults["provider_fail_threshold"])),
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
        "retry_per_provider": ("sms.retry_per_provider", lambda v: _parse_int(v, defaults["retry_per_provider"])),
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
        logger.warning("直接读取短信平台设置失败，回退到缓存配置: %s", exc)
    defaults["provider"] = normalize_sms_provider_name(defaults.get("provider"))
    defaults["provider_display_name"] = get_sms_provider_display_name(defaults["provider"])
    defaults["has_api_key"] = bool(_get_saved_sms_api_key(defaults["provider"]))
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


def _get_saved_dynamic_proxy_password() -> str:
    """优先从数据库直接读取动态代理账密模式密码。"""
    try:
        with get_db() as db:
            setting = crud.get_setting(db, "proxy.dynamic_password")
            value = str(setting.value or "").strip() if setting else ""
            if value:
                return value
    except Exception:
        pass
    try:
        settings = get_settings()
        secret = getattr(settings, "proxy_dynamic_password", None)
        if secret:
            return secret.get_secret_value().strip() if hasattr(secret, "get_secret_value") else str(secret).strip()
    except Exception:
        pass
    return ""


def _get_saved_dynamic_seekproxy_trade_no() -> str:
    """优先从数据库直接读取 SeekProxy trade_no。"""
    try:
        with get_db() as db:
            setting = crud.get_setting(db, "proxy.dynamic_seekproxy_trade_no")
            value = str(setting.value or "").strip() if setting else ""
            if value:
                return value
    except Exception:
        pass
    try:
        return str(getattr(get_settings(), "proxy_dynamic_seekproxy_trade_no", "") or "").strip()
    except Exception:
        return ""


def _get_saved_dynamic_seekproxy_key() -> str:
    """优先从数据库直接读取 SeekProxy key。"""
    try:
        with get_db() as db:
            setting = crud.get_setting(db, "proxy.dynamic_seekproxy_key")
            value = str(setting.value or "").strip() if setting else ""
            if value:
                return value
    except Exception:
        pass
    try:
        secret = getattr(get_settings(), "proxy_dynamic_seekproxy_key", None)
        if secret:
            return secret.get_secret_value().strip() if hasattr(secret, "get_secret_value") else str(secret).strip()
    except Exception:
        pass
    return ""


def _parse_json_dict_setting(value, default: Optional[dict] = None) -> dict:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return dict(default or {})
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


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
        "preference_mode": str(getattr(settings, "proxy_preference_mode", "auto") or "auto"),
        "preferred_fixed_id": int(getattr(settings, "proxy_preferred_fixed_id", 0) or 0),
        "connect_retry_count": int(getattr(settings, "proxy_connect_retry_count", 3) or 3),
        "dynamic_enabled": bool(getattr(settings, "proxy_dynamic_enabled", False)),
        "dynamic_profiles": dict(getattr(settings, "proxy_dynamic_profiles", {}) or {}),
        "dynamic_mode": str(getattr(settings, "proxy_dynamic_mode", "api") or "api"),
        "dynamic_provider": str(getattr(settings, "proxy_dynamic_provider", "generic") or "generic"),
        "dynamic_api_url": str(getattr(settings, "proxy_dynamic_api_url", "") or ""),
        "dynamic_api_key_header": str(getattr(settings, "proxy_dynamic_api_key_header", "X-API-Key") or "X-API-Key"),
        "dynamic_result_field": str(getattr(settings, "proxy_dynamic_result_field", "") or ""),
        "dynamic_provider_appid": str(getattr(settings, "proxy_dynamic_provider_appid", "") or ""),
        "has_dynamic_provider_appkey": bool(getattr(settings, "proxy_dynamic_provider_appkey", None)),
        "dynamic_seekproxy_trade_no": str(getattr(settings, "proxy_dynamic_seekproxy_trade_no", "") or ""),
        "has_dynamic_seekproxy_key": bool(getattr(settings, "proxy_dynamic_seekproxy_key", None)),
        "dynamic_seekproxy_auth_type": int(getattr(settings, "proxy_dynamic_seekproxy_auth_type", 2) or 2),
        "dynamic_seekproxy_ip_count": int(getattr(settings, "proxy_dynamic_seekproxy_ip_count", 1) or 1),
        "dynamic_seekproxy_state": str(getattr(settings, "proxy_dynamic_seekproxy_state", "") or ""),
        "dynamic_seekproxy_city": str(getattr(settings, "proxy_dynamic_seekproxy_city", "") or ""),
        "dynamic_seekproxy_break_type": int(getattr(settings, "proxy_dynamic_seekproxy_break_type", 1) or 1),
        "dynamic_seekproxy_time": int(getattr(settings, "proxy_dynamic_seekproxy_time", 5) or 5),
        "dynamic_seekproxy_protocol": int(getattr(settings, "proxy_dynamic_seekproxy_protocol", 0) or 0),
        "dynamic_seekproxy_pattern": int(getattr(settings, "proxy_dynamic_seekproxy_pattern", 0) or 0),
        "dynamic_seekproxy_valid_code": int(getattr(settings, "proxy_dynamic_seekproxy_valid_code", 0) or 0),
        "dynamic_scheme": str(getattr(settings, "proxy_dynamic_scheme", "http") or "http"),
        "dynamic_host": str(getattr(settings, "proxy_dynamic_host", "proxy.haiwai-ip.com") or "proxy.haiwai-ip.com"),
        "dynamic_port": int(getattr(settings, "proxy_dynamic_port", 1456) or 1456),
        "dynamic_username": str(getattr(settings, "proxy_dynamic_username", "") or ""),
        "has_dynamic_password": bool(getattr(settings, "proxy_dynamic_password", None)),
        "dynamic_country": str(getattr(settings, "proxy_dynamic_country", "us") or "us"),
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
        "preference_mode": ("proxy.preference_mode", lambda v: str(v).strip() or defaults["preference_mode"]),
        "preferred_fixed_id": ("proxy.preferred_fixed_id", lambda v: _parse_int(v, defaults["preferred_fixed_id"])),
        "connect_retry_count": ("proxy.connect_retry_count", lambda v: _parse_int(v, defaults["connect_retry_count"])),
        "dynamic_enabled": ("proxy.dynamic_enabled", lambda v: _parse_bool(v, defaults["dynamic_enabled"])),
        "dynamic_profiles": ("proxy.dynamic_profiles", lambda v: _parse_json_dict_setting(v, defaults["dynamic_profiles"])),
        "dynamic_mode": ("proxy.dynamic_mode", lambda v: str(v).strip() or defaults["dynamic_mode"]),
        "dynamic_provider": ("proxy.dynamic_provider", lambda v: str(v).strip() or defaults["dynamic_provider"]),
        "dynamic_api_url": ("proxy.dynamic_api_url", lambda v: str(v).strip()),
        "dynamic_api_key_header": ("proxy.dynamic_api_key_header", lambda v: str(v).strip() or defaults["dynamic_api_key_header"]),
        "dynamic_result_field": ("proxy.dynamic_result_field", lambda v: str(v).strip()),
        "dynamic_provider_appid": ("proxy.dynamic_provider_appid", lambda v: str(v).strip()),
        "dynamic_seekproxy_trade_no": ("proxy.dynamic_seekproxy_trade_no", lambda v: str(v).strip()),
        "dynamic_seekproxy_auth_type": ("proxy.dynamic_seekproxy_auth_type", lambda v: _parse_int(v, defaults["dynamic_seekproxy_auth_type"])),
        "dynamic_seekproxy_ip_count": ("proxy.dynamic_seekproxy_ip_count", lambda v: _parse_int(v, defaults["dynamic_seekproxy_ip_count"])),
        "dynamic_seekproxy_state": ("proxy.dynamic_seekproxy_state", lambda v: str(v).strip()),
        "dynamic_seekproxy_city": ("proxy.dynamic_seekproxy_city", lambda v: str(v).strip()),
        "dynamic_seekproxy_break_type": ("proxy.dynamic_seekproxy_break_type", lambda v: _parse_int(v, defaults["dynamic_seekproxy_break_type"])),
        "dynamic_seekproxy_time": ("proxy.dynamic_seekproxy_time", lambda v: _parse_int(v, defaults["dynamic_seekproxy_time"])),
        "dynamic_seekproxy_protocol": ("proxy.dynamic_seekproxy_protocol", lambda v: _parse_int(v, defaults["dynamic_seekproxy_protocol"])),
        "dynamic_seekproxy_pattern": ("proxy.dynamic_seekproxy_pattern", lambda v: _parse_int(v, defaults["dynamic_seekproxy_pattern"])),
        "dynamic_seekproxy_valid_code": ("proxy.dynamic_seekproxy_valid_code", lambda v: _parse_int(v, defaults["dynamic_seekproxy_valid_code"])),
        "dynamic_scheme": ("proxy.dynamic_scheme", lambda v: str(v).strip() or defaults["dynamic_scheme"]),
        "dynamic_host": ("proxy.dynamic_host", lambda v: str(v).strip() or defaults["dynamic_host"]),
        "dynamic_port": ("proxy.dynamic_port", lambda v: _parse_int(v, defaults["dynamic_port"])),
        "dynamic_username": ("proxy.dynamic_username", lambda v: str(v).strip()),
        "dynamic_country": ("proxy.dynamic_country", lambda v: str(v).strip() or defaults["dynamic_country"]),
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
            dynamic_password_setting = crud.get_setting(db, "proxy.dynamic_password")
            defaults["has_dynamic_password"] = bool(str(dynamic_password_setting.value or "").strip()) if dynamic_password_setting else defaults["has_dynamic_password"]
            dynamic_provider_appkey_setting = crud.get_setting(db, "proxy.dynamic_provider_appkey")
            defaults["has_dynamic_provider_appkey"] = bool(str(dynamic_provider_appkey_setting.value or "").strip()) if dynamic_provider_appkey_setting else defaults["has_dynamic_provider_appkey"]
            dynamic_seekproxy_key_setting = crud.get_setting(db, "proxy.dynamic_seekproxy_key")
            defaults["has_dynamic_seekproxy_key"] = bool(str(dynamic_seekproxy_key_setting.value or "").strip()) if dynamic_seekproxy_key_setting else defaults["has_dynamic_seekproxy_key"]
    except Exception as exc:
        logger.warning("直接读取代理设置失败，回退到缓存配置: %s", exc)

    # 兼容字段是实际运行链路仍会读取的兜底配置；如果 profiles 因历史版本、备份或重启
    # 没有恢复出来，需要反向合成当前 SeekProxy profile，避免前端回填为空并在测试/保存时覆盖旧值。
    profiles = _parse_json_dict_setting(defaults.get("dynamic_profiles"), {})
    defaults["dynamic_profiles"] = profiles
    if str(defaults.get("dynamic_provider") or "").strip().lower() == "seekproxy":
        profile_key = _dynamic_profile_key("seekproxy", defaults.get("dynamic_mode") or "api")
        profile = dict(profiles.get(profile_key) or {})
        if defaults.get("dynamic_seekproxy_trade_no") and not profile.get("trade_no"):
            profile["trade_no"] = defaults["dynamic_seekproxy_trade_no"]
        saved_key = _get_saved_dynamic_seekproxy_key()
        if saved_key and not profile.get("key"):
            profile["key"] = saved_key
        for field, profile_field in {
            "dynamic_seekproxy_auth_type": "auth_type",
            "dynamic_seekproxy_ip_count": "ip_count",
            "dynamic_seekproxy_state": "state",
            "dynamic_seekproxy_city": "city",
            "dynamic_seekproxy_break_type": "break_type",
            "dynamic_seekproxy_time": "time",
            "dynamic_seekproxy_protocol": "protocol",
            "dynamic_seekproxy_pattern": "pattern",
            "dynamic_seekproxy_valid_code": "valid_code",
            "dynamic_country": "country",
        }.items():
            value = defaults.get(field)
            if value not in (None, "") and profile.get(profile_field) in (None, ""):
                profile[profile_field] = value
        if profile:
            profiles[profile_key] = profile
    return defaults


def _dynamic_profile_key(provider: str, mode: str) -> str:
    return f"{str(provider or 'generic').strip().lower()}::{str(mode or 'api').strip().lower()}"


def _build_dynamic_profile_payload(request: "DynamicProxySettings") -> dict:
    mode = str(request.mode or "api").strip().lower() or "api"
    provider = str(request.provider or "generic").strip().lower() or "generic"
    if mode == "account":
        return {
            "scheme": str(request.scheme or "http").strip() or "http",
            "host": request.host.strip(),
            "port": request.port,
            "username": request.username.strip(),
            "password": request.password if request.password is not None else None,
            "country": request.country.strip() or "us",
        }
    if provider == "seekproxy":
        existing_profile = {}
        try:
            existing_profile = _load_proxy_settings_from_db().get("dynamic_profiles", {}).get(_dynamic_profile_key(provider, mode), {}) or {}
        except Exception:
            existing_profile = {}
        existing_trade_no = str(existing_profile.get("trade_no") or _get_saved_dynamic_seekproxy_trade_no() or "").strip()
        existing_key = str(existing_profile.get("key") or _get_saved_dynamic_seekproxy_key() or "").strip()
        incoming_key = request.seekproxy_key.strip() if isinstance(request.seekproxy_key, str) else request.seekproxy_key
        return {
            # trade_no 也需要“留空保持不变”，否则只保存刷新/验证开关或重启后页面未回填时会把已保存配置清空。
            "trade_no": request.seekproxy_trade_no.strip() or existing_trade_no,
            # 前端为了安全会在保存后清空密钥输入框；空字符串表示“保持原值”，
            # 只有显式传入非空值才覆盖，避免重启/二次保存后丢失。
            "key": incoming_key if incoming_key not in (None, "") else existing_key,
            "auth_type": request.seekproxy_auth_type,
            "ip_count": request.seekproxy_ip_count,
            "state": request.seekproxy_state.strip(),
            "city": request.seekproxy_city.strip(),
            "break_type": request.seekproxy_break_type,
            "time": request.seekproxy_time,
            "protocol": request.seekproxy_protocol,
            "pattern": request.seekproxy_pattern,
            "valid_code": request.seekproxy_valid_code,
            "country": request.country.strip() or "US",
        }
    if provider == "haiwaidaili":
        return {
            "api_url": request.api_url,
            "api_key": request.api_key if request.api_key is not None else None,
            "api_key_header": request.api_key_header,
            "result_field": request.result_field,
            "provider_appid": request.provider_appid.strip(),
            "provider_appkey": request.provider_appkey if request.provider_appkey is not None else None,
            "scheme": str(request.scheme or "http").strip() or "http",
            "country": request.country.strip() or "us",
        }
    return {
        "api_url": request.api_url,
        "api_key": request.api_key if request.api_key is not None else None,
        "api_key_header": request.api_key_header,
        "result_field": request.result_field,
        "scheme": str(request.scheme or "http").strip() or "http",
        "country": request.country.strip() or "us",
    }


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
    return build_proxy_requests_mapping(proxy_url, include_https=include_https)


def _ensure_seekproxy_cache_dir() -> None:
    try:
        SEEKPROXY_GEO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _load_seekproxy_geo_cache() -> dict:
    try:
        if SEEKPROXY_GEO_CACHE_PATH.exists():
            return json.loads(SEEKPROXY_GEO_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("读取 SeekProxy 地理缓存失败: %s", exc)
    return {}


def _save_seekproxy_geo_cache(data: dict) -> None:
    try:
        _ensure_seekproxy_cache_dir()
        SEEKPROXY_GEO_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("保存 SeekProxy 地理缓存失败: %s", exc)


def _seekproxy_cache_get(section: str, cache_key: str) -> Optional[list]:
    cache = _load_seekproxy_geo_cache()
    node = ((cache.get(section) or {}) if isinstance(cache, dict) else {})
    entry = node.get(cache_key) if isinstance(node, dict) else None
    if not isinstance(entry, dict):
        return None
    ts = float(entry.get("ts") or 0)
    if time.time() - ts > SEEKPROXY_GEO_CACHE_TTL:
        return None
    data = entry.get("data")
    return data if isinstance(data, list) else None


def _seekproxy_cache_set(section: str, cache_key: str, rows: list[dict]) -> None:
    cache = _load_seekproxy_geo_cache()
    if not isinstance(cache, dict):
        cache = {}
    section_node = cache.get(section)
    if not isinstance(section_node, dict):
        section_node = {}
        cache[section] = section_node
    section_node[cache_key] = {
        "ts": time.time(),
        "data": rows,
    }
    _save_seekproxy_geo_cache(cache)


def _ensure_sms_provider_cache_dir() -> None:
    try:
        SMS_PROVIDER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _load_sms_provider_cache() -> dict:
    try:
        if SMS_PROVIDER_CACHE_PATH.exists():
            return json.loads(SMS_PROVIDER_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("读取短信平台缓存失败: %s", exc)
    return {}


def _save_sms_provider_cache(data: dict) -> None:
    try:
        _ensure_sms_provider_cache_dir()
        SMS_PROVIDER_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("保存短信平台缓存失败: %s", exc)


def _format_cache_updated_at(ts: float) -> Optional[str]:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))
    except Exception:
        return None


def _sms_provider_cache_get(section: str, cache_key: str, *, allow_stale: bool = False) -> Optional[dict]:
    cache = _load_sms_provider_cache()
    node = ((cache.get(section) or {}) if isinstance(cache, dict) else {})
    entry = node.get(cache_key) if isinstance(node, dict) else None
    if not isinstance(entry, dict):
        return None
    data = entry.get("data")
    if not isinstance(data, list):
        return None
    ts = float(entry.get("ts") or 0)
    stale = time.time() - ts > SMS_PROVIDER_CACHE_TTL
    if stale and not allow_stale:
        return None
    return {
        "data": data,
        "ts": ts,
        "stale": stale,
        "cache_updated_at": _format_cache_updated_at(ts),
    }


def _sms_provider_cache_set(section: str, cache_key: str, rows: list[dict]) -> dict:
    ts = time.time()
    cache = _load_sms_provider_cache()
    if not isinstance(cache, dict):
        cache = {}
    section_node = cache.get(section)
    if not isinstance(section_node, dict):
        section_node = {}
        cache[section] = section_node
    section_node[cache_key] = {
        "ts": ts,
        "data": rows,
    }
    _save_sms_provider_cache(cache)
    return {
        "data": rows,
        "ts": ts,
        "stale": False,
        "cache_updated_at": _format_cache_updated_at(ts),
    }


def _fetch_seekproxy_json(path: str, params: Optional[dict] = None) -> list[dict]:
    from urllib.parse import urlencode
    import requests

    url = f"https://www.seekproxy.com{path}"
    if params:
        query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
        if query:
            url = f"{url}?{query}"

    response = requests.get(url, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if int(payload.get("code") or 0) != 200:
        raise ValueError(payload.get("msg") or "SeekProxy 接口返回失败")
    data = payload.get("data") or []
    if not isinstance(data, list):
        return []
    return data


def _get_seekproxy_countries(force_refresh: bool = False) -> list[dict]:
    cache_key = "all"
    if not force_refresh:
        cached = _seekproxy_cache_get("countries", cache_key)
        if cached is not None:
            return cached
    rows = _fetch_seekproxy_json("/out-api/country-list")
    normalized = [
        {
            "code": str(item.get("code") or "").strip().upper(),
            "name": str(item.get("name") or "").strip(),
        }
        for item in rows
        if str(item.get("code") or "").strip()
    ]
    _seekproxy_cache_set("countries", cache_key, normalized)
    return normalized


def _get_seekproxy_states(country_code: str, force_refresh: bool = False) -> list[dict]:
    code = str(country_code or "").strip().upper()
    if not code:
        return []
    if not force_refresh:
        cached = _seekproxy_cache_get("states", code)
        if cached is not None:
            return cached
    rows = _fetch_seekproxy_json("/out-api/state-list", {"code": code})
    normalized = []
    for item in rows:
        if isinstance(item, dict):
            state_code = str(item.get("code") or item.get("state") or item.get("name") or "").strip()
            state_name = str(item.get("name") or item.get("state") or item.get("code") or "").strip()
        else:
            state_code = str(item or "").strip()
            state_name = state_code
        if state_name:
            normalized.append({"code": state_code or state_name, "name": state_name})
    _seekproxy_cache_set("states", code, normalized)
    return normalized


def _get_seekproxy_cities(country_code: str, state_name: str, force_refresh: bool = False) -> list[dict]:
    code = str(country_code or "").strip().upper()
    state = str(state_name or "").strip()
    if not code or not state:
        return []
    cache_key = f"{code}::{state}"
    if not force_refresh:
        cached = _seekproxy_cache_get("cities", cache_key)
        if cached is not None:
            return cached
    rows = _fetch_seekproxy_json("/out-api/city-list", {"code": code, "state": state})
    normalized = []
    for item in rows:
        if isinstance(item, dict):
            city_code = str(item.get("code") or item.get("city") or item.get("name") or "").strip()
            city_name = str(item.get("name") or item.get("city") or item.get("code") or "").strip()
        else:
            city_code = str(item or "").strip()
            city_name = city_code
        if city_name:
            normalized.append({"code": city_code or city_name, "name": city_name})
    _seekproxy_cache_set("cities", cache_key, normalized)
    return normalized


def _search_seekproxy_geo_rows(rows: list[dict], keyword: str) -> list[dict]:
    key = str(keyword or "").strip().lower()
    if not key:
        return rows[:200]
    matched = []
    for row in rows:
        code = str(row.get("code") or "").lower()
        name = str(row.get("name") or "").lower()
        if key in code or key in name:
            matched.append(row)
    return matched[:200]


def _test_proxy_http_basic(proxy_url: str) -> dict:
    import time
    import requests

    start = time.time()
    resp = requests.get(
        "http://api.ipify.org?format=json",
        proxies=_build_proxy_test_mapping(proxy_url, include_https=False),
        timeout=10,
        headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"},
        verify=False,
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
    import requests
    try:
        resp = requests.get(
            "https://auth.openai.com/",
            proxies=_build_proxy_test_mapping(proxy_url, include_https=True),
            timeout=10,
            headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"},
            allow_redirects=False,
            verify=False,
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
    sms_settings = _load_sms_settings_from_db()
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
        "sms": sms_settings,
        "herosms": sms_settings,
        "sms_provider_ui": _get_sms_provider_ui_meta(sms_settings.get("provider")),
    }


@router.get("/proxy/dynamic")
async def get_dynamic_proxy_settings():
    """获取动态代理设置"""
    proxy_settings = _load_proxy_settings_from_db()
    return {
        "enabled": proxy_settings["dynamic_enabled"],
        "profiles": proxy_settings["dynamic_profiles"],
        "dynamic_profiles": proxy_settings["dynamic_profiles"],
        "mode": proxy_settings["dynamic_mode"],
        "provider": proxy_settings["dynamic_provider"],
        "api_url": proxy_settings["dynamic_api_url"],
        "api_key_header": proxy_settings["dynamic_api_key_header"],
        "result_field": proxy_settings["dynamic_result_field"],
        "provider_appid": proxy_settings["dynamic_provider_appid"],
        "has_provider_appkey": proxy_settings["has_dynamic_provider_appkey"],
        "seekproxy_trade_no": proxy_settings["dynamic_seekproxy_trade_no"],
        "has_seekproxy_key": proxy_settings["has_dynamic_seekproxy_key"],
        "seekproxy_auth_type": proxy_settings["dynamic_seekproxy_auth_type"],
        "seekproxy_ip_count": proxy_settings["dynamic_seekproxy_ip_count"],
        "seekproxy_state": proxy_settings["dynamic_seekproxy_state"],
        "seekproxy_city": proxy_settings["dynamic_seekproxy_city"],
        "seekproxy_break_type": proxy_settings["dynamic_seekproxy_break_type"],
        "seekproxy_time": proxy_settings["dynamic_seekproxy_time"],
        "seekproxy_protocol": proxy_settings["dynamic_seekproxy_protocol"],
        "seekproxy_pattern": proxy_settings["dynamic_seekproxy_pattern"],
        "seekproxy_valid_code": proxy_settings["dynamic_seekproxy_valid_code"],
        "scheme": proxy_settings["dynamic_scheme"],
        "host": proxy_settings["dynamic_host"],
        "port": proxy_settings["dynamic_port"],
        "username": proxy_settings["dynamic_username"],
        "has_password": proxy_settings["has_dynamic_password"],
        "country": proxy_settings["dynamic_country"],
        "refresh_use_proxy": proxy_settings["refresh_use_proxy"],
        "validate_use_proxy": proxy_settings["validate_use_proxy"],
        "has_api_key": proxy_settings["has_dynamic_api_key"],
        "diagnostics": _build_proxy_runtime_diagnostics(),
    }


class DynamicProxySettings(BaseModel):
    """动态代理设置"""
    enabled: bool = False
    mode: str = "api"
    provider: str = "generic"
    api_url: str = ""
    api_key: Optional[str] = None
    api_key_header: str = "X-API-Key"
    result_field: str = ""
    provider_appid: str = ""
    provider_appkey: Optional[str] = None
    seekproxy_trade_no: str = ""
    seekproxy_key: Optional[str] = None
    seekproxy_auth_type: int = 2
    seekproxy_ip_count: int = 1
    seekproxy_state: str = ""
    seekproxy_city: str = ""
    seekproxy_break_type: int = 1
    seekproxy_time: int = 5
    seekproxy_protocol: int = 0
    seekproxy_pattern: int = 0
    seekproxy_valid_code: int = 0
    scheme: str = "http"
    host: str = "proxy.haiwai-ip.com"
    port: int = 1456
    username: str = ""
    password: Optional[str] = None
    country: str = "us"
    refresh_use_proxy: bool = False
    validate_use_proxy: bool = False


class ProxyPreferenceSettings(BaseModel):
    """任务代理来源策略"""
    preference_mode: str = "auto"
    preferred_fixed_id: int = 0
    connect_retry_count: int = 3


@router.get("/proxy/seekproxy/countries")
async def get_seekproxy_countries(keyword: str = Query("", description="国家代码或名称关键字"), refresh: bool = Query(False)):
    try:
        rows = _get_seekproxy_countries(force_refresh=refresh)
        return {"items": _search_seekproxy_geo_rows(rows, keyword)}
    except Exception as exc:
        logger.warning("获取 SeekProxy 国家列表失败: %s", exc)
        raise HTTPException(status_code=502, detail=f"获取 SeekProxy 国家列表失败: {exc}")


@router.get("/proxy/seekproxy/states")
async def get_seekproxy_states(
    country_code: str = Query(..., description="国家代码"),
    keyword: str = Query("", description="州/省关键字"),
    refresh: bool = Query(False),
):
    try:
        rows = _get_seekproxy_states(country_code, force_refresh=refresh)
        return {"country_code": country_code.upper(), "items": _search_seekproxy_geo_rows(rows, keyword)}
    except Exception as exc:
        logger.warning("获取 SeekProxy 州省列表失败: %s", exc)
        raise HTTPException(status_code=502, detail=f"获取 SeekProxy 州省列表失败: {exc}")


@router.get("/proxy/seekproxy/cities")
async def get_seekproxy_cities(
    country_code: str = Query(..., description="国家代码"),
    state: str = Query(..., description="州/省名称"),
    keyword: str = Query("", description="城市关键字"),
    refresh: bool = Query(False),
):
    try:
        rows = _get_seekproxy_cities(country_code, state, force_refresh=refresh)
        return {
            "country_code": country_code.upper(),
            "state": state,
            "items": _search_seekproxy_geo_rows(rows, keyword),
        }
    except Exception as exc:
        logger.warning("获取 SeekProxy 城市列表失败: %s", exc)
        raise HTTPException(status_code=502, detail=f"获取 SeekProxy 城市列表失败: {exc}")


@router.post("/proxy/dynamic")
async def update_dynamic_proxy_settings(request: DynamicProxySettings):
    """更新动态代理设置"""
    mode = str(request.mode or "api").strip().lower() or "api"
    provider = str(request.provider or "generic").strip().lower() or "generic"
    proxy_settings = _load_proxy_settings_from_db()
    profiles = dict(proxy_settings.get("dynamic_profiles", {}) or {})
    profile_key = _dynamic_profile_key(provider, mode)
    current_profile = profiles.get(profile_key, {})
    new_profile = _build_dynamic_profile_payload(request)
    merged_profile = dict(current_profile)
    for key, value in new_profile.items():
        if value is not None:
            merged_profile[key] = value
    profiles[profile_key] = merged_profile
    update_dict = {
        "proxy_dynamic_enabled": request.enabled,
        "proxy_dynamic_profiles": profiles,
        "proxy_dynamic_mode": mode,
        "proxy_dynamic_provider": provider,
        "proxy_refresh_use_proxy": request.refresh_use_proxy,
        "proxy_validate_use_proxy": request.validate_use_proxy,
    }

    # 同步当前选中的组合到兼容字段，供旧逻辑读取
    if mode == "account":
        update_dict.update({
            "proxy_dynamic_api_url": "",
            "proxy_dynamic_api_key_header": "X-API-Key",
            "proxy_dynamic_result_field": "",
            "proxy_dynamic_api_key": "",
            "proxy_dynamic_provider_appid": "",
            "proxy_dynamic_provider_appkey": "",
            "proxy_dynamic_seekproxy_trade_no": "",
            "proxy_dynamic_seekproxy_key": "",
            "proxy_dynamic_seekproxy_auth_type": 2,
            "proxy_dynamic_seekproxy_ip_count": 1,
            "proxy_dynamic_seekproxy_state": "",
            "proxy_dynamic_seekproxy_city": "",
            "proxy_dynamic_seekproxy_break_type": 1,
            "proxy_dynamic_seekproxy_time": 5,
            "proxy_dynamic_scheme": merged_profile.get("scheme", "http"),
            "proxy_dynamic_host": merged_profile.get("host", "proxy.haiwai-ip.com"),
            "proxy_dynamic_port": merged_profile.get("port", 1456),
            "proxy_dynamic_username": merged_profile.get("username", ""),
            "proxy_dynamic_password": merged_profile.get("password", ""),
            "proxy_dynamic_country": merged_profile.get("country", "us"),
        })
    elif provider == "seekproxy":
        update_dict.update({
            "proxy_dynamic_api_url": "",
            "proxy_dynamic_api_key_header": "X-API-Key",
            "proxy_dynamic_result_field": "",
            "proxy_dynamic_api_key": "",
            "proxy_dynamic_provider_appid": "",
            "proxy_dynamic_provider_appkey": "",
            "proxy_dynamic_seekproxy_trade_no": merged_profile.get("trade_no", ""),
            "proxy_dynamic_seekproxy_key": merged_profile.get("key", ""),
            "proxy_dynamic_seekproxy_auth_type": merged_profile.get("auth_type", 2),
            "proxy_dynamic_seekproxy_ip_count": merged_profile.get("ip_count", 1),
            "proxy_dynamic_seekproxy_state": merged_profile.get("state", ""),
            "proxy_dynamic_seekproxy_city": merged_profile.get("city", ""),
            "proxy_dynamic_seekproxy_break_type": merged_profile.get("break_type", 1),
            "proxy_dynamic_seekproxy_time": merged_profile.get("time", 5),
            "proxy_dynamic_seekproxy_protocol": merged_profile.get("protocol", 0),
            "proxy_dynamic_seekproxy_pattern": merged_profile.get("pattern", 0),
            "proxy_dynamic_seekproxy_valid_code": merged_profile.get("valid_code", 0),
            "proxy_dynamic_scheme": "http",
            "proxy_dynamic_host": "",
            "proxy_dynamic_port": 1456,
            "proxy_dynamic_username": "",
            "proxy_dynamic_password": "",
            "proxy_dynamic_country": merged_profile.get("country", "US"),
        })
    elif provider == "haiwaidaili":
        update_dict.update({
            "proxy_dynamic_api_url": merged_profile.get("api_url", ""),
            "proxy_dynamic_api_key_header": merged_profile.get("api_key_header", "X-API-Key"),
            "proxy_dynamic_result_field": merged_profile.get("result_field", ""),
            "proxy_dynamic_api_key": merged_profile.get("api_key", ""),
            "proxy_dynamic_provider_appid": merged_profile.get("provider_appid", ""),
            "proxy_dynamic_provider_appkey": merged_profile.get("provider_appkey", ""),
            "proxy_dynamic_seekproxy_trade_no": "",
            "proxy_dynamic_seekproxy_key": "",
            "proxy_dynamic_seekproxy_auth_type": 2,
            "proxy_dynamic_seekproxy_ip_count": 1,
            "proxy_dynamic_seekproxy_state": "",
            "proxy_dynamic_seekproxy_city": "",
            "proxy_dynamic_seekproxy_break_type": 1,
            "proxy_dynamic_seekproxy_time": 5,
            "proxy_dynamic_scheme": merged_profile.get("scheme", "http"),
            "proxy_dynamic_host": "",
            "proxy_dynamic_port": 1456,
            "proxy_dynamic_username": "",
            "proxy_dynamic_password": "",
            "proxy_dynamic_country": merged_profile.get("country", "us"),
        })
    else:
        update_dict.update({
            "proxy_dynamic_api_url": merged_profile.get("api_url", ""),
            "proxy_dynamic_api_key_header": merged_profile.get("api_key_header", "X-API-Key"),
            "proxy_dynamic_result_field": merged_profile.get("result_field", ""),
            "proxy_dynamic_api_key": merged_profile.get("api_key", ""),
            "proxy_dynamic_provider_appid": "",
            "proxy_dynamic_provider_appkey": "",
            "proxy_dynamic_seekproxy_trade_no": "",
            "proxy_dynamic_seekproxy_key": "",
            "proxy_dynamic_seekproxy_auth_type": 2,
            "proxy_dynamic_seekproxy_ip_count": 1,
            "proxy_dynamic_seekproxy_state": "",
            "proxy_dynamic_seekproxy_city": "",
            "proxy_dynamic_seekproxy_break_type": 1,
            "proxy_dynamic_seekproxy_time": 5,
            "proxy_dynamic_scheme": merged_profile.get("scheme", "http"),
            "proxy_dynamic_host": "",
            "proxy_dynamic_port": 1456,
            "proxy_dynamic_username": "",
            "proxy_dynamic_password": "",
            "proxy_dynamic_country": merged_profile.get("country", "us"),
        })

    update_settings(**update_dict)
    return {"success": True, "message": "动态代理设置已更新"}


@router.post("/proxy/preference")
async def update_proxy_preference_settings(request: ProxyPreferenceSettings):
    """更新任务代理来源策略"""
    preference_mode = str(request.preference_mode or "auto").strip().lower() or "auto"
    allowed_modes = {"auto", "dynamic", "fixed", "pool", "direct"}
    if preference_mode not in allowed_modes:
        raise HTTPException(status_code=400, detail="代理来源策略无效")

    preferred_fixed_id = int(request.preferred_fixed_id or 0)
    connect_retry_count = max(1, min(10, int(request.connect_retry_count or 3)))
    selected_proxy = None
    if preference_mode == "fixed":
        if preferred_fixed_id <= 0:
            raise HTTPException(status_code=400, detail="固定代理模式必须选择一个已启用的固定代理")
        with get_db() as db:
            selected_proxy = crud.get_proxy_by_id(db, preferred_fixed_id)
            if not selected_proxy:
                raise HTTPException(status_code=404, detail="指定的固定代理不存在")
            if not selected_proxy.enabled:
                raise HTTPException(status_code=400, detail="指定的固定代理未启用，请先启用后再保存")

    update_settings(
        proxy_preference_mode=preference_mode,
        proxy_preferred_fixed_id=preferred_fixed_id if preference_mode == "fixed" else 0,
        proxy_connect_retry_count=connect_retry_count,
    )
    return {
        "success": True,
        "message": "任务代理策略已更新",
        "preference_mode": preference_mode,
        "preferred_fixed_id": preferred_fixed_id if preference_mode == "fixed" else 0,
        "connect_retry_count": connect_retry_count,
        "preferred_fixed_name": getattr(selected_proxy, "name", None),
    }


@router.post("/proxy/preference/test")
async def test_proxy_preference_settings(request: ProxyPreferenceSettings):
    """测试当前任务代理策略最终命中的代理是否可达。"""
    from ...core.dynamic_proxy import get_proxy_url_for_task

    preference_mode = str(request.preference_mode or "auto").strip().lower() or "auto"
    allowed_modes = {"auto", "dynamic", "fixed", "pool", "direct"}
    if preference_mode not in allowed_modes:
        raise HTTPException(status_code=400, detail="代理来源策略无效")

    preferred_fixed_id = int(request.preferred_fixed_id or 0)

    def get_fixed_proxy():
        if preferred_fixed_id <= 0:
            return None, "固定代理未选择"
        with get_db() as db:
            proxy = crud.get_proxy_by_id(db, preferred_fixed_id)
            if not proxy:
                return None, "指定固定代理不存在"
            if not proxy.enabled:
                return None, "指定固定代理未启用"
            return proxy.proxy_url, f"固定代理 #{proxy.id} {proxy.name}"

    def get_pool_proxy():
        with get_db() as db:
            proxy = crud.get_random_proxy(db)
            if not proxy:
                return None, "代理池无可用代理"
            return proxy.proxy_url, f"代理池命中 #{proxy.id} {proxy.name}"

    settings = get_settings()
    dynamic_proxy = get_proxy_url_for_task() if settings.proxy_dynamic_enabled else None
    fixed_proxy, fixed_detail = get_fixed_proxy()
    pool_proxy, pool_detail = get_pool_proxy()
    static_proxy = settings.proxy_url

    source = "direct"
    source_name = "直连"
    source_detail = ""
    proxy_url = None
    if preference_mode == "dynamic":
        proxy_url, source, source_name, source_detail = dynamic_proxy, "dynamic", "动态代理", "任务代理策略=dynamic"
    elif preference_mode == "fixed":
        proxy_url, source, source_name, source_detail = fixed_proxy, "fixed", "固定代理", fixed_detail
    elif preference_mode == "pool":
        proxy_url, source, source_name, source_detail = pool_proxy, "pool", "代理池", pool_detail
    elif preference_mode == "direct":
        proxy_url, source, source_name, source_detail = None, "direct", "直连", "任务代理策略=direct"
    else:
        if dynamic_proxy:
            proxy_url, source, source_name, source_detail = dynamic_proxy, "dynamic", "动态代理", "auto 命中动态代理"
        elif fixed_proxy:
            proxy_url, source, source_name, source_detail = fixed_proxy, "fixed", "固定代理", fixed_detail
        elif pool_proxy:
            proxy_url, source, source_name, source_detail = pool_proxy, "pool", "代理池", pool_detail
        elif static_proxy:
            proxy_url, source, source_name, source_detail = static_proxy, "static", "静态代理", "auto 命中静态代理"
        else:
            proxy_url, source, source_name, source_detail = None, "direct", "直连", "auto 未命中任何代理"

    if not proxy_url:
        return {
            "success": True,
            "proxy_source": source,
            "proxy_source_name": source_name,
            "message": f"当前策略最终为{source_name}，无需代理连通性测试",
        }

    basic = _test_proxy_http_basic(proxy_url)
    if not basic.get("success"):
        return {
            "success": False,
            "proxy_source": source,
            "proxy_source_name": source_name,
            "proxy_used": proxy_url,
            "message": basic.get("message", "HTTP 代理测试失败"),
        }
    https_test = _test_proxy_https_openai(proxy_url)
    return {
        "success": bool(https_test.get("success")),
        "proxy_source": source,
        "proxy_source_name": source_name,
        "proxy_source_detail": source_detail,
        "proxy_used": proxy_url,
        "ip": basic.get("ip", ""),
        "response_time": basic.get("response_time"),
        "https_openai_ok": bool(https_test.get("success")),
        "https_openai_message": https_test.get("message", ""),
        "message": https_test.get("message") if not https_test.get("success") else basic.get("message", "代理可用"),
    }


@router.post("/proxy/dynamic/test")
async def test_dynamic_proxy(request: DynamicProxySettings):
    """测试动态代理 API"""
    from ...core.dynamic_proxy import (
        fetch_dynamic_proxy,
        fetch_dynamic_proxy_candidates,
        build_account_proxy_url,
        ensure_haiwaidaili_whitelist,
        ensure_seekproxy_whitelist,
        build_seekproxy_api_url,
        select_best_dynamic_proxy,
    )

    mode = str(request.mode or "api").strip().lower() or "api"
    if mode == "account":
        password = (request.password or "").strip()
        if not password:
            password = _get_saved_dynamic_proxy_password()
        proxy_url = build_account_proxy_url(
            scheme=request.scheme,
            host=request.host,
            port=request.port,
            username=request.username,
            password=password,
            country=request.country,
        )
        if not proxy_url:
            raise HTTPException(status_code=400, detail="请填写完整的账密代理主机、端口、用户名、密码")
    else:
        provider = str(request.provider or "generic").strip().lower() or "generic"
        api_url = request.api_url
        if provider == "seekproxy":
            seekproxy_trade_no = request.seekproxy_trade_no.strip() or _get_saved_dynamic_seekproxy_trade_no()
            seekproxy_key = (request.seekproxy_key or "").strip() or _get_saved_dynamic_seekproxy_key()
            if not seekproxy_trade_no or not seekproxy_key:
                raise HTTPException(status_code=400, detail="请填写完整的 SeekProxy trade_no 和 key")
            whitelist_message = ""
            if int(request.seekproxy_auth_type or 2) == 2:
                ok, whitelist_message = ensure_seekproxy_whitelist(seekproxy_trade_no, seekproxy_key)
                if not ok:
                    return {"success": False, "message": whitelist_message}
            api_url = build_seekproxy_api_url(
                trade_no=seekproxy_trade_no,
                key=seekproxy_key,
                auth_type=request.seekproxy_auth_type,
                ip_count=request.seekproxy_ip_count,
                country=request.country,
                state=request.seekproxy_state,
                city=request.seekproxy_city,
                fmt=1,
                break_type=request.seekproxy_break_type,
                hold_time=request.seekproxy_time,
                protocol=request.seekproxy_protocol,
                pattern=request.seekproxy_pattern,
                valid_code=request.seekproxy_valid_code,
            )
        elif provider != "seekproxy" and not request.api_url:
            raise HTTPException(status_code=400, detail="请填写动态代理 API 地址")

        provider_appid = (request.provider_appid or "").strip()
        provider_appkey = (request.provider_appkey or "").strip()
        if not provider_appkey:
            settings = get_settings()
            secret = getattr(settings, "proxy_dynamic_provider_appkey", None)
            if secret:
                provider_appkey = secret.get_secret_value().strip() if hasattr(secret, "get_secret_value") else str(secret).strip()
        whitelist_message = locals().get("whitelist_message", "")
        if provider == "haiwaidaili" and provider_appid and provider_appkey:
            ok, whitelist_message = ensure_haiwaidaili_whitelist(provider_appid, provider_appkey)
            if not ok:
                return {"success": False, "message": whitelist_message}

        # 若未传入 api_key，使用已保存的
        api_key = request.api_key or ""
        if not api_key:
            api_key = _get_saved_dynamic_proxy_api_key()
        candidates = fetch_dynamic_proxy_candidates(
            api_url=api_url,
            api_key=api_key,
            api_key_header=request.api_key_header,
            result_field=request.result_field,
            provider=provider,
            default_scheme="socks5h" if provider == "seekproxy" and int(request.seekproxy_auth_type or 2) == 1 and int(request.seekproxy_protocol or 0) == 2 else request.scheme,
        )
        if provider == "seekproxy":
            proxy_url = select_best_dynamic_proxy(
                candidates,
                seekproxy_trade_no=seekproxy_trade_no,
                seekproxy_key=seekproxy_key,
            ) if candidates else None
        else:
            proxy_url = select_best_dynamic_proxy(candidates) if candidates else None

    if not proxy_url:
        return {"success": False, "message": "动态代理返回为空或请求失败"}

    # 先按服务商文档测试 HTTP 代理基础可用性，再额外测试是否适合 OpenAI HTTPS。
    try:
        basic = _test_proxy_http_basic(proxy_url)
        if not basic.get("success"):
            return {"success": False, "proxy_url": proxy_url, "message": basic.get("message", "HTTP 代理测试失败"), "whitelist_message": locals().get("whitelist_message", "")}
        https_test = _test_proxy_https_openai(proxy_url)
        return {
            "success": True,
            "proxy_url": proxy_url,
            "candidate_count": len(candidates) if 'candidates' in locals() else 1,
            "ip": basic.get("ip", ""),
            "response_time": basic.get("response_time"),
            "https_openai_ok": bool(https_test.get("success")),
            "https_openai_message": https_test.get("message", ""),
            "message": basic.get("message", "动态代理 HTTP 可用"),
            "whitelist_message": locals().get("whitelist_message", ""),
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


class SMSSettings(BaseModel):
    """短信接码平台设置"""
    provider: str = "herosms"
    operator: str = ""
    provider_ids: str = ""
    except_provider_ids: str = ""
    phone_exception: str = ""
    country_key: str = ""
    min_price: str = "-1"
    reuse_platform: bool = False
    voice: bool = False
    forwarding: bool = False
    forwarding_number: str = ""
    provider_failover_enabled: bool = True
    provider_fail_threshold: int = 3
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
    retry_per_provider: int = 1
    reuse_enabled: bool = False
    reuse_max_uses: int = 2


class SMSTestRequest(BaseModel):
    """短信平台测试请求"""
    api_key: Optional[str] = None
    proxy: str = ""
    provider: Optional[str] = None


# 兼容旧命名
HeroSMSSettings = SMSSettings
HeroSMSTestRequest = SMSTestRequest


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
async def get_sms_settings_legacy():
    """获取短信平台设置"""
    return _load_sms_settings_from_db()


@router.get("/sms")
async def get_sms_settings():
    """获取短信平台设置（通用入口）。"""
    return _load_sms_settings_from_db()


def _validate_sms_settings_request(request: SMSSettings) -> str:
    provider_name = normalize_sms_provider_name(request.provider or "herosms")
    if provider_name != "5sim" and request.country <= 0:
        raise HTTPException(status_code=400, detail="国家代码必须大于 0")
    if provider_name == "5sim" and not request.country_key.strip():
        raise HTTPException(status_code=400, detail="5SIM 需要填写国家 slug/key，例如 england 或 any")
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
    if request.retry_per_provider < 1 or request.retry_per_provider > 50:
        raise HTTPException(status_code=400, detail="同组合取号重试次数必须在 1-50 之间")
    if request.provider_fail_threshold < 1 or request.provider_fail_threshold > 10:
        raise HTTPException(status_code=400, detail="同 provider 连续失败阈值必须在 1-10 之间")
    if request.reuse_max_uses < 1 or request.reuse_max_uses > 5:
        raise HTTPException(status_code=400, detail="号码复用次数必须在 1-5 之间")
    try:
        min_price = float(str(request.min_price).strip())
    except Exception:
        min_price = -1
    try:
        max_price = float(str(request.max_price).strip())
    except Exception:
        max_price = -1
    if min_price > 0 and max_price > 0 and min_price > max_price:
        raise HTTPException(status_code=400, detail="最小价格不能大于最大单价")
    return provider_name


def _build_sms_settings_update_dict(request: SMSSettings, provider_name: str) -> dict:
    update_dict = {
        "sms_provider": provider_name,
        "sms_operator": request.operator.strip(),
        "sms_provider_ids": request.provider_ids.strip(),
        "sms_except_provider_ids": request.except_provider_ids.strip(),
        "sms_phone_exception": request.phone_exception.strip(),
        "sms_country_key": request.country_key.strip(),
        "sms_min_price": request.min_price,
        "sms_reuse": request.reuse_platform,
        "sms_voice": request.voice,
        "sms_forwarding": request.forwarding,
        "sms_forwarding_number": request.forwarding_number.strip(),
        "sms_provider_failover_enabled": request.provider_failover_enabled,
        "sms_provider_fail_threshold": request.provider_fail_threshold,
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
        "sms_retry_per_provider": request.retry_per_provider,
        "herosms_reuse_enabled": request.reuse_enabled,
        "herosms_reuse_max_uses": request.reuse_max_uses,
    }
    if request.api_key is not None and request.api_key.strip():
        update_dict[get_sms_provider_api_key_field(provider_name)] = request.api_key.strip()
    return update_dict


@router.post("/herosms")
async def update_sms_settings_legacy(request: SMSSettings):
    """更新短信平台设置"""
    provider_name = _validate_sms_settings_request(request)
    update_dict = _build_sms_settings_update_dict(request, provider_name)
    update_settings(**update_dict)
    return {"success": True, "message": f"{get_sms_provider_display_name(provider_name)} 设置已更新"}


@router.post("/sms")
async def update_sms_settings(request: SMSSettings):
    """更新短信平台设置（通用入口）。"""
    provider_name = _validate_sms_settings_request(request)
    update_dict = _build_sms_settings_update_dict(request, provider_name)
    update_settings(**update_dict)
    return {"success": True, "message": f"{get_sms_provider_display_name(provider_name)} 设置已更新"}


@router.post("/herosms/test")
async def test_sms_settings_legacy(request: SMSTestRequest):
    """测试短信平台 API Key 是否可用。"""
    try:
        settings = get_settings()
        provider_name = normalize_sms_provider_name(request.provider or getattr(settings, "sms_provider", "herosms") or "herosms")
        provider_display_name = get_sms_provider_display_name(provider_name)
        api_key = (request.api_key or "").strip()
        if not api_key:
            api_key = _get_saved_sms_api_key(provider_name)
        if not api_key:
            raise HTTPException(status_code=400, detail=f"未提供 {provider_display_name} API Key，且系统中没有已保存的 Key")

        proxy = (request.proxy or "").strip() or (settings.herosms_proxy or "").strip() or None
        client = get_sms_provider(SMSProviderConfig(api_key=api_key, provider=provider_name, proxy=proxy))
        balance = client.get_balance()
        return {
            "success": True,
            "provider": provider_name,
            "provider_display_name": provider_display_name,
            "balance": balance,
            "message": f"{provider_display_name} 连接成功，当前余额: {balance}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("测试短信平台连接失败: %s", e)
        return {
            "success": False,
            "message": f"短信平台连接失败: {e}"
        }


@router.post("/sms/test")
async def test_sms_settings(request: SMSTestRequest):
    """测试短信平台连接（通用入口）。"""
    try:
        settings = get_settings()
        provider_name = normalize_sms_provider_name(request.provider or getattr(settings, "sms_provider", "herosms") or "herosms")
        provider_display_name = get_sms_provider_display_name(provider_name)
        api_key = (request.api_key or "").strip()
        if not api_key:
            api_key = _get_saved_sms_api_key(provider_name)
        if not api_key:
            raise HTTPException(status_code=400, detail=f"未提供 {provider_display_name} API Key，且系统中没有已保存的 Key")

        proxy = (request.proxy or "").strip() or (settings.herosms_proxy or "").strip() or None
        client = get_sms_provider(SMSProviderConfig(api_key=api_key, provider=provider_name, proxy=proxy))
        balance = client.get_balance()
        return {
            "success": True,
            "provider": provider_name,
            "provider_display_name": provider_display_name,
            "balance": balance,
            "message": f"{provider_display_name} 连接成功，当前余额: {balance}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("测试短信平台连接失败: %s", e)
        return {
            "success": False,
            "message": f"短信平台连接失败: {e}"
        }


@router.get("/herosms/countries")
async def get_sms_countries_legacy(provider: Optional[str] = Query(None), refresh: bool = Query(False)):
    """获取短信平台国家列表，用于前端可搜索选择。"""
    try:
        settings = get_settings()
        provider_name = normalize_sms_provider_name(provider or getattr(settings, "sms_provider", "herosms") or "herosms")
        return _load_sms_countries_with_cache(provider_name, refresh=refresh)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("获取短信平台国家列表失败: %s", e)
        raise HTTPException(status_code=502, detail=f"获取短信平台国家列表失败: {e}")


@router.get("/sms/countries")
async def get_sms_countries(provider: Optional[str] = Query(None), refresh: bool = Query(False)):
    """获取短信平台国家列表（通用入口）。"""
    try:
        settings = get_settings()
        provider_name = normalize_sms_provider_name(provider or getattr(settings, "sms_provider", "herosms") or "herosms")
        return _load_sms_countries_with_cache(provider_name, refresh=refresh)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("获取短信平台国家列表失败: %s", e)
        raise HTTPException(status_code=502, detail=f"获取短信平台国家列表失败: {e}")


def _build_sms_provider_from_settings(
    api_key: str = "",
    proxy: Optional[str] = None,
    *,
    provider_name: Optional[str] = None,
    service: Optional[str] = None,
    country: Optional[int] = None,
    country_key: Optional[str] = None,
):
    settings = get_settings()
    provider_name = normalize_sms_provider_name(provider_name or getattr(settings, "sms_provider", "herosms") or "herosms")
    return get_sms_provider(SMSProviderConfig(
        api_key=api_key or _get_saved_sms_api_key(provider_name),
        provider=provider_name,
        service=str(service or getattr(settings, "herosms_service", "dr") or "dr"),
        country=int(country if country is not None else getattr(settings, "herosms_country", 187) or 187),
        country_key=str(country_key if country_key is not None else getattr(settings, "sms_country_key", "") or ""),
        max_price=float(getattr(settings, "herosms_max_price", -1) or -1),
        min_price=float(getattr(settings, "sms_min_price", -1) or -1),
        proxy=proxy or ((getattr(settings, "herosms_proxy", "") or "").strip() or None),
        timeout=int(getattr(settings, "herosms_timeout", 30) or 30),
        provider_ids=str(getattr(settings, "sms_provider_ids", "") or ""),
        except_provider_ids=str(getattr(settings, "sms_except_provider_ids", "") or ""),
        phone_exception=str(getattr(settings, "sms_phone_exception", "") or ""),
        reuse=bool(getattr(settings, "sms_reuse", False)),
        voice=bool(getattr(settings, "sms_voice", False)),
        forwarding=bool(getattr(settings, "sms_forwarding", False)),
        forwarding_number=str(getattr(settings, "sms_forwarding_number", "") or ""),
    ))


SMS_PROVIDER_UI_META = {
    "herosms": {
        "label": "HeroSMS",
        "supports": {
            "top_countries": True,
            "services": True,
            "operators": True,
            "operator_quotes": True,
            "provider_quotes": False,
            "static_wallet": False,
        },
        "service_example": "dr",
        "country_mode": "numeric",
    },
    "smsbower": {
        "label": "SMSBower",
        "supports": {
            "top_countries": True,
            "services": True,
            "operators": False,
            "operator_quotes": False,
            "provider_quotes": True,
            "static_wallet": True,
        },
        "service_example": "dr",
        "country_mode": "numeric",
    },
    "5sim": {
        "label": "5SIM",
        "supports": {
            "top_countries": True,
            "services": True,
            "operators": True,
            "operator_quotes": True,
            "provider_quotes": False,
            "static_wallet": False,
        },
        "service_example": "openai",
        "country_mode": "slug",
    },
}


def _get_sms_provider_ui_meta(provider: Optional[str] = None) -> dict:
    provider_name = normalize_sms_provider_name(provider or getattr(get_settings(), "sms_provider", "herosms"))
    meta = SMS_PROVIDER_UI_META.get(provider_name, SMS_PROVIDER_UI_META["herosms"]).copy()
    meta["provider"] = provider_name
    return meta


def _normalize_sms_country_rows(countries: list[dict]) -> list[dict]:
    normalized = []
    for item in countries:
        if not isinstance(item, dict):
            continue
        code = item.get("heroSmsCountry") or item.get("hero_sms_country") or item.get("id") or item.get("country") or item.get("code")
        country_key = str(item.get("country_key") or item.get("key") or item.get("slug") or "").strip()
        if code is None and not country_key:
            continue
        name = str(item.get("apiName") or item.get("name") or item.get("eng") or item.get("title") or str(code)).strip()
        en_name = str(item.get("eng") or item.get("english") or item.get("apiName") or name).strip()
        zh_name = str(
            item.get("cn")
            or item.get("zh")
            or item.get("chn")
            or item.get("name_cn")
            or SMS_COUNTRY_ZH.get(en_name)
            or SMS_COUNTRY_ZH.get(name)
            or name
        ).strip()
        code_label = str(code) if code is not None else country_key
        display = f"{zh_name}({en_name}) - {code_label}" if en_name and en_name != zh_name else f"{zh_name} - {code_label}"
        normalized.append({
            "code": int(code) if code is not None else None,
            "country_key": country_key,
            "name": name,
            "zh_name": zh_name,
            "en_name": en_name,
            "display": display,
            "raw": item,
        })
    normalized.sort(key=lambda x: (x["zh_name"], x["en_name"].lower(), x["code"] if x["code"] is not None else 999999, x["country_key"]))
    return normalized


def _load_sms_countries_with_cache(provider_name: str, *, refresh: bool = False) -> dict:
    provider_name = normalize_sms_provider_name(provider_name or "herosms")
    cache_key = provider_name
    if not refresh:
        cached = _sms_provider_cache_get("countries", cache_key)
        if cached is not None:
            return {
                "countries": cached["data"],
                "provider": provider_name,
                "cached": True,
                "stale": False,
                "cache_updated_at": cached.get("cache_updated_at"),
            }

    try:
        client = _build_sms_provider_from_settings(provider_name=provider_name)
        normalized = _normalize_sms_country_rows(client.get_countries())
        cached = _sms_provider_cache_set("countries", cache_key, normalized)
        return {
            "countries": normalized,
            "provider": provider_name,
            "cached": False,
            "stale": False,
            "cache_updated_at": cached.get("cache_updated_at"),
        }
    except Exception as exc:
        stale = _sms_provider_cache_get("countries", cache_key, allow_stale=True)
        if stale is not None:
            logger.warning("获取短信平台国家列表失败，使用缓存 provider=%s: %s", provider_name, exc)
            return {
                "countries": stale["data"],
                "provider": provider_name,
                "cached": True,
                "stale": True,
                "cache_updated_at": stale.get("cache_updated_at"),
                "warning": f"获取短信平台国家列表失败，已使用缓存: {exc}",
            }
        raise


def _beautify_top_country_rows(provider_name: str, rows: list[dict]) -> list[dict]:
    provider_name = normalize_sms_provider_name(provider_name)
    if provider_name == "5sim":
        rows = [row for row in rows if int(row.get("count") or 0) > 0]
    if provider_name == "herosms":
        try:
            countries = _load_sms_countries_with_cache(provider_name, refresh=False).get("countries") or []
        except Exception as exc:
            logger.warning("HeroSMS 推荐国家补充元数据失败，跳过国家列表 enrichment: %s", exc)
            countries = []
        by_code = {int(item["code"]): item for item in countries if item.get("code") is not None}
        enriched = []
        for row in rows:
            code = row.get("heroSmsCountry")
            meta = by_code.get(int(code)) if code is not None else None
            if meta:
                row = {
                    **row,
                    "apiName": meta.get("en_name") or meta.get("name") or row.get("apiName") or "",
                    "zh_name": meta.get("zh_name") or "",
                    "isoCode": meta.get("raw", {}).get("isoCode") or row.get("isoCode") or "",
                    "dialCode": meta.get("raw", {}).get("dialCode") or row.get("dialCode") or "",
                }
            enriched.append(row)
        rows = enriched
    return rows


# 兼容旧命名
_load_herosms_settings_from_db = _load_sms_settings_from_db


@router.get("/sms/top-countries")
async def get_sms_top_countries(
    service: Optional[str] = None,
    provider: Optional[str] = Query(None),
    country: Optional[int] = Query(None),
    country_key: Optional[str] = Query(None),
):
    try:
        meta = _get_sms_provider_ui_meta(provider)
        if not meta["supports"]["top_countries"]:
            raise HTTPException(status_code=501, detail=f"{meta['label']} 暂不支持推荐国家查询")
        provider_client = _build_sms_provider_from_settings(provider_name=provider, service=service, country=country, country_key=country_key)
        rows = provider_client.get_top_countries_by_service(service=service or None)
        rows = _beautify_top_country_rows(normalize_sms_provider_name(provider or get_settings().sms_provider), rows)
        return {"provider": provider or get_settings().sms_provider, "items": rows}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("获取短信平台推荐国家失败: %s", e)
        raise HTTPException(status_code=502, detail=f"获取推荐国家失败: {e}")


@router.get("/sms/services")
async def get_sms_services(
    provider: Optional[str] = Query(None),
    country: Optional[int] = Query(None),
    country_key: Optional[str] = Query(None),
):
    try:
        meta = _get_sms_provider_ui_meta(provider)
        if not meta["supports"]["services"]:
            raise HTTPException(status_code=501, detail=f"{meta['label']} 暂不支持服务列表查询")
        provider_client = _build_sms_provider_from_settings(provider_name=provider, country=country, country_key=country_key)
        services = provider_client.get_services()
        return {"provider": provider or get_settings().sms_provider, "items": services}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("获取短信平台服务列表失败: %s", e)
        raise HTTPException(status_code=502, detail=f"获取服务列表失败: {e}")


@router.get("/sms/operators")
async def get_sms_operators(
    country: Optional[int] = Query(None),
    provider: Optional[str] = Query(None),
    country_key: Optional[str] = Query(None),
    service: Optional[str] = Query(None),
):
    meta = _get_sms_provider_ui_meta(provider)
    if not meta["supports"]["operators"]:
        raise HTTPException(status_code=501, detail=f"{meta['label']} 暂不支持运营商查询")
    if (provider or "").strip().lower() != "5sim" and (country is None or country <= 0):
        raise HTTPException(status_code=400, detail="国家代码必须大于 0")
    try:
        provider_client = _build_sms_provider_from_settings(provider_name=provider, country=country, country_key=country_key)
        service_value = None
        if normalize_sms_provider_name(provider or get_settings().sms_provider) == "5sim":
            service_value = str(service or getattr(provider_client.config, "service", "") or "").strip() or None
        try:
            operators = provider_client.get_operators(country or 0, service=service_value)
        except TypeError:
            operators = provider_client.get_operators(country or 0)
        return {"provider": provider or get_settings().sms_provider, "country": country, "country_key": country_key, "service": service or "", "items": operators}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("获取短信平台运营商失败: %s", e)
        raise HTTPException(status_code=502, detail=f"获取运营商失败: {e}")


@router.get("/sms/operator-quotes")
async def get_sms_operator_quotes(
    country: Optional[int] = Query(None),
    service: Optional[str] = None,
    provider: Optional[str] = Query(None),
    country_key: Optional[str] = Query(None),
):
    meta = _get_sms_provider_ui_meta(provider)
    if not meta["supports"]["operator_quotes"]:
        raise HTTPException(status_code=501, detail=f"{meta['label']} 暂不支持运营商报价查询")
    if (provider or "").strip().lower() != "5sim" and (country is None or country <= 0):
        raise HTTPException(status_code=400, detail="国家代码必须大于 0")
    try:
        provider_client = _build_sms_provider_from_settings(provider_name=provider, service=service, country=country, country_key=country_key)
        quotes = provider_client.get_operator_quote_options(service=service or None, country=country or 0)
        return {"provider": provider or get_settings().sms_provider, "country": country, "country_key": country_key, "service": service or "", "items": quotes}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("获取短信平台运营商报价失败: %s", e)
        raise HTTPException(status_code=502, detail=f"获取运营商报价失败: {e}")


@router.get("/sms/provider-quotes")
async def get_sms_provider_quotes(
    country: Optional[int] = Query(None),
    service: Optional[str] = None,
    provider: Optional[str] = Query(None),
    country_key: Optional[str] = Query(None),
):
    meta = _get_sms_provider_ui_meta(provider)
    if not meta["supports"]["provider_quotes"]:
        raise HTTPException(status_code=501, detail=f"{meta['label']} 暂不支持 Provider 级报价查询")
    if (provider or "").strip().lower() != "5sim" and (country is None or country <= 0):
        raise HTTPException(status_code=400, detail="国家代码必须大于 0")
    try:
        provider_client = _build_sms_provider_from_settings(provider_name=provider, service=service, country=country, country_key=country_key)
        quotes = provider_client.get_provider_price_options(service=service or None, country=country or 0)
        return {"provider": provider or get_settings().sms_provider, "country": country, "country_key": country_key, "service": service or "", "items": quotes}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("获取短信平台 provider 级报价失败: %s", e)
        raise HTTPException(status_code=502, detail=f"获取 provider 报价失败: {e}")


@router.get("/sms/static-wallet")
async def get_sms_static_wallet(coin: str, network: str, provider: Optional[str] = Query(None)):
    try:
        meta = _get_sms_provider_ui_meta(provider)
        if not meta["supports"]["static_wallet"]:
            raise HTTPException(status_code=501, detail=f"{meta['label']} 暂不支持静态钱包查询")
        provider_client = _build_sms_provider_from_settings(provider_name=provider)
        wallet = provider_client.get_static_wallet(coin=coin, network=network)
        return {"provider": provider or get_settings().sms_provider, "coin": coin, "network": network, "wallet": wallet}
    except HTTPException:
        raise
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.warning("获取短信平台静态钱包失败: %s", e)
        raise HTTPException(status_code=502, detail=f"获取静态钱包失败: {e}")


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


