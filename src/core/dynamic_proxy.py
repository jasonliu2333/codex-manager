"""
动态代理获取模块
支持通过外部 API 获取动态代理 URL
"""

import logging
import re
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


def build_seekproxy_api_url(
    *,
    trade_no: str,
    key: str,
    auth_type: int = 2,
    ip_count: int = 1,
    country: str = "",
    state: str = "",
    city: str = "",
    fmt: int = 1,
    break_type: int = 1,
    hold_time: int = 5,
) -> str:
    from urllib.parse import urlencode
    base = "http://api.seekproxy.com:8000/api/get-ips"
    params = {
        "trade_no": trade_no,
        "key": key,
        "auth_type": int(auth_type or 2),
        "ip_count": int(ip_count or 1),
        "country": country or "",
        "state": state or "",
        "city": city or "",
        "format": int(fmt or 1),
        "break_type": int(break_type or 1),
        "time": int(hold_time or 5),
    }
    return f"{base}?{urlencode(params)}"


def build_account_proxy_url(
    *,
    scheme: str = "http",
    host: str,
    port: int,
    username: str,
    password: str,
    country: str = "",
) -> Optional[str]:
    scheme = (scheme or "http").strip().lower()
    if scheme not in {"http", "socks5", "socks5h"}:
        scheme = "http"
    host = str(host or "").strip()
    username = str(username or "").strip()
    password = str(password or "").strip()
    country = str(country or "").strip().lower()
    if not host or not port or not username or not password:
        return None
    proxy_user = username
    if country:
        proxy_user = f"{username}-country-{country}-session-{int(__import__('time').time() * 1000000)}"
    safe_user = quote(proxy_user, safe="")
    safe_password = quote(password, safe="")
    return f"{scheme}://{safe_user}:{safe_password}@{host}:{int(port)}"


def _normalize_proxy_url(proxy_url: str, *, default_scheme: str = "http") -> Optional[str]:
    proxy_url = str(proxy_url or "").strip()
    if not proxy_url:
        return None
    if re.match(r'^(http|https|socks5|socks5h)://', proxy_url):
        return proxy_url
    scheme = (default_scheme or "http").strip().lower()
    if scheme not in {"http", "https", "socks5", "socks5h"}:
        scheme = "http"
    return f"{scheme}://{proxy_url}"


def _extract_proxy_from_json_text(text: str, result_field: str = "") -> Optional[str]:
    try:
        import json
        data = json.loads(text)
        if result_field:
            for key in result_field.split("."):
                if isinstance(data, dict):
                    data = data.get(key)
                elif isinstance(data, list) and key.isdigit():
                    data = data[int(key)]
                else:
                    data = None
                if data is None:
                    break
            return str(data).strip() if data is not None else None
        for key in ("proxy", "url", "proxy_url", "data", "ip"):
            val = data.get(key) if isinstance(data, dict) else None
            if val:
                return str(val).strip()
    except Exception:
        return None
    return None


def _parse_generic_proxy_response(text: str, *, result_field: str = "", default_scheme: str = "http") -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None
    if result_field or text.startswith("{") or text.startswith("["):
        extracted = _extract_proxy_from_json_text(text, result_field=result_field)
        if extracted:
            return _normalize_proxy_url(extracted, default_scheme=default_scheme)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return _normalize_proxy_url(first_line, default_scheme=default_scheme) if first_line else None


def _parse_haiwaidaili_proxy_response(text: str) -> Optional[str]:
    first_line = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    return _normalize_proxy_url(first_line, default_scheme="http") if first_line else None


def _parse_seekproxy_proxy_response(text: str) -> Optional[str]:
    first_line = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    if not first_line:
        return None
    parts = first_line.split(":")
    if len(parts) == 4:
        host, port, username, password = parts
        return build_account_proxy_url(
            scheme="http",
            host=host,
            port=int(port),
            username=username,
            password=password,
            country="",
        )
    return _normalize_proxy_url(first_line, default_scheme="http")


