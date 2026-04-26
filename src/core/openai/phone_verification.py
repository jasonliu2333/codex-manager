"""OpenAI add-phone 流程与 HeroSMS 的桥接。"""

from __future__ import annotations

import json
import threading
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional

from ...config.settings import get_settings
from ...database import crud
from ...database.session import get_db
from ..herosms_client import HeroSMSActivation, HeroSMSClient, HeroSMSConfig


HEROSMS_REUSE_POOL_KEY = "herosms.reuse_pool"
_REUSE_POOL_LOCK = threading.RLock()


def is_add_phone_challenge(page_type: str = "", continue_url: str = "", payload: Any = None) -> bool:
    text = f"{page_type} {continue_url}".lower()
    if "add_phone" in text or "add-phone" in text or "phone_verification" in text:
        return True
    if isinstance(payload, dict):
        page = payload.get("page")
        if isinstance(page, dict):
            page_type = str(page.get("type") or "")
            if is_add_phone_challenge(page_type, "", None):
                return True
        for key in ("continue_url", "next_url", "redirect_url"):
            value = str(payload.get(key) or "")
            if is_add_phone_challenge("", value, None):
                return True
    return False


def handle_openai_add_phone_challenge(engine: Any, continue_url: str = "") -> Optional[str]:
    """处理 auth.openai.com/add-phone，并返回下一步 continue_url。"""

    runtime = _load_herosms_runtime_settings()
    if not runtime.get("enabled", False):
        engine._log("检测到 add-phone，但 HeroSMS 未启用，跳过手机验证", "warning")
        return None

    api_key = _get_saved_herosms_api_key()
    if not api_key:
        engine._log("检测到 add-phone，但未配置 HeroSMS API Key", "error")
        return None

    if continue_url:
        continue_url = urllib.parse.urljoin("https://auth.openai.com", continue_url)
        try:
            engine.session.get(
                continue_url,
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "referer": "https://auth.openai.com/email-verification",
                    "user-agent": engine.http_client.default_headers.get("User-Agent", ""),
                },
                allow_redirects=True,
                timeout=30,
            )
        except Exception as exc:
            engine._log(f"add-phone 页面预加载失败，将继续尝试 API: {exc}", "warning")

    cfg = HeroSMSConfig(
        api_key=api_key,
        service=runtime.get("service", "dr") or "dr",
        country=int(runtime.get("country", 187) or 187),
        max_price=_positive_float_or_none(runtime.get("max_price", -1)),
        proxy=(runtime.get("proxy", "") or getattr(engine, "proxy_url", None) or None),
        timeout=int(runtime.get("timeout", 30) or 30),
    )
    client = HeroSMSClient(cfg)
    max_number_attempts = max(1, int(runtime.get("max_number_attempts", 1) or 1))
    target_number_index = max(1, int(runtime.get("target_number_index", 1) or 1))
    lowest_price_first = bool(runtime.get("lowest_price_first", True))
    price_relax_enabled = bool(runtime.get("price_relax_enabled", True))
    price_relax_max_multiplier = max(1, int(runtime.get("price_relax_max_multiplier", 5) or 5))
    reuse_enabled = bool(runtime.get("reuse_enabled", False))
    reuse_max_uses = max(1, int(runtime.get("reuse_max_uses", 1) or 1))
    resolved_max_price = cfg.max_price
    if lowest_price_first:
        try:
            lowest_price = client.get_lowest_price(service=cfg.service, country=cfg.country)
            if lowest_price and lowest_price > 0:
                resolved_max_price = lowest_price
                engine._log(f"add-phone: 已启用最低价优先，本次使用 maxPrice={lowest_price}")
            else:
                engine._log("add-phone: 未解析到最低价格，回退到默认取号策略", "warning")
        except Exception as exc:
            engine._log(f"add-phone: 查询最低价格失败，回退到默认取号策略: {exc}", "warning")

    last_error: Optional[str] = None
    for number_attempt in range(1, max_number_attempts + 1):
        activation = None
        reused_activation = False
        previous_codes: set[str] = set()
        try:
            reuse_entry = _claim_reusable_activation(cfg.service, cfg.country, reuse_max_uses) if reuse_enabled else None
            if reuse_entry:
                activation = HeroSMSActivation(
                    activation_id=str(reuse_entry["activation_id"]),
                    phone_number=str(reuse_entry["phone_number"]),
                    raw_number=str(reuse_entry.get("raw_number") or reuse_entry["phone_number"]),
                    country_phone_code=str(reuse_entry.get("country_phone_code") or ""),
                    activation_cost=_positive_float_or_none(reuse_entry.get("activation_cost")),
                )
                reused_activation = True
                previous_codes = {str(code).strip() for code in reuse_entry.get("used_codes", []) if str(code).strip()}
                engine._log(
                    f"add-phone: 复用已成功号码 {activation.phone_number} "
                    f"(activation={activation.activation_id}, used={reuse_entry.get('uses', 0)}/{reuse_max_uses})"
                )
            else:
                price_candidates = _build_price_candidates(
                    resolved_max_price,
                    price_relax_enabled=price_relax_enabled,
                    price_relax_max_multiplier=price_relax_max_multiplier,
                )
                last_request_error: Optional[Exception] = None
                for idx, candidate_price in enumerate(price_candidates, start=1):
                    try:
                        price_label = "不限价" if candidate_price is None else str(candidate_price)
                        engine._log(
                            f"add-phone: 正在向 HeroSMS 取号 service={cfg.service}, country={cfg.country}, "
                            f"attempt={number_attempt}/{max_number_attempts}, price_try={idx}/{len(price_candidates)}, maxPrice={price_label}"
                        )
                        activation = client.request_number(max_price=candidate_price)
                        break
                    except Exception as exc:
                        last_request_error = exc
                        err_text = str(exc)
                        if "NO_NUMBERS" in err_text and idx < len(price_candidates):
                            engine._log(f"add-phone: 当前价格档无号，自动放宽价格继续尝试: {err_text}", "warning")
                            continue
                        raise
                if activation is None and last_request_error:
                    raise last_request_error
                engine._log(f"add-phone: 取号成功 {activation.phone_number} (activation={activation.activation_id})")

                if number_attempt < target_number_index:
                    engine._log(f"add-phone: 当前为第 {number_attempt} 个号码，配置要求从第 {target_number_index} 个号码开始使用，跳过当前号码", "warning")
                    client.cancel_activation(activation.activation_id)
                    continue

            headers = _phone_headers(engine, "https://auth.openai.com/add-phone")
            endpoint_settings = get_settings()
            send_url = getattr(endpoint_settings, "openai_add_phone_send_url", "") or "https://auth.openai.com/api/accounts/add-phone/send"
            validate_url = getattr(endpoint_settings, "openai_phone_otp_validate_url", "") or "https://auth.openai.com/api/accounts/phone-otp/validate"
            resend_url = getattr(endpoint_settings, "openai_phone_otp_resend_url", "") or "https://auth.openai.com/api/accounts/phone-otp/resend"

            send_resp = _post_json_with_payload_variants(
                engine,
                send_url,
                headers,
                [
                    {"phone_number": activation.phone_number},
                    {"phone": activation.phone_number},
                    {"phoneNumber": activation.phone_number},
                ],
                label="add-phone 提交手机号",
            )
            if send_resp is None or send_resp.status_code not in (200, 201, 204):
                body = (getattr(send_resp, "text", "") or "")[:300] if send_resp is not None else ""
                raise RuntimeError(f"提交手机号失败: {getattr(send_resp, 'status_code', 'NO_RESPONSE')} {body}")

            if not reused_activation:
                try:
                    client.set_status(activation.activation_id, 1)
                except Exception:
                    pass

            def resend_business_code() -> None:
                resp = engine.session.post(
                    resend_url,
                    headers=headers,
                    data=json.dumps({}),
                    allow_redirects=False,
                    timeout=30,
                )
                engine._log(f"add-phone: 业务侧重发短信状态: {resp.status_code}")
                resp.raise_for_status()

            timeout = int(runtime.get("verify_timeout", 180) or 180)
            poll_interval = int(runtime.get("poll_interval", 3) or 3)
            engine._log(f"add-phone: 等待短信验证码，最多 {timeout} 秒")
            code = client.wait_for_code(
                activation.activation_id,
                timeout=timeout,
                poll_interval=poll_interval,
                resend_business_code=resend_business_code,
                exclude_codes=previous_codes,
                trace_callback=lambda message: engine._log(f"add-phone: HeroSMS 状态: {message}", "debug"),
            )
            if not code:
                raise RuntimeError("等待短信验证码超时")
            engine._log(f"add-phone: 成功获取短信验证码: {code}")

            validate_resp = _post_json_with_payload_variants(
                engine,
                validate_url,
                headers,
                [{"code": code}, {"otp": code}, {"verification_code": code}],
                label="add-phone 校验短信",
            )
            if validate_resp is None or validate_resp.status_code not in (200, 201, 204):
                body = (getattr(validate_resp, "text", "") or "")[:300] if validate_resp is not None else ""
                raise RuntimeError(f"短信验证码校验失败: {getattr(validate_resp, 'status_code', 'NO_RESPONSE')} {body}")

            next_url = _extract_continue_url(engine, validate_resp)
            should_finish = True
            if reuse_enabled:
                should_finish = _record_activation_success(
                    activation,
                    service=cfg.service,
                    country=cfg.country,
                    max_uses=reuse_max_uses,
                    code=code,
                    reused=reused_activation,
                )
                if should_finish:
                    engine._log(f"add-phone: 号码 {activation.phone_number} 已达到复用上限，将结束激活")
                else:
                    try:
                        client.request_resend_sms(activation.activation_id)
                    except Exception as exc:
                        engine._log(f"add-phone: 请求 HeroSMS 继续接收下一条短信失败，后续复用时仍会重试: {exc}", "warning")
                    engine._log(f"add-phone: 号码 {activation.phone_number} 已保存到复用池，后续账号可继续使用")
            if should_finish:
                try:
                    client.set_status(activation.activation_id, 6)
                except Exception:
                    pass
                client.finish_activation(activation.activation_id)
            engine._log("add-phone: 手机验证完成")
            return next_url or "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
        except Exception as exc:
            last_error = str(exc)
            engine._log(f"add-phone: 手机验证失败: {exc}", "error")
            if activation:
                if reused_activation:
                    _discard_reusable_activation(activation.activation_id, last_error)
                    engine._log(f"add-phone: 复用号码 {activation.phone_number} 已因错误废弃", "warning")
                else:
                    client.cancel_activation(activation.activation_id)
            if "等待短信验证码超时" in last_error and number_attempt < max_number_attempts:
                engine._log(f"add-phone: 第 {number_attempt} 个号码短信超时，自动切换到下一个号码重试", "warning")
                continue
            break
    if last_error:
        engine._log(f"add-phone: 最终失败，原因: {last_error}", "error")
    return None


