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
    protocol: int = 0,
    pattern: int = 0,
    valid_code: int = 0,
) -> str:
    from urllib.parse import urlencode
    # SeekProxy 新版文档使用 HTTPS out-api。旧地址
    # http://api.seekproxy.com:8000/api/get-ips 在部分远程机房会被 reset。
    base = "https://www.seekproxy.com/out-api/get-ips"
    params = {
        "trade_no": trade_no,
        "key": key,
        "auth_type": int(auth_type or 2),
    }
    # 文档示例只有 trade_no/key/auth_type；以下参数为控制台兼容项，
    # 仅在用户显式配置时追加，避免新接口因未知空参数拒绝请求。
    if int(ip_count or 1) > 1:
        params["ip_count"] = int(ip_count or 1)
    if country:
        params["country"] = country
    if state:
        params["state"] = state
    if city:
        params["city"] = city
    if fmt not in (None, "", 1, "1"):
        params["format"] = int(fmt or 1)
    if break_type not in (None, "", 1, "1"):
        params["break_type"] = int(break_type or 1)
    if hold_time not in (None, "", 5, "5"):
        params["time"] = int(hold_time or 5)
    if int(auth_type or 2) == 1:
        params["protocol"] = int(protocol or 0)
        params["pattern"] = int(pattern or 0)
        params["valid_code"] = int(valid_code or 0)
    return f"{base}?{urlencode(params)}"


def _redact_url(url: str) -> str:
    """日志用：脱敏 URL 里的 key/password/token。"""
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        parts = urlsplit(str(url or ""))
        params = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in {"key", "token", "password", "appkey", "api_key"} and value:
                value = value[:3] + "***" + value[-3:] if len(value) > 8 else "***"
            params.append((key, value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))
    except Exception:
        return re.sub(r'((?:key|token|password|appkey|api_key)=)[^&]+', r'\1***', str(url or ""), flags=re.I)