def parse_dynamic_proxy_response(
    provider: str,
    text: str,
    *,
    result_field: str = "",
    default_scheme: str = "http",
) -> Optional[str]:
    provider = str(provider or "generic").strip().lower()
    if provider == "seekproxy":
        return _parse_seekproxy_proxy_response(text)
    if provider == "haiwaidaili":
        return _parse_haiwaidaili_proxy_response(text)
    return _parse_generic_proxy_response(text, result_field=result_field, default_scheme=default_scheme)


def parse_dynamic_proxy_candidates(
    provider: str,
    text: str,
    *,
    result_field: str = "",
    default_scheme: str = "http",
) -> list[str]:
    provider = str(provider or "generic").strip().lower()
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    candidates: list[str] = []
    if provider == "seekproxy":
        for line in lines:
            proxy = _parse_seekproxy_proxy_response(line)
            if proxy:
                candidates.append(proxy)
        return candidates
    if provider == "haiwaidaili":
        for line in lines:
            proxy = _parse_haiwaidaili_proxy_response(line)
            if proxy:
                candidates.append(proxy)
        return candidates
    proxy = _parse_generic_proxy_response(text, result_field=result_field, default_scheme=default_scheme)
    return [proxy] if proxy else []


def fetch_dynamic_proxy(
    api_url: str,
    api_key: str = "",
    api_key_header: str = "X-API-Key",
    result_field: str = "",
    retries: int = 3,
    *,
    provider: str = "generic",
    default_scheme: str = "http",
) -> Optional[str]:
    """
    从代理 API 获取代理 URL

    Args:
        api_url: 代理 API 地址，响应应为代理 URL 字符串或含代理 URL 的 JSON
        api_key: API 密钥（可选）
        api_key_header: API 密钥请求头名称
        result_field: 从 JSON 响应中提取代理 URL 的字段路径，支持点号分隔（如 "data.proxy"），留空则使用响应原文

    Returns:
        代理 URL 字符串（如 http://user:pass@host:port），失败返回 None
    """
    from curl_cffi import requests as cffi_requests

    headers = {}
    if api_key:
        headers[api_key_header] = api_key

    last_error = None
    max_attempts = max(1, int(retries or 1))
    for attempt in range(1, max_attempts + 1):
        try:
            response = cffi_requests.get(
                api_url,
                headers=headers,
                timeout=10,
                impersonate="chrome110"
            )

            if response.status_code != 200:
                last_error = f"动态代理 API 返回错误状态码: {response.status_code}"
                logger.warning(f"{last_error} (第 {attempt}/{max_attempts} 次)")
                continue

            text = response.text.strip()
            proxy_url = parse_dynamic_proxy_response(
                provider,
                text,
                result_field=result_field,
                default_scheme=default_scheme,
            )

            if not proxy_url:
                last_error = "动态代理 API 返回空代理 URL"
                logger.warning(f"{last_error} (第 {attempt}/{max_attempts} 次)")
                continue

            logger.info(f"动态代理获取成功: {proxy_url[:40]}..." if len(proxy_url) > 40 else f"动态代理获取成功: {proxy_url}")
            return proxy_url

        except Exception as e:
            last_error = str(e)
            logger.error(f"获取动态代理失败(第 {attempt}/{max_attempts} 次): {e}")
            continue

    logger.error(f"获取动态代理最终失败: {last_error}")
    return None


