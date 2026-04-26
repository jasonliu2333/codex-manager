"""
OpenAI MFA 识别与 TOTP 验证辅助。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import struct
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple


_AUTH_DOMAINS = ["auth.openai.com", ".openai.com", "openai.com"]


def _normalize_totp_secret(secret: str) -> str:
    return re.sub(r"[^A-Z2-7]", "", str(secret or "").upper())


def generate_totp_code(secret: str, for_time: Optional[int] = None, period: int = 30, digits: int = 6) -> str:
    normalized = _normalize_totp_secret(secret)
    if not normalized:
        raise ValueError("空的 MFA 密钥")
    raw = base64.b32decode(normalized, casefold=True)
    now = int(for_time if for_time is not None else time.time())
    counter = now // period
    msg = struct.pack(">Q", counter)
    digest = hmac.new(raw, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    code = str(code_int % (10 ** digits)).zfill(digits)
    return code


def is_mfa_challenge(page_type: str = "", continue_url: str = "", payload: Any = None) -> bool:
    candidates: List[str] = []
    if page_type:
        candidates.append(str(page_type))
    if continue_url:
        candidates.append(str(continue_url))
    if isinstance(payload, dict):
        candidates.extend(str(k) for k in payload.keys())
        page = payload.get("page")
        if isinstance(page, dict):
            candidates.extend(str(v) for v in page.values() if v is not None)
    text = "\n".join(candidates).lower()
    return (
        "mfa-challenge" in text
        or "mfa_challenge" in text
        or "multifactor" in text
        or "two_factor" in text
        or "totp" in text
    )


def _get_engine_mfa_secret(engine: Any) -> str:
    for attr in ("mfa_secret", "mfa_totp_secret"):
        value = str(getattr(engine, attr, "") or "").strip()
        if value:
            return value
    extra_data = getattr(engine, "account_extra_data", None)
    if isinstance(extra_data, dict):
        value = str(extra_data.get("mfa_totp_secret") or "").strip()
        if value:
            return value
    return ""


def _extract_json_string_candidates(text: str) -> List[str]:
    results = set()
    if not text:
        return []
    pattern = r"([\"'])(/api/[^\"']*mfa[^\"']*)\1"
    for match in re.findall(pattern, text, flags=re.I):
        try:
            results.add(match[1])
        except Exception:
            pass
    return list(results)


def _extract_form_action(challenge_url: str, html: str) -> Optional[str]:
    if not html:
        return None
    patterns = [
        r'<form[^>]+action=["\']([^"\']*mfa[^"\']*)["\']',
        r'action["\']?\s*:\s*["\']([^"\']*mfa[^"\']*)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I)
        if match:
            return urllib.parse.urljoin(challenge_url, match.group(1))
    return None


def _extract_hidden_inputs(html: str) -> Dict[str, str]:
    hidden: Dict[str, str] = {}
    if not html:
        return hidden
    input_pattern = re.compile(r'<input[^>]+type=["\']hidden["\'][^>]*>', flags=re.I)
    for tag in input_pattern.findall(html):
        name_match = re.search(r'name=["\']([^"\']+)["\']', tag, flags=re.I)
        if not name_match:
            continue
        value_match = re.search(r'value=["\']([^"\']*)["\']', tag, flags=re.I)
        hidden[name_match.group(1)] = value_match.group(1) if value_match else ''
    return hidden


def _extract_next_data_json(html: str) -> Any:
    if not html:
        return None
    patterns = [
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        r'<script[^>]*>\s*window\.__NEXT_DATA__\s*=\s*(\{.*?\})\s*</script>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I | re.S)
        if not match:
            continue
        raw = match.group(1).strip()
        try:
            return json.loads(raw)
        except Exception:
            continue
    return None


def _walk_json(obj: Any, path: str = '') -> List[Tuple[str, Any]]:
    items: List[Tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_path = f'{path}.{k}' if path else str(k)
            items.append((new_path, v))
            items.extend(_walk_json(v, new_path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            new_path = f'{path}[{i}]'
            items.append((new_path, v))
            items.extend(_walk_json(v, new_path))
    return items


def _extract_page_hints(challenge_url: str, html: str) -> Tuple[List[str], Dict[str, str], List[Dict[str, str]]]:
    endpoints: List[str] = []
    hidden = _extract_hidden_inputs(html)
    factor_hints: List[Dict[str, str]] = []

    form_action = _extract_form_action(challenge_url, html)
    if form_action:
        endpoints.append(form_action)

    next_data = _extract_next_data_json(html)
    for path, value in _walk_json(next_data):
        if isinstance(value, str):
            value_str = value.strip()
            if '/api/' in value_str and 'mfa' in value_str.lower():
                full = urllib.parse.urljoin(challenge_url, value_str)
                if full not in endpoints:
                    endpoints.append(full)
            key = path.lower()
            if any(token in key for token in ('factor_id', 'factorid')):
                hint = {'factor_id': value_str, 'factor_type': 'totp'}
                if hint not in factor_hints:
                    factor_hints.append(hint)
            if any(token in key for token in ('challenge_id', 'challengeid', 'mfa_challenge_id')):
                hidden.setdefault('challenge_id', value_str)
        elif isinstance(value, dict):
            factor_id = str(value.get('id') or value.get('factor_id') or '').strip()
            factor_type = str(value.get('type') or value.get('factor_type') or value.get('kind') or '').strip()
            if factor_id or factor_type:
                hint = {'factor_id': factor_id, 'factor_type': factor_type or 'totp'}
                if hint not in factor_hints:
                    factor_hints.append(hint)

    return endpoints, hidden, factor_hints


def _extract_response_excerpt(resp: Any) -> str:
    try:
        text = (resp.text or '').strip()
        if text:
            return text[:300]
        data = resp.json()
        return json.dumps(data, ensure_ascii=False)[:300]
    except Exception:
        return ''


def _extract_id_candidates(challenge_url: str, hidden_inputs: Dict[str, str], factor_hints: List[Dict[str, str]]) -> List[str]:
    candidates: List[str] = []

    for key in ("id", "factor_id", "challenge_id", "mfa_challenge_id"):
        value = str(hidden_inputs.get(key) or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    for hint in factor_hints:
        for key in ("id", "factor_id"):
            value = str(hint.get(key) or hint.get("factor_id") or "").strip()
            if value and value not in candidates:
                candidates.append(value)

    parsed = urllib.parse.urlparse(challenge_url)
    path_parts = [part for part in parsed.path.split('/') if part]
    if path_parts:
        tail = path_parts[-1].strip()
        if tail and tail.lower() != 'mfa-challenge' and tail not in candidates:
            candidates.append(tail)

    return candidates


def _candidate_mfa_endpoints(challenge_url: str, html: str = "") -> List[str]:
    defaults = [
        "/api/accounts/mfa/verify",
    ]
    extracted = _extract_json_string_candidates(html)
    hinted_endpoints, _, _ = _extract_page_hints(challenge_url, html)
    urls = []
    for item in hinted_endpoints + extracted + defaults:
        full = urllib.parse.urljoin(challenge_url, item)
        if full not in urls:
            urls.append(full)
    return urls


def _parse_cookie_json(engine: Any, name: str) -> Any:
    try:
        getter = getattr(engine, "_get_cookie_value", None)
        if not callable(getter):
            return None
        raw = getter(name, domains=_AUTH_DOMAINS)
        if not raw:
            return None
        text = urllib.parse.unquote(str(raw))
        for candidate in (text, text.strip('"')):
            try:
                return json.loads(candidate)
            except Exception:
                pass
    except Exception:
        return None
    return None


def _factor_hints(engine: Any) -> List[Dict[str, str]]:
    hints: List[Dict[str, str]] = []
    for cookie_name in ("mfa_challenge_factors", "mfa_factors"):
        data = _parse_cookie_json(engine, cookie_name)
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                factor_id = str(item.get("id") or item.get("factor_id") or "").strip()
                factor_type = str(item.get("type") or item.get("factor_type") or item.get("kind") or "").strip()
                hint = {"factor_id": factor_id, "factor_type": factor_type}
                if hint not in hints:
                    hints.append(hint)
        elif isinstance(data, dict):
            factor_id = str(data.get("id") or data.get("factor_id") or "").strip()
            factor_type = str(data.get("type") or data.get("factor_type") or data.get("kind") or "").strip()
            hint = {"factor_id": factor_id, "factor_type": factor_type}
            if hint not in hints:
                hints.append(hint)
    return hints


def _build_payload_variants(code: str, factor_hints: List[Dict[str, str]], hidden_inputs: Optional[Dict[str, str]] = None, id_candidates: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """按当前已验证成功的最小提交格式构造 payload。

    从日志可知：
    - 缺少 `id` 会返回 400 Missing required parameter: 'id'
    - 紧接着下一组带上 `id` 后即返回 200

    因此当前优先锁定为：
    {"type": "totp", "code": <code>, "id": <challenge_id>}
    """
    hidden_inputs = dict(hidden_inputs or {})
    id_candidates = [str(x).strip() for x in (id_candidates or []) if str(x).strip()]
    payloads: List[Dict[str, Any]] = []

    for id_value in id_candidates:
        payload = {**hidden_inputs, "type": "totp", "id": id_value, "code": code}
        if payload not in payloads:
            payloads.append(payload)

    # 没拿到 id 时保留一个最小兜底，便于继续观察服务端返回
    fallback = {**hidden_inputs, "type": "totp", "code": code}
    if fallback not in payloads:
        payloads.append(fallback)

    return payloads


def handle_openai_mfa_challenge(engine: Any, continue_url: str = "") -> Optional[str]:
    challenge_url = continue_url or "https://auth.openai.com/mfa-challenge"
    if challenge_url.startswith("/"):
        challenge_url = urllib.parse.urljoin("https://auth.openai.com", challenge_url)

    setattr(engine, "_last_mfa_required", True)
    setattr(engine, "_last_mfa_challenge_url", challenge_url)
    engine._log("14.2 注册后 OAuth 需要 MFA 二次验证...")

    secret = _get_engine_mfa_secret(engine)
    if not secret:
        msg = "该账号需要 MFA 二次验证，但未配置 MFA 密钥"
        setattr(engine, "_last_mfa_error_message", msg)
        engine._log(f"MFA: {msg}", "warning")
        return None

    candidate_codes: List[str] = []
    try:
        for offset in (-30, 0, 30):
            code = generate_totp_code(secret, for_time=int(time.time()) + offset)
            if code not in candidate_codes:
                candidate_codes.append(code)
    except Exception as exc:
        msg = f"MFA 密钥不可用: {exc}"
        setattr(engine, "_last_mfa_error_message", msg)
        engine._log(f"MFA: {msg}", "error")
        return None

    engine._log(f"MFA: 已根据密钥生成 TOTP 验证码候选 {candidate_codes}")

    try:
        challenge_resp = engine.session.get(
            challenge_url,
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "referer": "https://auth.openai.com/log-in/password",
                "user-agent": engine.http_client.default_headers.get("User-Agent", ""),
            },
            allow_redirects=True,
            timeout=30,
        )
        if getattr(challenge_resp, "url", None):
            final_url = str(challenge_resp.url)
            if "code=" in final_url and "state=" in final_url:
                return final_url
        html = challenge_resp.text or ""
        engine._log(f"MFA challenge 页面状态: {challenge_resp.status_code}")
    except Exception as exc:
        html = ""
        engine._log(f"MFA challenge 页面访问失败: {exc}", "warning")

    page_endpoints, hidden_inputs, page_factor_hints = _extract_page_hints(challenge_url, html)
    factor_hints = _factor_hints(engine)
    for hint in page_factor_hints:
        if hint not in factor_hints:
            factor_hints.append(hint)
    endpoints = []
    preferred_endpoint = urllib.parse.urljoin(challenge_url, "/api/accounts/mfa/verify")
    for endpoint in [preferred_endpoint] + page_endpoints + _candidate_mfa_endpoints(challenge_url, html):
        if endpoint not in endpoints:
            endpoints.append(endpoint)
    id_candidates = _extract_id_candidates(challenge_url, hidden_inputs, factor_hints)
    engine._log(f"MFA: 候选提交端点 {endpoints}")
    if hidden_inputs:
        engine._log(f"MFA: challenge 隐藏字段 {hidden_inputs}")
    if factor_hints:
        engine._log(f"MFA: factor 提示 {factor_hints}")
    if id_candidates:
        engine._log(f"MFA: id 候选 {id_candidates}")

    for code in candidate_codes:
        payloads = _build_payload_variants(code, factor_hints, hidden_inputs, id_candidates)
        if endpoints:
            engine._log(f"MFA: 当前验证码 {code}，尝试 {len(payloads)} 组精简 payload")
        for endpoint in endpoints:
            for payload in payloads:
                try:
                    resp = engine.session.post(
                        endpoint,
                        headers=engine._oauth_json_headers(challenge_url),
                        data=json.dumps(payload),
                        allow_redirects=False,
                        timeout=30,
                    )
                    excerpt = _extract_response_excerpt(resp)
                    engine._log(f"MFA 提交状态: {resp.status_code} {endpoint}")
                    if excerpt and resp.status_code >= 400:
                        engine._log(f"MFA 提交响应: {excerpt}", "warning")

                    location = resp.headers.get("Location") or ""
                    if location:
                        next_url = urllib.parse.urljoin(endpoint, location)
                        if "code=" in next_url and "state=" in next_url:
                            return next_url
                        if "mfa-challenge" not in next_url.lower():
                            return next_url

                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                        except Exception:
                            data = None
                        if isinstance(data, dict):
                            extractor = getattr(engine, "_extract_continue_url_from_payload", None)
                            if callable(extractor):
                                next_url = extractor(data)
                                if next_url:
                                    return next_url
                            page = data.get("page")
                            if isinstance(page, dict) and str(page.get("type") or "").lower().startswith("mfa"):
                                continue
                        low_text = (resp.text or "")[:200].lower()
                        if "invalid" in low_text or "incorrect" in low_text or "expired" in low_text:
                            continue
                except Exception as exc:
                    engine._log(f"MFA 提交异常: {exc}", "warning")
                    continue

    msg = "MFA 自动验证失败，当前 challenge 未拿到 continue_url"
    setattr(engine, "_last_mfa_error_message", msg)
    engine._log(f"MFA: {msg}", "warning")
    return None