def build_account_proxy_url(
    *,
    scheme: str = "http",
    host: str,
    port: int,
    username: str,
    password: str,
    country: str = "",
    session_suffix: Optional[str] = None,
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
        session_value = session_suffix or str(int(__import__('time').time() * 1000000))
        proxy_user = f"{username}-country-{country}-session-{session_value}"
    safe_user = quote(proxy_user, safe="")
    safe_password = quote(password, safe="")
    return f"{scheme}://{safe_user}:{safe_password}@{host}:{int(port)}"


def _normalize_proxy_url(proxy_url: str, *, default_scheme: str = "http") -> Optional[str]:
    proxy_url = str(proxy_url or "").strip()
    if not proxy_url:
        return None
    if re.match(r'^(http|https|socks5|socks5h)://', proxy_url):
        return proxy_url
    # SeekProxy 提取格式：host:port:username:password。
    # requests 需要转换成：http://username:password@host:port
    parts = proxy_url.split(":")
    if len(parts) == 4 and "@" not in proxy_url:
        host, port, username, password = parts
        if host and port and username and password and port.isdigit():
            safe_user = quote(username, safe="")
            safe_password = quote(password, safe="")
            return f"{default_scheme}://{safe_user}:{safe_password}@{host}:{int(port)}"
    scheme = (default_scheme or "http").strip().lower()
    if scheme not in {"http", "https", "socks5", "socks5h"}:
        scheme = "http"
    return f"{scheme}://{proxy_url}"


def normalize_proxy_url_for_requests(proxy_url: str, *, default_scheme: str = "http") -> Optional[str]:
    """
    统一成 requests/curl_cffi proxies 可用格式。

    HTTP 代理访问 HTTPS 站点时，proxies["https"] 仍然应该是
    http://user:pass@host:port，由 requests 通过 CONNECT 建隧道；
    只有 SOCKS5 代理才使用 socks5h://。
    """
    return _normalize_proxy_url(proxy_url, default_scheme=default_scheme)


def build_proxy_requests_mapping(proxy_url: str, *, include_https: bool = True, default_scheme: str = "http") -> dict:
    normalized = normalize_proxy_url_for_requests(proxy_url, default_scheme=default_scheme)
    if not normalized:
        return {}
    proxies = {"http": normalized}
    if include_https:
        proxies["https"] = normalized
    return proxies


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
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        try:
            import json
            data = json.loads(text)
            rows = data.get("data") if isinstance(data, dict) else data
            if isinstance(rows, list) and rows:
                return _parse_seekproxy_proxy_response(str(rows[0]))
            if isinstance(rows, str):
                return _parse_seekproxy_proxy_response(rows)
        except Exception:
            pass
    first_line = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    if not first_line:
        return None
    parts = first_line.split(":")
    if len(parts) == 4:
        host, port, username, password = parts
        safe_user = quote(username, safe="")
        safe_password = quote(password, safe="")
        return f"http://{safe_user}:{safe_password}@{host}:{int(port)}"
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
        raw_text = str(text or "").strip()
        if raw_text.startswith("{") or raw_text.startswith("["):
            try:
                import json
                data = json.loads(raw_text)
                rows = data.get("data") if isinstance(data, dict) else data
                if isinstance(rows, list):
                    for row in rows:
                        proxy = _parse_seekproxy_proxy_response(str(row))
                        if proxy:
                            candidates.append(proxy)
                    return candidates
                if isinstance(rows, str):
                    proxy = _parse_seekproxy_proxy_response(rows)
                    return [proxy] if proxy else []
            except Exception:
                pass
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
    headers = {}
    if api_key:
        headers[api_key_header] = api_key

    last_error = None
    max_attempts = max(1, int(retries or 1))
    for attempt in range(1, max_attempts + 1):
        try:
            response = _http_get_dynamic_api(
                api_url,
                headers=headers,
                timeout=10,
                attempt=attempt,
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
            logger.error("获取动态代理失败(第 %s/%s 次): %s | url=%s", attempt, max_attempts, e, _redact_url(api_url))
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
    headers = {}
    if api_key:
        headers[api_key_header] = api_key

    last_error = None
    max_attempts = max(1, int(retries or 1))
    for attempt in range(1, max_attempts + 1):
        try:
            response = _http_get_dynamic_api(
                api_url,
                headers=headers,
                timeout=10,
                attempt=attempt,
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
            logger.error("获取动态代理候选失败(第 %s/%s 次): %s | url=%s", attempt, max_attempts, e, _redact_url(api_url))
            continue
    logger.error(f"获取动态代理候选最终失败: {last_error}")
    return []


def _http_get_dynamic_api(api_url: str, *, headers: dict, timeout: int, attempt: int):
    """
    动态代理“提取 API”请求。
    SeekProxy 文档示例是普通 HTTPS GET；这里优先用 requests，
    若远程环境 requests 不兼容，再回退 curl_cffi。
    """
    last_error = None
    for client_name in ("requests", "curl_cffi"):
        try:
            if client_name == "requests":
                import requests
                return requests.get(api_url, headers=headers, timeout=timeout)
            from curl_cffi import requests as cffi_requests
            return cffi_requests.get(api_url, headers=headers, timeout=timeout, impersonate="chrome110")
        except Exception as exc:
            last_error = exc
            logger.warning(
                "动态代理 API 请求失败，准备尝试下一个客户端: client=%s attempt=%s error=%s url=%s",
                client_name,
                attempt,
                exc,
                _redact_url(api_url),
            )
    raise last_error


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
    return build_proxy_requests_mapping(proxy_url, include_https=include_https)


def probe_proxy_http_basic(proxy_url: str, timeout: int = 8) -> tuple[bool, str]:
    import requests
    try:
        resp = requests.get(
            "http://api.ipify.org?format=json",
            proxies=_build_proxy_test_mapping(proxy_url, include_https=False),
            timeout=timeout,
            headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"},
            verify=False,
        )
        return resp.status_code == 200, (resp.text or "")[:200]
    except Exception as exc:
        return False, str(exc)


def probe_proxy_https_openai(proxy_url: str, timeout: int = 8) -> tuple[bool, str]:
    import requests
    try:
        resp = requests.get(
            "https://auth.openai.com/",
            proxies=_build_proxy_test_mapping(proxy_url, include_https=True),
            timeout=timeout,
            headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"},
            allow_redirects=False,
            verify=False,
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
            profiles = dict(getattr(settings, "proxy_dynamic_profiles", {}) or {})
            provider = str(getattr(settings, "proxy_dynamic_provider", "generic") or "generic").strip().lower()
            mode = str(getattr(settings, "proxy_dynamic_mode", "account") or "account").strip().lower()
            profile = profiles.get(f"{provider}::{mode}", {}) if profiles else {}
            password = profile.get("password") if isinstance(profile, dict) else None
            if not password:
                password = settings.proxy_dynamic_password.get_secret_value() if settings.proxy_dynamic_password else ""
            proxy_url = build_account_proxy_url(
                scheme=profile.get("scheme") if isinstance(profile, dict) and profile.get("scheme") else getattr(settings, "proxy_dynamic_scheme", "http"),
                host=profile.get("host") if isinstance(profile, dict) and profile.get("host") else getattr(settings, "proxy_dynamic_host", ""),
                port=profile.get("port") if isinstance(profile, dict) and profile.get("port") else getattr(settings, "proxy_dynamic_port", 1456),
                username=profile.get("username") if isinstance(profile, dict) and profile.get("username") else getattr(settings, "proxy_dynamic_username", ""),
                password=password,
                country=profile.get("country") if isinstance(profile, dict) and profile.get("country") else getattr(settings, "proxy_dynamic_country", ""),
            )
            if proxy_url:
                return proxy_url
            logger.warning("账密动态代理配置不完整，回退到静态代理")
        else:
            request_cfg = build_dynamic_api_provider_request(settings)
            provider = request_cfg["provider"]
            if provider == "haiwaidaili":
                provider_appid = getattr(settings, "proxy_dynamic_provider_appid", "")
                provider_appkey = settings.proxy_dynamic_provider_appkey.get_secret_value() if getattr(settings, "proxy_dynamic_provider_appkey", None) else ""
                if provider_appid and provider_appkey:
                    ok, msg = ensure_haiwaidaili_whitelist(provider_appid, provider_appkey)
                    if not ok:
                        logger.warning("动态代理白名单检查失败: %s", msg)
                    else:
                        logger.info("动态代理白名单检查成功: %s", msg)
            candidates = fetch_dynamic_proxy_candidates(
                api_url=request_cfg["api_url"],
                api_key=request_cfg["api_key"],
                api_key_header=request_cfg["api_key_header"],
                result_field=request_cfg["result_field"],
                provider=provider,
                default_scheme=request_cfg["default_scheme"],
            )
            proxy_url = select_best_dynamic_proxy(candidates) if candidates else None
            if proxy_url:
                return proxy_url
            logger.warning("动态代理获取失败，回退到静态代理")

    # 使用静态代理
    return settings.proxy_url


def build_dynamic_api_provider_request(settings) -> dict:
    profiles = dict(getattr(settings, "proxy_dynamic_profiles", {}) or {})
    provider = str(getattr(settings, "proxy_dynamic_provider", "generic") or "generic").strip().lower()
    mode = str(getattr(settings, "proxy_dynamic_mode", "api") or "api").strip().lower()
    profile = profiles.get(f"{provider}::{mode}", {}) if profiles else {}

    def profile_or_compat(key: str, compat_attr: str, default=None):
        value = profile.get(key) if isinstance(profile, dict) else None
        if value not in (None, ""):
            return value
        return getattr(settings, compat_attr, default)

    provider = str(getattr(settings, "proxy_dynamic_provider", "generic") or "generic").strip().lower()
    if provider == "seekproxy":
        seekproxy_key = profile.get("key") if isinstance(profile, dict) else None
        if not seekproxy_key:
            secret = getattr(settings, "proxy_dynamic_seekproxy_key", None)
            seekproxy_key = secret.get_secret_value().strip() if secret else ""
        return {
            "provider": "seekproxy",
            "api_url": build_seekproxy_api_url(
                trade_no=profile_or_compat("trade_no", "proxy_dynamic_seekproxy_trade_no", ""),
                key=seekproxy_key,
                auth_type=profile_or_compat("auth_type", "proxy_dynamic_seekproxy_auth_type", 2),
                ip_count=profile_or_compat("ip_count", "proxy_dynamic_seekproxy_ip_count", 1),
                country=profile_or_compat("country", "proxy_dynamic_country", ""),
                state=profile_or_compat("state", "proxy_dynamic_seekproxy_state", ""),
                city=profile_or_compat("city", "proxy_dynamic_seekproxy_city", ""),
                fmt=1,
                break_type=profile_or_compat("break_type", "proxy_dynamic_seekproxy_break_type", 1),
                hold_time=profile_or_compat("time", "proxy_dynamic_seekproxy_time", 5),
                protocol=profile_or_compat("protocol", "proxy_dynamic_seekproxy_protocol", 0),
                pattern=profile_or_compat("pattern", "proxy_dynamic_seekproxy_pattern", 0),
                valid_code=profile_or_compat("valid_code", "proxy_dynamic_seekproxy_valid_code", 0),
            ),
            "api_key": "",
            "api_key_header": "X-API-Key",
            "result_field": "",
            "default_scheme": "http",
        }
    if provider == "haiwaidaili":
        api_key = profile.get("api_key") if isinstance(profile, dict) else None
        if not api_key:
            secret = getattr(settings, "proxy_dynamic_api_key", None)
            api_key = secret.get_secret_value().strip() if secret else ""
        return {
            "provider": "haiwaidaili",
            "api_url": profile_or_compat("api_url", "proxy_dynamic_api_url", ""),
            "api_key": api_key,
            "api_key_header": profile_or_compat("api_key_header", "proxy_dynamic_api_key_header", "X-API-Key"),
            "result_field": profile_or_compat("result_field", "proxy_dynamic_result_field", ""),
            "default_scheme": profile_or_compat("scheme", "proxy_dynamic_scheme", "http"),
        }
    api_key = profile.get("api_key") if isinstance(profile, dict) else None
    if not api_key:
        secret = getattr(settings, "proxy_dynamic_api_key", None)
        api_key = secret.get_secret_value().strip() if secret else ""
    return {
        "provider": "generic",
        "api_url": profile_or_compat("api_url", "proxy_dynamic_api_url", ""),
        "api_key": api_key,
        "api_key_header": profile_or_compat("api_key_header", "proxy_dynamic_api_key_header", "X-API-Key"),
        "result_field": profile_or_compat("result_field", "proxy_dynamic_result_field", ""),
        "default_scheme": profile_or_compat("scheme", "proxy_dynamic_scheme", "http"),
    }