def fetch_dynamic_proxy_candidates(
    api_url: str,
    api_key: str = "",
    api_key_header: str = "X-API-Key",
    result_field: str = "",
    retries: int = 3,
    *,
    provider: str = "generic",
    default_scheme: str = "http",
) -> list[str]:
    from curl_cffi import requests as cffi_requests

    headers = {}
    if api_key:
        headers[api_key_header] = api_key

    last_error = None
    max_attempts = max(1, int(retries or 1))
    for attempt in range(1, max_attempts + 1):
        try:
            response = cffi_requests.get(
                api_url,
                headers=headers,
                timeout=10,
                impersonate="chrome110"
            )
            if response.status_code != 200:
                last_error = f"动态代理 API 返回错误状态码: {response.status_code}"
                logger.warning(f"{last_error} (第 {attempt}/{max_attempts} 次)")
                continue
            candidates = parse_dynamic_proxy_candidates(
                provider,
                response.text,
                result_field=result_field,
                default_scheme=default_scheme,
            )
            if candidates:
                logger.info("动态代理获取候选成功，共 %s 个节点", len(candidates))
                return candidates
            last_error = "动态代理 API 未解析出可用候选"
            logger.warning(f"{last_error} (第 {attempt}/{max_attempts} 次)")
        except Exception as e:
            last_error = str(e)
            logger.error(f"获取动态代理候选失败(第 {attempt}/{max_attempts} 次): {e}")
            continue
    logger.error(f"获取动态代理候选最终失败: {last_error}")
    return []


def fetch_public_ip() -> Optional[str]:
    from curl_cffi import requests as cffi_requests
    for url in ("http://ip234.in/ip.json", "http://api.ipify.org?format=json"):
        try:
            resp = cffi_requests.get(url, timeout=10, impersonate="chrome110")
            if resp.status_code != 200:
                continue
            try:
                data = resp.json()
                ip = str(data.get("ip") or data.get("data", {}).get("ip") or "").strip()
                if ip:
                    return ip
            except Exception:
                text = resp.text.strip()
                if text:
                    return text
        except Exception:
            continue
    return None


def _build_proxy_test_mapping(proxy_url: str, *, include_https: bool) -> dict:
    proxies = {"http": proxy_url}
    if include_https:
        proxies["https"] = proxy_url
    return proxies


def probe_proxy_http_basic(proxy_url: str, timeout: int = 8) -> tuple[bool, str]:
    from curl_cffi import requests as cffi_requests
    try:
        resp = cffi_requests.get(
            "http://ip234.in/ip.json",
            proxies=_build_proxy_test_mapping(proxy_url, include_https=True),
            timeout=timeout,
            impersonate="chrome110",
        )
        return resp.status_code == 200, (resp.text or "")[:200]
    except Exception as exc:
        return False, str(exc)


def probe_proxy_https_openai(proxy_url: str, timeout: int = 8) -> tuple[bool, str]:
    from curl_cffi import requests as cffi_requests
    try:
        resp = cffi_requests.get(
            "https://auth.openai.com/",
            proxies=_build_proxy_test_mapping(proxy_url, include_https=True),
            timeout=timeout,
            impersonate="chrome110",
            allow_redirects=False,
        )
        return True, str(resp.status_code)
    except Exception as exc:
        return False, str(exc)


def select_best_dynamic_proxy(candidates: list[str]) -> Optional[str]:
    for proxy_url in candidates:
        ok_http, detail_http = probe_proxy_http_basic(proxy_url)
        if not ok_http:
            logger.warning("动态代理候选不可用(HTTP): %s | %s", proxy_url, detail_http)
            continue
        ok_https, detail_https = probe_proxy_https_openai(proxy_url)
        if not ok_https:
            logger.warning("动态代理候选不可用(HTTPS): %s | %s", proxy_url, detail_https)
            continue
        logger.info("动态代理候选命中可用节点: %s", proxy_url)
        return proxy_url
    return None


def get_haiwaidaili_white_list(appid: str, appkey: str) -> list[str]:
    from curl_cffi import requests as cffi_requests
    url = f"https://www.haiwaidaili.net/index/user/white_list/appid/{appid}/appkey/{appkey}.html?format=simple"
    resp = cffi_requests.get(url, timeout=15, impersonate="chrome110")
    resp.raise_for_status()
    try:
        data = resp.json()
        rows = data.get("data", []) if isinstance(data, dict) else []
        return [str(x).strip() for x in rows if str(x).strip()]
    except Exception:
        text = resp.text.strip()
        return [line.strip() for line in text.splitlines() if line.strip()]