def _phone_headers(engine: Any, referer: str) -> dict:
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "referer": referer,
        "user-agent": engine.http_client.default_headers.get("User-Agent", ""),
    }
    device_id = getattr(engine, "device_id", None)
    if device_id:
        headers["oai-device-id"] = device_id
    return headers


def _post_json_with_payload_variants(engine: Any, url: str, headers: dict, payloads: list[dict], *, label: str):
    last_resp = None
    for idx, payload in enumerate(payloads, start=1):
        resp = engine.session.post(
            url,
            headers=headers,
            data=json.dumps(payload),
            allow_redirects=False,
            timeout=30,
        )
        last_resp = resp
        engine._log(f"{label} 状态({idx}/{len(payloads)}): {resp.status_code}")
        if resp.status_code in (200, 201, 204):
            return resp
        if resp.status_code not in (400, 422):
            return resp
    return last_resp


def _extract_continue_url(engine: Any, response: Any) -> Optional[str]:
    try:
        data = response.json()
    except Exception:
        return None
    extractor = getattr(engine, "_extract_continue_url_from_payload", None)
    if callable(extractor):
        return extractor(data)
    if isinstance(data, dict):
        return str(data.get("continue_url") or data.get("next_url") or "").strip() or None
    return None


def _positive_float_or_none(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _load_herosms_runtime_settings() -> dict:
    settings = get_settings()
    data = {
        "enabled": bool(getattr(settings, "herosms_enabled", False)),
        "service": getattr(settings, "herosms_service", "dr") or "dr",
        "country": int(getattr(settings, "herosms_country", 187) or 187),
        "max_price": getattr(settings, "herosms_max_price", -1),
        "proxy": getattr(settings, "herosms_proxy", "") or "",
        "timeout": int(getattr(settings, "herosms_timeout", 30) or 30),
        "verify_timeout": int(getattr(settings, "herosms_verify_timeout", 180) or 180),
        "poll_interval": int(getattr(settings, "herosms_poll_interval", 3) or 3),
        "max_number_attempts": int(getattr(settings, "herosms_max_number_attempts", 1) or 1),
        "target_number_index": int(getattr(settings, "herosms_target_number_index", 1) or 1),
        "lowest_price_first": bool(getattr(settings, "herosms_lowest_price_first", True)),
        "price_relax_enabled": bool(getattr(settings, "herosms_price_relax_enabled", True)),
        "price_relax_max_multiplier": int(getattr(settings, "herosms_price_relax_max_multiplier", 5) or 5),
        "reuse_enabled": bool(getattr(settings, "herosms_reuse_enabled", False)),
        "reuse_max_uses": int(getattr(settings, "herosms_reuse_max_uses", 1) or 1),
    }
    key_map = {
        "herosms.enabled": ("enabled", lambda v, d=data["enabled"]: _parse_bool(v, d)),
        "herosms.service": ("service", lambda v, d=data["service"]: str(v or d)),
        "herosms.country": ("country", lambda v, d=data["country"]: int(v or d)),
        "herosms.max_price": ("max_price", lambda v, d=data["max_price"]: v if v not in (None, "") else d),
        "herosms.proxy": ("proxy", lambda v, d=data["proxy"]: str(v or d)),
        "herosms.timeout": ("timeout", lambda v, d=data["timeout"]: int(v or d)),
        "herosms.verify_timeout": ("verify_timeout", lambda v, d=data["verify_timeout"]: int(v or d)),
        "herosms.poll_interval": ("poll_interval", lambda v, d=data["poll_interval"]: int(v or d)),
        "herosms.max_number_attempts": ("max_number_attempts", lambda v, d=data["max_number_attempts"]: int(v or d)),
        "herosms.target_number_index": ("target_number_index", lambda v, d=data["target_number_index"]: int(v or d)),
        "herosms.lowest_price_first": ("lowest_price_first", lambda v, d=data["lowest_price_first"]: _parse_bool(v, d)),
        "herosms.price_relax_enabled": ("price_relax_enabled", lambda v, d=data["price_relax_enabled"]: _parse_bool(v, d)),
        "herosms.price_relax_max_multiplier": ("price_relax_max_multiplier", lambda v, d=data["price_relax_max_multiplier"]: int(v or d)),
        "herosms.reuse_enabled": ("reuse_enabled", lambda v, d=data["reuse_enabled"]: _parse_bool(v, d)),
        "herosms.reuse_max_uses": ("reuse_max_uses", lambda v, d=data["reuse_max_uses"]: int(v or d)),
    }
    try:
        with get_db() as db:
            for db_key, (field, caster) in key_map.items():
                setting = crud.get_setting(db, db_key)
                if setting and setting.value is not None:
                    try:
                        data[field] = caster(setting.value)
                    except Exception:
                        pass
    except Exception:
        pass
    return data


def _build_price_candidates(
    base_price: Optional[float],
    *,
    price_relax_enabled: bool,
    price_relax_max_multiplier: int,
) -> list[Optional[float]]:
    if base_price is None or base_price <= 0:
        return [None]
    if not price_relax_enabled:
        return [base_price]

    candidates: list[Optional[float]] = []
    multipliers = [1, 2, 3, price_relax_max_multiplier]
    seen = set()
    for multi in multipliers:
        value = round(base_price * multi, 4)
        if value > 0 and value not in seen:
            seen.add(value)
            candidates.append(value)
    candidates.append(None)  # 最后回退到不限价
    return candidates


def _get_saved_herosms_api_key() -> str:
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
        secret = getattr(settings, "herosms_api_key", None)
        if secret:
            return secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
    except Exception:
        pass
    return ""


def _load_reuse_pool() -> list[dict]:
    try:
        with get_db() as db:
            setting = crud.get_setting(db, HEROSMS_REUSE_POOL_KEY)
            if not setting or not setting.value:
                return []
            data = json.loads(setting.value)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_reuse_pool(pool: list[dict]) -> None:
    with get_db() as db:
        crud.set_setting(
            db,
            HEROSMS_REUSE_POOL_KEY,
            json.dumps(pool, ensure_ascii=False),
            description="HeroSMS 成功号码复用池",
            category="sms",
        )


def _claim_reusable_activation(service: str, country: int, max_uses: int) -> Optional[dict]:
    """从复用池领取一个号码；领取后标记为 in_use，避免并发任务同时使用同一个号码。"""
    now = _utc_now()
    with _REUSE_POOL_LOCK:
        pool = _load_reuse_pool()
        changed = False
        for item in pool:
            if item.get("state") != "active":
                continue
            if str(item.get("service")) != str(service) or int(item.get("country") or 0) != int(country):
                continue
            if int(item.get("uses") or 0) >= max_uses:
                item["state"] = "exhausted"
                changed = True
                continue
            if item.get("in_use") and not _reservation_is_stale(str(item.get("reserved_at") or "")):
                continue
            item["in_use"] = True
            item["reserved_at"] = now
            item["updated_at"] = now
            changed = True
            _save_reuse_pool(pool)
            return dict(item)
        if changed:
            _save_reuse_pool(pool)
    return None


def _record_activation_success(
    activation: HeroSMSActivation,
    *,
    service: str,
    country: int,
    max_uses: int,
    code: str,
    reused: bool,
) -> bool:
    """记录号码成功使用次数。返回 True 表示应结束 HeroSMS activation。"""
    now = _utc_now()
    with _REUSE_POOL_LOCK:
        pool = _load_reuse_pool()
        item = next((x for x in pool if str(x.get("activation_id")) == str(activation.activation_id)), None)
        if not item:
            item = {
                "activation_id": str(activation.activation_id),
                "phone_number": activation.phone_number,
                "raw_number": activation.raw_number,
                "country_phone_code": activation.country_phone_code,
                "activation_cost": activation.activation_cost,
                "service": service,
                "country": country,
                "uses": 0,
                "used_codes": [],
                "created_at": now,
            }
            pool.append(item)

        used_codes = [str(x).strip() for x in item.get("used_codes", []) if str(x).strip()]
        if code and code not in used_codes:
            used_codes.append(code)
        item.update({
            "service": service,
            "country": country,
            "phone_number": activation.phone_number,
            "raw_number": activation.raw_number,
            "country_phone_code": activation.country_phone_code,
            "activation_cost": activation.activation_cost,
            "uses": int(item.get("uses") or 0) + 1,
            "max_uses": max_uses,
            "used_codes": used_codes[-10:],
            "last_code": code,
            "last_reused": reused,
            "in_use": False,
            "reserved_at": "",
            "updated_at": now,
        })
        if int(item["uses"]) >= max_uses:
            item["state"] = "exhausted"
            should_finish = True
        else:
            item["state"] = "active"
            should_finish = False
        _save_reuse_pool(pool[-50:])
        return should_finish


def _discard_reusable_activation(activation_id: str, reason: str) -> None:
    now = _utc_now()
    with _REUSE_POOL_LOCK:
        pool = _load_reuse_pool()
        for item in pool:
            if str(item.get("activation_id")) == str(activation_id):
                item["state"] = "failed"
                item["in_use"] = False
                item["failure_reason"] = str(reason)[:300]
                item["updated_at"] = now
                break
        _save_reuse_pool(pool)


def _reservation_is_stale(reserved_at: str) -> bool:
    try:
        dt = datetime.fromisoformat(reserved_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() > 900
    except Exception:
        return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