def add_haiwaidaili_white_ip(appid: str, appkey: str, ip: str) -> bool:
    from curl_cffi import requests as cffi_requests
    url = f"https://www.haiwaidaili.net/index/user/save_white/appid/{appid}/appkey/{appkey}.html"
    resp = cffi_requests.get(url, params={"white": ip}, timeout=15, impersonate="chrome110")
    resp.raise_for_status()
    try:
        data = resp.json()
        return str(data.get("code")) == "0"
    except Exception:
        return "成功" in (resp.text or "")


def ensure_haiwaidaili_whitelist(appid: str, appkey: str) -> tuple[bool, str]:
    ip = fetch_public_ip()
    if not ip:
        return False, "无法获取当前外网 IP"
    try:
        current = get_haiwaidaili_white_list(appid, appkey)
    except Exception as exc:
        return False, f"获取白名单失败: {exc}"
    if ip in current:
        return True, f"当前外网 IP 已在白名单中: {ip}"
    try:
        ok = add_haiwaidaili_white_ip(appid, appkey, ip)
        if ok:
            return True, f"已自动加入白名单: {ip}"
        return False, f"加入白名单失败: {ip}"
    except Exception as exc:
        return False, f"加入白名单失败: {exc}"


def get_proxy_url_for_task() -> Optional[str]:
    """
    为注册任务获取代理 URL。
    优先使用动态代理（若启用），否则使用静态代理配置。

    Returns:
        代理 URL 或 None
    """
    from ..config.settings import get_settings
    settings = get_settings()

    # 优先使用动态代理
    if settings.proxy_dynamic_enabled:
        if getattr(settings, "proxy_dynamic_mode", "api") == "account":
            password = settings.proxy_dynamic_password.get_secret_value() if settings.proxy_dynamic_password else ""
            proxy_url = build_account_proxy_url(
                scheme=getattr(settings, "proxy_dynamic_scheme", "http"),
                host=getattr(settings, "proxy_dynamic_host", ""),
                port=getattr(settings, "proxy_dynamic_port", 1456),
                username=getattr(settings, "proxy_dynamic_username", ""),
                password=password,
                country=getattr(settings, "proxy_dynamic_country", ""),
            )
            if proxy_url:
                return proxy_url
            logger.warning("账密动态代理配置不完整，回退到静态代理")
        elif settings.proxy_dynamic_api_url:
            provider = str(getattr(settings, "proxy_dynamic_provider", "generic") or "generic").strip().lower()
            if provider == "haiwaidaili":
                provider_appid = getattr(settings, "proxy_dynamic_provider_appid", "")
                provider_appkey = settings.proxy_dynamic_provider_appkey.get_secret_value() if getattr(settings, "proxy_dynamic_provider_appkey", None) else ""
                if provider_appid and provider_appkey:
                    ok, msg = ensure_haiwaidaili_whitelist(provider_appid, provider_appkey)
                    if not ok:
                        logger.warning("动态代理白名单检查失败: %s", msg)
                    else:
                        logger.info("动态代理白名单检查成功: %s", msg)
            api_key = settings.proxy_dynamic_api_key.get_secret_value() if settings.proxy_dynamic_api_key else ""
            candidates = fetch_dynamic_proxy_candidates(
                api_url=settings.proxy_dynamic_api_url,
                api_key=api_key,
                api_key_header=settings.proxy_dynamic_api_key_header,
                result_field=settings.proxy_dynamic_result_field,
                provider=provider,
                default_scheme=getattr(settings, "proxy_dynamic_scheme", "http"),
            )
            proxy_url = select_best_dynamic_proxy(candidates) if candidates else None
            if proxy_url:
                return proxy_url
            logger.warning("动态代理获取失败，回退到静态代理")

    # 使用静态代理
    return settings.proxy_url
