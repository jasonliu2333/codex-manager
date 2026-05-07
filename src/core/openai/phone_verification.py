"""OpenAI add-phone 流程与短信平台的桥接。"""

from __future__ import annotations

import json
import logging
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ...config.settings import get_settings, normalize_sms_provider_name, get_sms_provider_api_key_db_key, get_sms_provider_api_key_field
from ...database import crud
from ...database.session import get_db, get_session_manager
from ..sms import SMSActivation, SMSProviderConfig, get_sms_provider


def _persist_phone_stage(attempt_id: Optional[int], stage: str, wait_timeout: Optional[int] = None,
                         task_uuid: Optional[str] = None, batch_id: Optional[str] = None):
    """持久化手机验证阶段，用于重启后可恢复。"""
    if not attempt_id:
        return
    try:
        with get_db() as db:
            crud.update_phone_attempt_stage(db, int(attempt_id), stage,
                                            wait_timeout_seconds=wait_timeout,
                                            task_uuid=task_uuid, batch_id=batch_id)
    except Exception:
        pass


SMS_REUSE_POOL_KEY = "herosms.reuse_pool"
SMS_ACTIVATION_WINDOW_SECONDS = 20 * 60
SMS_PENDING_CANCEL_KEY = "sms.pending_cancel_pool"
PHONE_REPUTATION_MAX_SUCCESS_USES = 3
_REUSE_POOL_LOCK = threading.RLock()
_PHONE_STATS_SCHEMA_LOCK = threading.RLock()
_PHONE_STATS_SCHEMA_READY = False
logger = logging.getLogger(__name__)


def _ensure_phone_stats_schema() -> None:
    """兜底确保手机统计表/列已迁移。

    这层兜底是为了避免服务未完整重启或旧库未迁移时，统计写入被缺表/缺列静默吞掉。
    """
    global _PHONE_STATS_SCHEMA_READY
    if _PHONE_STATS_SCHEMA_READY:
        return
    with _PHONE_STATS_SCHEMA_LOCK:
        if _PHONE_STATS_SCHEMA_READY:
            return
        try:
            manager = get_session_manager()
            manager.create_tables()
            manager.migrate_tables()
            _PHONE_STATS_SCHEMA_READY = True
        except Exception as exc:
            logger.warning("初始化手机统计数据库结构失败: %s", exc)


def _extract_account_identity(engine: Any) -> tuple[Optional[int], Optional[str]]:
    account_id = getattr(engine, "account_id", None)
    email = (
        getattr(engine, "email", None)
        or getattr(engine, "email_address", None)
        or getattr(engine, "account_email", None)
        or getattr(engine, "current_email", None)
    )
    return (int(account_id) if isinstance(account_id, int) or str(account_id).isdigit() else None, str(email).strip() if email else None)


def _create_phone_verification_record(
    engine: Any,
    *,
    cfg: SMSProviderConfig,
    activation: Optional[SMSActivation],
    provider_slot: Optional[str],
    provider_quote: Optional[float],
    provider_count: Optional[int],
    reused: bool,
    charged_cost: Optional[float],
    original_activation_cost: Optional[float] = None,
) -> Optional[int]:
    account_id, account_email = _extract_account_identity(engine)
    try:
        _ensure_phone_stats_schema()
        with get_db() as db:
            record = crud.create_phone_verification_attempt(
                db,
                account_id=account_id,
                account_email=account_email,
                sms_provider=normalize_sms_provider_name(cfg.provider or "herosms"),
                provider_slot=str(provider_slot or "").strip() or None,
                provider_quote=provider_quote,
                provider_count=provider_count,
                service=cfg.service,
                country=cfg.country if cfg.country and int(cfg.country) > 0 else None,
                country_key=str(cfg.country_key or "").strip() or None,
                operator=(
                    str(getattr(activation, "activation_operator", "") or "")
                    or (str(cfg.operator or "") if hasattr(cfg, "operator") else None)
                ),
                phone_number=getattr(activation, "phone_number", None),
                activation_id=str(getattr(activation, "activation_id", "") or ""),
                requested_max_price=cfg.max_price,
                requested_min_price=cfg.min_price,
                activation_cost=getattr(activation, "activation_cost", None),
                charged_cost=charged_cost,
                original_activation_cost=original_activation_cost,
                reused=reused,
                result_status="pending",
                success=False,
                invalid=False,
            )
            return int(record.id)
    except Exception as exc:
        try:
            engine._log(f"add-phone: 写入手机验证统计失败: {exc}", "warning")
        except Exception:
            logger.warning("写入手机验证统计失败: %s", exc)
        return None


def _update_phone_verification_record(attempt_id: Optional[int], **updates) -> None:
    if not attempt_id:
        return
    if updates.get("success") is True:
        updates.setdefault("result_status", "success")
        updates.setdefault("failure_type", None)
    elif updates.get("invalid") is True:
        stage = str(updates.get("failure_stage") or "")
        code = str(updates.get("error_code") or "")
        if stage in {"history_blacklist_skip", "number_skipped"}:
            updates.setdefault("result_status", "skipped")
        else:
            updates.setdefault("result_status", "invalid")
        updates.setdefault("failure_type", _classify_phone_failure_type(code, str(updates.get("error_message") or "")))
    try:
        _ensure_phone_stats_schema()
        with get_db() as db:
            crud.update_phone_verification_attempt(db, int(attempt_id), **updates)
    except Exception as exc:
        logger.warning("更新手机验证统计失败 attempt_id=%s: %s", attempt_id, exc)
        return


def _extract_error_code_from_text(error_text: str) -> Optional[str]:
    text = (error_text or "").lower()
    markers = [
        "sms_code_timeout",
        "等待短信验证码超时",
        "fraud_guard",
        "phone_max_usage_exceeded",
        "phone_number_in_use",
        "phone_number_blocked",
        "phone_number_invalid",
        "phone_number_not_supported",
        "phone_number_banned",
        "phone_number_unavailable",
        "phone number cannot be used",
        "phone number is unavailable",
        "number unavailable",
        "temporarily unavailable",
        "too_many_attempts",
        "too_many_requests",
        "no_numbers",
        "no_balance",
        "bad_key",
    ]
    for marker in markers:
        if marker in text:
            return marker
    return None


def _classify_phone_failure_type(error_code: str = "", error_message: str = "") -> str:
    text = f"{error_code} {error_message}".lower()
    hard_markers = [
        "phone_max_usage_exceeded",
        "phone_number_in_use",
        "phone_number_blocked",
        "phone_number_invalid",
        "phone_number_not_supported",
        "phone_number_banned",
        "phone_number_unavailable",
        "phone number already in use",
        "phone number blocked",
        "phone number banned",
        "invalid phone number",
        "unsupported phone number",
        "phone number cannot be used",
        "phone number is unavailable",
        "number unavailable",
        "temporarily unavailable",
        "history_blacklist_skip",
        "bad_key",
        "no_balance",
    ]
    transient_markers = [
        "tls",
        "connect",
        "timeout",
        "timed out",
        "curl",
        "proxy",
        "network",
        "http_5",
        "server offline",
        "internal error",
    ]
    policy_markers = [
        "cloudflare",
        "challenge",
        "captcha",
        "forbidden",
        "fraud_guard",
        "suspicious behavior",
        "too_many_attempts",
        "too_many_requests",
    ]
    if any(marker in text for marker in hard_markers):
        return "hard_invalid"
    if any(marker in text for marker in policy_markers):
        return "policy_blocked"
    if any(marker in text for marker in transient_markers):
        return "transient"
    return "soft_invalid"


def _should_blacklist_phone_failure(error_code: str = "", error_message: str = "") -> bool:
    text = f"{error_code} {error_message}".lower()
    blacklist_markers = [
        "sms_code_timeout",
        "等待短信验证码超时",
        "phone_max_usage_exceeded",
        "maximum number of accounts",
        "phone_number_in_use",
        "phone number already in use",
        "phone_number_blocked",
        "phone number blocked",
        "phone_number_banned",
        "phone number banned",
        "phone_number_invalid",
        "invalid phone number",
        "phone_number_not_supported",
        "unsupported phone number",
        "phone verification failed for this number",
        "phone_number_unavailable",
        "phone number cannot be used",
        "phone number is unavailable",
        "number unavailable",
        "temporarily unavailable",
    ]
    return _classify_phone_failure_type(error_code, error_message) == "hard_invalid" or any(
        marker in text for marker in blacklist_markers
    )


def _is_phone_blacklisted(provider_name: str, phone_number: str) -> Optional[dict]:
    try:
        provider_name = normalize_sms_provider_name(provider_name or "herosms")
        with get_db() as db:
            record = crud.get_phone_number_reputation(db, provider_name, phone_number)
            if not record:
                return None
            success_count = int(record.success_count or 0)
            blacklisted = bool(record.blacklisted) or success_count >= PHONE_REPUTATION_MAX_SUCCESS_USES
            if not blacklisted:
                return None
            return {
                "failure_count": int(record.failure_count or 0),
                "success_count": success_count,
                "last_error_code": record.last_error_code,
                "last_error_message": record.last_error_message,
            }
    except Exception:
        return None


def _record_phone_reputation(
    *,
    provider_name: str,
    phone_number: Optional[str],
    service: str,
    country: Optional[int],
    country_key: Optional[str],
    provider_slot: Optional[str],
    success: bool,
    blacklisted: bool,
    error_code: Optional[str],
    error_message: Optional[str],
    activation_cost: Optional[float],
    result_label: Optional[str],
) -> None:
    if not phone_number:
        return
    try:
        _ensure_phone_stats_schema()
        with get_db() as db:
            crud.upsert_phone_number_reputation(
                db,
                sms_provider=normalize_sms_provider_name(provider_name),
                phone_number=str(phone_number).strip(),
                service=service,
                country=country,
                country_key=country_key,
                provider_slot=provider_slot,
                success=success,
                blacklisted=blacklisted,
                error_code=error_code,
                error_message=error_message,
                activation_cost=activation_cost,
                result_label=result_label,
            )
    except Exception as exc:
        logger.warning("更新手机号码信誉失败 phone=%s: %s", phone_number, exc)
        return


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

    _task_uuid = getattr(engine, "task_uuid", None)
    runtime = _load_sms_runtime_settings()
    provider_name = normalize_sms_provider_name(runtime.get("provider", "herosms"))
    provider_label = {
        "herosms": "HeroSMS",
        "smsbower": "SMSBower",
        "5sim": "5SIM",
    }.get(provider_name, provider_name)
    if not runtime.get("enabled", False):
        engine._log(f"检测到 add-phone，但 {provider_label} 未启用，跳过手机验证", "warning")
        return None

    api_key = _get_saved_sms_api_key()
    if not api_key:
        engine._log(f"检测到 add-phone，但未配置 {provider_label} API Key", "error")
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

    cfg = SMSProviderConfig(
        api_key=api_key,
        provider=str(runtime.get("provider", "herosms") or "herosms"),
        service=runtime.get("service", "dr") or "dr",
        country=int(runtime.get("country", 187) or 187),
        country_key=str(runtime.get("country_key", "") or ""),
        max_price=_positive_float_or_none(runtime.get("max_price", -1)),
        min_price=_positive_float_or_none(runtime.get("min_price", -1)),
        proxy=(runtime.get("proxy", "") or getattr(engine, "proxy_url", None) or None),
        timeout=int(runtime.get("timeout", 30) or 30),
        provider_ids=str(runtime.get("provider_ids", "") or ""),
        except_provider_ids=str(runtime.get("except_provider_ids", "") or ""),
        phone_exception=str(runtime.get("phone_exception", "") or ""),
        reuse=bool(runtime.get("reuse_platform", False)),
        voice=bool(runtime.get("voice", False)),
        forwarding=bool(runtime.get("forwarding", False)),
        forwarding_number=str(runtime.get("forwarding_number", "") or ""),
    )
    client = get_sms_provider(cfg)
    max_number_attempts = max(1, int(runtime.get("max_number_attempts", 1) or 1))
    target_number_index = max(1, int(runtime.get("target_number_index", 1) or 1))
    lowest_price_first = bool(runtime.get("lowest_price_first", True))
    price_relax_enabled = bool(runtime.get("price_relax_enabled", True))
    price_relax_max_multiplier = max(1, int(runtime.get("price_relax_max_multiplier", 5) or 5))
    reuse_enabled = bool(runtime.get("reuse_enabled", False))
    reuse_max_uses = max(1, int(runtime.get("reuse_max_uses", 1) or 1))
    selected_operator = str(runtime.get("operator", "") or "").strip()
    provider_candidates = _build_provider_candidates(engine, client, cfg)
    provider_failover_enabled = bool(runtime.get("provider_failover_enabled", True)) and provider_name == "smsbower" and not str(cfg.provider_ids or "").strip()
    provider_fail_threshold = max(1, int(runtime.get("provider_fail_threshold", 3) or 3))
    provider_rotation_index = 0
    provider_forced_price_floor: Optional[float] = None
    provider_failure_counts: dict[str, int] = {}
    resolved_max_price = cfg.max_price
    min_price_floor = _positive_float_or_none(cfg.min_price)
    max_price_cap = _positive_float_or_none(cfg.max_price)
    engine._log(
        "add-phone: 价格规则初始化: "
        f"min_price={min_price_floor if min_price_floor is not None else '-'}, "
        f"max_price={max_price_cap if max_price_cap is not None else '-'}, "
        f"lowest_price_first={lowest_price_first}, "
        f"price_relax_enabled={price_relax_enabled}, "
        f"price_relax_max_multiplier={price_relax_max_multiplier}"
    )
    if lowest_price_first:
        try:
            lowest_price = client.get_lowest_price(service=cfg.service, country=cfg.country)
            if lowest_price and lowest_price > 0:
                if min_price_floor and lowest_price < min_price_floor:
                    resolved_max_price = min_price_floor
                    engine._log(f"add-phone: 已启用最低价优先，但受 min_price={min_price_floor} 限制，本次使用 maxPrice={min_price_floor}")
                else:
                    resolved_max_price = lowest_price
                    engine._log(f"add-phone: 已启用最低价优先，本次使用 maxPrice={lowest_price}")
            else:
                engine._log("add-phone: 未解析到最低价格，回退到默认取号策略", "warning")
        except Exception as exc:
            engine._log(f"add-phone: 查询最低价格失败，回退到默认取号策略: {exc}", "warning")
    if max_price_cap and resolved_max_price and resolved_max_price > max_price_cap:
        resolved_max_price = max_price_cap
        engine._log(f"add-phone: 受 max_price={max_price_cap} 限制，最终 maxPrice={max_price_cap}")

    last_error: Optional[str] = None
    for number_attempt in range(1, max_number_attempts + 1):
        activation = None
        verification_attempt_id: Optional[int] = None
        provider_slot_used: Optional[str] = None
        reused_activation = False
        previous_codes: set[str] = set()
        previous_texts: set[str] = set()
        try:
            _cleanup_reuse_pool(client)
            _cleanup_pending_cancels(client)
            reuse_entry = _claim_reusable_activation(provider_name, cfg.service, cfg.country, reuse_max_uses) if reuse_enabled else None
            if reuse_entry:
                activation = SMSActivation(
                    activation_id=str(reuse_entry["activation_id"]),
                    phone_number=str(reuse_entry["phone_number"]),
                    raw_number=str(reuse_entry.get("raw_number") or reuse_entry["phone_number"]),
                    country_phone_code=str(reuse_entry.get("country_phone_code") or ""),
                    activation_cost=_positive_float_or_none(reuse_entry.get("activation_cost")),
                )
                reused_activation = True
                provider_slot_used = str(reuse_entry.get("provider_slot") or "").strip() or None
                previous_codes = {str(code).strip() for code in reuse_entry.get("used_codes", []) if str(code).strip()}
                previous_texts = {
                    str(text).strip()
                    for text in reuse_entry.get("used_texts", [])
                    if str(text).strip()
                }
                activation_expires_at = str(reuse_entry.get("expires_at") or "")
                engine._log(
                    f"add-phone: 复用已成功号码 {activation.phone_number} "
                    f"(activation={activation.activation_id}, used={reuse_entry.get('uses', 0)}/{reuse_max_uses}, "
                    f"expires_at={activation_expires_at or '-'})"
                )
                original_cost = reuse_entry.get("activation_cost")
                if original_cost not in (None, "", "null"):
                    engine._log(f"add-phone: 复用号码本次费用=0，原始激活费用={original_cost}")
                else:
                    engine._log("add-phone: 复用号码本次费用=0")
                verification_attempt_id = _create_phone_verification_record(
                    engine,
                    cfg=cfg,
                    activation=activation,
                    provider_slot=provider_slot_used,
                    provider_quote=None,
                    provider_count=None,
                    reused=True,
                    charged_cost=0.0,
                    original_activation_cost=_positive_float_or_none(original_cost),
                )
                _persist_phone_stage(verification_attempt_id, "acquired_number", task_uuid=_task_uuid)
            else:
                price_candidates = _build_price_candidates(
                    resolved_max_price,
                    price_relax_enabled=price_relax_enabled,
                    price_relax_max_multiplier=price_relax_max_multiplier,
                )
                engine._log(
                    "add-phone: 最终价格档列表: "
                    + ", ".join("不限价" if value is None else str(value) for value in price_candidates)
                )
                last_request_error: Optional[Exception] = None
                for idx, candidate_price in enumerate(price_candidates, start=1):
                    provider_try_plan = _build_provider_try_plan_with_failover(
                        provider_candidates,
                        candidate_price,
                        cfg,
                        min_provider_index=provider_rotation_index,
                        forced_price_floor=provider_forced_price_floor,
                    )
                    if provider_candidates:
                        engine._log(
                            "add-phone: 当前价格档可尝试 provider: "
                            + ", ".join(
                                f"{item.get('provider_ids') or '自动'}"
                                f"[price={item.get('price') if item.get('price') is not None else '-'},"
                                f"count={item.get('count') if item.get('count') is not None else '-'}]"
                                for item in provider_try_plan
                            )
                        )
                    for provider_try_index, provider_choice in enumerate(provider_try_plan, start=1):
                        try:
                            price_label = "不限价" if candidate_price is None else str(candidate_price)
                            provider_choice_label = provider_choice.get("provider_ids") or "自动"
                            provider_meta = ""
                            if provider_choice.get("price") is not None or provider_choice.get("count") is not None:
                                provider_meta = f", provider_quote={provider_choice.get('price')}, provider_count={provider_choice.get('count')}"
                            balance_before = _safe_get_balance(client)
                            engine._log(
                                f"add-phone: 正在向短信平台取号 service={cfg.service}, country={cfg.country}, "
                                f"attempt={number_attempt}/{max_number_attempts}, price_try={idx}/{len(price_candidates)}, "
                                f"provider_try={provider_try_index}/{len(provider_try_plan)}, maxPrice={price_label}, providerIds={provider_choice_label}{provider_meta}"
                            )
                            activation = _request_number_with_provider_options(
                                client,
                                candidate_price=candidate_price,
                                selected_operator=selected_operator,
                                cfg=cfg,
                                provider_ids=provider_choice.get("provider_ids"),
                            )
                            balance_after = _safe_get_balance(client)
                            provider_slot_used = provider_choice.get("provider_ids") or None
                            engine._log(
                                "add-phone: 当前价格档命中结果: "
                                f"maxPrice={price_label}, "
                                f"provider={provider_choice_label}, "
                                f"actual_price={activation.activation_cost if activation.activation_cost is not None else '未知'}"
                            )
                            charged_cost = _log_activation_cost(engine, activation, balance_before, balance_after)
                            verification_attempt_id = _create_phone_verification_record(
                                engine,
                                cfg=cfg,
                                activation=activation,
                                provider_slot=provider_slot_used,
                                provider_quote=provider_choice.get("price"),
                                provider_count=provider_choice.get("count"),
                                reused=False,
                                charged_cost=charged_cost,
                            )
                            _persist_phone_stage(verification_attempt_id, "acquired_number", task_uuid=_task_uuid)
                            if activation.activation_operator or activation.activation_time or activation.can_get_another_sms is not None:
                                engine._log(
                                    "add-phone: activation 扩展信息: "
                                    f"operator={activation.activation_operator or '-'}, "
                                    f"time={activation.activation_time or '-'}, "
                                    f"can_get_another_sms={activation.can_get_another_sms if activation.can_get_another_sms is not None else '-'}"
                                )
                            break
                        except Exception as exc:
                            last_request_error = exc
                            err_text = str(exc)
                            provider_slot_on_fail = provider_choice.get("provider_ids") or None
                            request_attempt_id = _create_phone_verification_record(
                                engine,
                                cfg=cfg,
                                activation=None,
                                provider_slot=provider_slot_on_fail,
                                provider_quote=provider_choice.get("price"),
                                provider_count=provider_choice.get("count"),
                                reused=False,
                                charged_cost=None,
                            )
                            _update_phone_verification_record(
                                request_attempt_id,
                                invalid=True,
                                result_status="provider_failed",
                                failure_stage="request_number",
                                error_code=_extract_error_code_from_text(err_text) or "request_number_failed",
                                error_message=err_text[:1000],
                            )
                            provider_rotation_index, provider_forced_price_floor = _register_provider_failure_and_maybe_rotate(
                                engine,
                                provider_failover_enabled=provider_failover_enabled,
                                provider_slot_used=provider_slot_on_fail,
                                provider_failure_counts=provider_failure_counts,
                                provider_fail_threshold=provider_fail_threshold,
                                provider_candidates=provider_candidates,
                                provider_rotation_index=provider_rotation_index,
                                provider_forced_price_floor=provider_forced_price_floor,
                                max_price_cap=max_price_cap,
                            )
                            if "NO_NUMBERS" in err_text and provider_try_index < len(provider_try_plan):
                                engine._log(f"add-phone: 当前 provider 无号，自动切换下一个 provider 重试: {err_text}", "warning")
                                continue
                            if "NO_NUMBERS" in err_text and idx < len(price_candidates) and provider_try_index == len(provider_try_plan):
                                engine._log(f"add-phone: 当前价格档无号，自动放宽价格继续尝试: {err_text}", "warning")
                                break
                            raise
                    if activation is not None:
                        break
                if activation is None and last_request_error:
                    raise last_request_error
                engine._log(f"add-phone: 取号成功 {activation.phone_number} (activation={activation.activation_id})")

                if number_attempt < target_number_index:
                    engine._log(f"add-phone: 当前为第 {number_attempt} 个号码，配置要求从第 {target_number_index} 个号码开始使用，跳过当前号码", "warning")
                    _update_phone_verification_record(
                        verification_attempt_id,
                        invalid=True,
                        failure_stage="number_skipped",
                        error_code="target_number_skip",
                        error_message=f"跳过第 {number_attempt} 个号码，等待目标序号 {target_number_index}",
                    )
                    client.cancel_activation(activation.activation_id)
                    continue

            blacklist_info = _is_phone_blacklisted(provider_name, activation.phone_number)
            if blacklist_info:
                reason_text = (
                    f"历史失败 {blacklist_info.get('failure_count')} 次，"
                    f"历史成功 {blacklist_info.get('success_count', 0)} 次，"
                    f"last_code={blacklist_info.get('last_error_code') or '-'}"
                )
                engine._log(f"add-phone: 号码 {activation.phone_number} 命中黑名单，直接跳过: {reason_text}", "warning")
                _update_phone_verification_record(
                    verification_attempt_id,
                    invalid=True,
                    failure_stage="history_blacklist_skip",
                    error_code="history_blacklist_skip",
                    error_message=reason_text[:1000],
                )
                _record_phone_reputation(
                    provider_name=provider_name,
                    phone_number=activation.phone_number,
                    service=cfg.service,
                    country=cfg.country if cfg.country and int(cfg.country) > 0 else None,
                    country_key=cfg.country_key or None,
                    provider_slot=provider_slot_used,
                    success=False,
                    blacklisted=True,
                    error_code="history_blacklist_skip",
                    error_message=reason_text,
                    activation_cost=activation.activation_cost,
                    result_label="history_blacklist_skip",
                )
                if reused_activation:
                    _discard_reusable_activation(activation.activation_id, reason_text[:300])
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
                if _is_phone_max_usage_error(send_resp, body):
                    _update_phone_verification_record(
                        verification_attempt_id,
                        invalid=True,
                        failure_stage="submit_phone",
                        error_code="phone_max_usage_exceeded",
                        error_message=body or "手机号已达最大绑定次数",
                    )
                    raise RuntimeError(f"手机号已达最大绑定次数，需更换号码: {body}")
                _update_phone_verification_record(
                    verification_attempt_id,
                    invalid=True,
                    failure_stage="submit_phone",
                    error_code=f"http_{getattr(send_resp, 'status_code', 'no_response')}",
                    error_message=body or "提交手机号失败",
                )
                raise RuntimeError(f"提交手机号失败: {getattr(send_resp, 'status_code', 'NO_RESPONSE')} {body}")

            if not reused_activation:
                try:
                    client.set_status(activation.activation_id, 1)
                except Exception:
                    pass

            _persist_phone_stage(verification_attempt_id, "submitted_phone", task_uuid=_task_uuid)

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
            _persist_phone_stage(verification_attempt_id, "waiting_sms", wait_timeout=timeout, task_uuid=_task_uuid)
            request_started_at = _utc_now()
            code = client.wait_for_code(
                activation.activation_id,
                timeout=timeout,
                poll_interval=poll_interval,
                resend_business_code=resend_business_code,
                exclude_codes=previous_codes,
                exclude_texts=previous_texts if reused_activation else None,
                request_started_at=request_started_at,
            trace_callback=lambda message: engine._log(f"add-phone: {provider_label} 状态: {message}", "debug"),
            )
            if not code:
                _update_phone_verification_record(
                    verification_attempt_id,
                    invalid=True,
                    failure_stage="wait_sms_code",
                    error_code="sms_code_timeout",
                    error_message="等待短信验证码超时",
                )
                raise RuntimeError("等待短信验证码超时")
            engine._log(f"add-phone: 成功获取短信验证码: {code}")
            _persist_phone_stage(verification_attempt_id, "sms_received", task_uuid=_task_uuid)
            _update_phone_verification_record(
                verification_attempt_id,
                sms_code=code,
                sms_received_at=datetime.utcnow(),
            )

            validate_resp = _post_json_with_payload_variants(
                engine,
                validate_url,
                headers,
                [{"code": code}, {"otp": code}, {"verification_code": code}],
                label="add-phone 校验短信",
            )
            if validate_resp is None or validate_resp.status_code not in (200, 201, 204):
                body = (getattr(validate_resp, "text", "") or "")[:300] if validate_resp is not None else ""
                _update_phone_verification_record(
                    verification_attempt_id,
                    invalid=True,
                    failure_stage="validate_sms_code",
                    error_code=f"http_{getattr(validate_resp, 'status_code', 'no_response')}",
                    error_message=body or "短信验证码校验失败",
                )
                raise RuntimeError(f"短信验证码校验失败: {getattr(validate_resp, 'status_code', 'NO_RESPONSE')} {body}")

            next_url = _extract_continue_url(engine, validate_resp)
            should_finish = True
            if reuse_enabled:
                should_finish = _record_activation_success(
                    activation,
                    provider_name=provider_name,
                    service=cfg.service,
                    country=cfg.country,
                    max_uses=reuse_max_uses,
                    code=code,
                    request_started_at=request_started_at,
                    reused=reused_activation,
                    provider_slot=provider_slot_used,
                )
                if should_finish:
                    engine._log(f"add-phone: 号码 {activation.phone_number} 已达到复用上限，将结束激活")
                else:
                    try:
                        client.request_resend_sms(activation.activation_id)
                    except Exception as exc:
                        engine._log(f"add-phone: 请求 {provider_label} 继续接收下一条短信失败，后续复用时仍会重试: {exc}", "warning")
                    engine._log(f"add-phone: 号码 {activation.phone_number} 已保存到复用池，后续账号可继续使用")
            if should_finish:
                try:
                    client.set_status(activation.activation_id, 6)
                except Exception:
                    pass
                client.finish_activation(activation.activation_id)
            _update_phone_verification_record(
                verification_attempt_id,
                success=True,
                invalid=False,
                result_status="success",
                failure_type=None,
                failure_stage=None,
                error_code=None,
                error_message=None,
                verified_at=datetime.utcnow(),
            )
            _record_phone_reputation(
                provider_name=provider_name,
                phone_number=activation.phone_number if activation else None,
                service=cfg.service,
                country=cfg.country if cfg.country and int(cfg.country) > 0 else None,
                country_key=cfg.country_key or None,
                provider_slot=provider_slot_used,
                success=True,
                blacklisted=False,
                error_code=None,
                error_message=None,
                activation_cost=activation.activation_cost if activation else None,
                result_label="success",
            )
            engine._log("add-phone: 手机验证完成")
            _persist_phone_stage(verification_attempt_id, "verified", task_uuid=_task_uuid)
            return next_url or "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
        except Exception as exc:
            last_error = str(exc)
            error_code = _extract_error_code_from_text(last_error) or "runtime_error"
            engine._log(f"add-phone: 手机验证失败: {exc}", "error")
            _persist_phone_stage(verification_attempt_id, "failed", task_uuid=_task_uuid)
            failure_stage = "wait_sms_code" if error_code == "sms_code_timeout" else "exception"
            _update_phone_verification_record(
                verification_attempt_id,
                invalid=True,
                failure_stage=failure_stage,
                error_code=error_code,
                error_message=last_error[:1000],
            )
            _record_phone_reputation(
                provider_name=provider_name,
                phone_number=activation.phone_number if activation else None,
                service=cfg.service,
                country=cfg.country if cfg.country and int(cfg.country) > 0 else None,
                country_key=cfg.country_key or None,
                provider_slot=provider_slot_used,
                success=False,
                blacklisted=bool(activation and activation.phone_number and _should_blacklist_phone_failure(error_code, last_error)),
                error_code=error_code,
                error_message=last_error,
                activation_cost=activation.activation_cost if activation else None,
                result_label="failed",
            )
            provider_rotation_index, provider_forced_price_floor = _register_provider_failure_and_maybe_rotate(
                engine,
                provider_failover_enabled=provider_failover_enabled,
                provider_slot_used=provider_slot_used,
                provider_failure_counts=provider_failure_counts,
                provider_fail_threshold=provider_fail_threshold,
                provider_candidates=provider_candidates,
                provider_rotation_index=provider_rotation_index,
                provider_forced_price_floor=provider_forced_price_floor,
                max_price_cap=max_price_cap,
            )
            if activation:
                if reused_activation:
                    _release_failed_activation(client, activation.activation_id, last_error)
                    _discard_reusable_activation(activation.activation_id, last_error)
                    engine._log(f"add-phone: 复用号码 {activation.phone_number} 已因错误废弃", "warning")
                else:
                    _release_failed_activation(client, activation.activation_id, last_error)
            if reused_activation and _should_retry_with_new_number(last_error) and number_attempt < max_number_attempts:
                engine._log(
                    f"add-phone: 复用号码不可继续使用（{_summarize_retry_reason(last_error)}），自动切换到新号码重试",
                    "warning",
                )
                continue
            if _should_retry_with_new_number(last_error) and number_attempt < max_number_attempts:
                engine._log(
                    f"add-phone: 检测到当前号码不可继续使用（{_summarize_retry_reason(last_error)}），自动切换到下一个号码重试",
                    "warning",
                )
                continue
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
        body_preview = (getattr(resp, "text", "") or "")[:300]
        if resp.status_code >= 400 and body_preview:
            engine._log(f"{label} 响应({idx}/{len(payloads)}): {body_preview}", "debug")
        if resp.status_code in (200, 201, 204):
            return resp
        if resp.status_code not in (400, 422):
            return resp
        if not _should_try_next_payload_variant(resp, payload):
            return resp
    return last_resp


def _should_try_next_payload_variant(response: Any, payload: dict) -> bool:
    try:
        data = response.json()
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    error = data.get("error")
    if not isinstance(error, dict):
        return False
    code = str(error.get("code") or "").strip().lower()
    param = str(error.get("param") or "").strip()
    message = str(error.get("message") or "").strip().lower()

    payload_keys = set(payload.keys())

    if code == "missing_required_parameter":
        if param == "phone_number" and "phone_number" not in payload_keys:
            return True
        if param in {"code", "otp", "verification_code"} and param not in payload_keys:
            return True
        return False

    if "did you mean to provide" in message:
        if "phone_number" in message and "phone_number" not in payload_keys:
            return True
        if any(name in message for name in ("code", "otp", "verification_code")):
            provided_names = {"code", "otp", "verification_code"} & payload_keys
            if provided_names:
                return False

    return False


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


def _load_sms_runtime_settings() -> dict:
    settings = get_settings()
    data = {
        "provider": normalize_sms_provider_name(getattr(settings, "sms_provider", "herosms") or "herosms"),
        "operator": getattr(settings, "sms_operator", "") or "",
        "provider_ids": getattr(settings, "sms_provider_ids", "") or "",
        "except_provider_ids": getattr(settings, "sms_except_provider_ids", "") or "",
        "phone_exception": getattr(settings, "sms_phone_exception", "") or "",
        "country_key": getattr(settings, "sms_country_key", "") or "",
        "min_price": getattr(settings, "sms_min_price", -1),
        "reuse_platform": bool(getattr(settings, "sms_reuse", False)),
        "voice": bool(getattr(settings, "sms_voice", False)),
        "forwarding": bool(getattr(settings, "sms_forwarding", False)),
        "forwarding_number": getattr(settings, "sms_forwarding_number", "") or "",
        "provider_failover_enabled": bool(getattr(settings, "sms_provider_failover_enabled", True)),
        "provider_fail_threshold": int(getattr(settings, "sms_provider_fail_threshold", 3) or 3),
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
        "sms.provider": ("provider", lambda v, d=data["provider"]: str(v or d)),
        "sms.operator": ("operator", lambda v, d=data["operator"]: str(v or d)),
        "sms.provider_ids": ("provider_ids", lambda v, d=data["provider_ids"]: str(v or d)),
        "sms.except_provider_ids": ("except_provider_ids", lambda v, d=data["except_provider_ids"]: str(v or d)),
        "sms.phone_exception": ("phone_exception", lambda v, d=data["phone_exception"]: str(v or d)),
        "sms.country_key": ("country_key", lambda v, d=data["country_key"]: str(v or d)),
        "sms.min_price": ("min_price", lambda v, d=data["min_price"]: v if v not in (None, "") else d),
        "sms.reuse": ("reuse_platform", lambda v, d=data["reuse_platform"]: _parse_bool(v, d)),
        "sms.voice": ("voice", lambda v, d=data["voice"]: _parse_bool(v, d)),
        "sms.forwarding": ("forwarding", lambda v, d=data["forwarding"]: _parse_bool(v, d)),
        "sms.forwarding_number": ("forwarding_number", lambda v, d=data["forwarding_number"]: str(v or d)),
        "sms.provider_failover_enabled": ("provider_failover_enabled", lambda v, d=data["provider_failover_enabled"]: _parse_bool(v, d)),
        "sms.provider_fail_threshold": ("provider_fail_threshold", lambda v, d=data["provider_fail_threshold"]: int(v or d)),
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
    max_multi = max(1, int(price_relax_max_multiplier or 1))
    multipliers = list(range(1, max_multi + 1))
    seen = set()
    for multi in multipliers:
        value = round(base_price * multi, 4)
        if value > 0 and value not in seen:
            seen.add(value)
            candidates.append(value)
    return candidates


def _build_provider_candidates(engine: Any, client: object, cfg: SMSProviderConfig) -> list[dict]:
    if (cfg.provider or "").strip().lower() != "smsbower":
        return []
    if str(cfg.provider_ids or "").strip():
        return []
    try:
        provider_quotes = client.get_provider_price_options(cfg.service, cfg.country)
        provider_quotes = [item for item in provider_quotes if item.get("provider_id")]
        min_price_floor = _positive_float_or_none(cfg.min_price)
        max_price_cap = _positive_float_or_none(cfg.max_price)
        if min_price_floor:
            filtered_quotes = []
            for item in provider_quotes:
                try:
                    price_value = item.get("price")
                    if price_value is None or float(price_value) >= min_price_floor:
                        filtered_quotes.append(item)
                except Exception:
                    continue
            provider_quotes = filtered_quotes
        if max_price_cap:
            filtered_quotes = []
            for item in provider_quotes:
                try:
                    price_value = item.get("price")
                    if price_value is None or float(price_value) <= max_price_cap:
                        filtered_quotes.append(item)
                except Exception:
                    continue
            provider_quotes = filtered_quotes
        provider_quotes.sort(key=lambda x: (x.get("price") if x.get("price") is not None else 999999, -(x.get("count") or 0)))
        if provider_quotes:
            engine._log(
                "add-phone: SMSBower provider 自动排序已启用，候选="
                + ", ".join(
                    f"{item.get('provider_id')}[price={item.get('price')},count={item.get('count')}]"
                    for item in provider_quotes[:8]
                )
            )
        return provider_quotes[:8]
    except Exception as exc:
        engine._log(f"add-phone: 获取 SMSBower provider 报价失败，将回退为平台自动选择: {exc}", "warning")
        return []


def _build_provider_try_plan(provider_candidates: list[dict], candidate_price: Optional[float], cfg: SMSProviderConfig) -> list[dict]:
    return _build_provider_try_plan_with_failover(provider_candidates, candidate_price, cfg)


def _build_provider_try_plan_with_failover(
    provider_candidates: list[dict],
    candidate_price: Optional[float],
    cfg: SMSProviderConfig,
    *,
    min_provider_index: int = 0,
    forced_price_floor: Optional[float] = None,
) -> list[dict]:
    explicit_provider_ids = str(cfg.provider_ids or "").strip()
    if explicit_provider_ids:
        return [{"provider_ids": explicit_provider_ids}]
    if not provider_candidates:
        return [{"provider_ids": None}]
    plan = []
    effective_cap = candidate_price
    if forced_price_floor is not None:
        if effective_cap is None:
            effective_cap = forced_price_floor
        else:
            effective_cap = max(effective_cap, forced_price_floor)
    for idx, item in enumerate(provider_candidates):
        if idx < max(0, int(min_provider_index or 0)):
            continue
        quote_price = item.get("price")
        if effective_cap is not None and quote_price is not None and quote_price > effective_cap:
            continue
        plan.append({
            "provider_ids": str(item.get("provider_id") or "").strip(),
            "price": quote_price,
            "count": item.get("count"),
            "candidate_index": idx,
        })
    return plan or [{"provider_ids": None}]


def _advance_provider_failover(
    engine: Any,
    provider_candidates: list[dict],
    provider_slot_used: str,
    current_rotation_index: int,
    current_forced_price_floor: Optional[float],
    max_price_cap: Optional[float],
) -> Optional[dict]:
    normalized_slot = str(provider_slot_used or "").strip()
    if not normalized_slot or not provider_candidates:
        return None
    matched_index = next(
        (idx for idx, item in enumerate(provider_candidates) if str(item.get("provider_id") or "").strip() == normalized_slot),
        None,
    )
    if matched_index is None:
        return None
    next_index = matched_index + 1
    if next_index >= len(provider_candidates):
        engine._log(f"add-phone: providerIds={normalized_slot} 已达到连续失败阈值，但没有更高一档 provider 可切换", "warning")
        return None
    next_item = provider_candidates[next_index]
    next_price = _positive_float_or_none(next_item.get("price"))
    cap = _positive_float_or_none(max_price_cap)
    if cap is not None and next_price is not None and next_price > cap:
        engine._log(
            f"add-phone: providerIds={normalized_slot} 已达到连续失败阈值，但下一 providerIds={next_item.get('provider_id')} 报价 {next_price} 超过最大价格 {cap}，停止上移",
            "warning",
        )
        return None
    forced_floor = next_price if next_price is not None else current_forced_price_floor
    engine._log(
        f"add-phone: providerIds={normalized_slot} 连续失败，切换到下一 providerIds={next_item.get('provider_id')}，"
        f"新的价格下限={forced_floor if forced_floor is not None else '-'}",
        "warning",
    )
    return {
        "next_index": max(current_rotation_index, next_index),
        "next_price": forced_floor,
    }


def _register_provider_failure_and_maybe_rotate(
    engine: Any,
    *,
    provider_failover_enabled: bool,
    provider_slot_used: Optional[str],
    provider_failure_counts: dict[str, int],
    provider_fail_threshold: int,
    provider_candidates: list[dict],
    provider_rotation_index: int,
    provider_forced_price_floor: Optional[float],
    max_price_cap: Optional[float],
) -> tuple[int, Optional[float]]:
    if not provider_failover_enabled or not provider_slot_used:
        return provider_rotation_index, provider_forced_price_floor
    provider_failure_counts[provider_slot_used] = int(provider_failure_counts.get(provider_slot_used, 0) or 0) + 1
    failure_count = provider_failure_counts[provider_slot_used]
    engine._log(f"add-phone: providerIds={provider_slot_used} 已连续失败 {failure_count}/{provider_fail_threshold}", "warning")
    if failure_count < provider_fail_threshold:
        return provider_rotation_index, provider_forced_price_floor
    rotation = _advance_provider_failover(
        engine,
        provider_candidates,
        provider_slot_used,
        provider_rotation_index,
        provider_forced_price_floor,
        max_price_cap,
    )
    provider_failure_counts[provider_slot_used] = 0
    if not rotation:
        return provider_rotation_index, provider_forced_price_floor
    return rotation["next_index"], rotation["next_price"]


def _request_number_with_provider_options(
    client: object,
    *,
    candidate_price: Optional[float],
    selected_operator: str,
    cfg: SMSProviderConfig,
    provider_ids: Optional[str],
):
    try:
        return client.request_number(
            max_price=candidate_price,
            operator=selected_operator or None,
            provider_ids=provider_ids or cfg.provider_ids or None,
            except_provider_ids=cfg.except_provider_ids or None,
            phone_exception=cfg.phone_exception or None,
            min_price=cfg.min_price,
            country_key=cfg.country_key or None,
            reuse=cfg.reuse,
            voice=cfg.voice,
            forwarding=cfg.forwarding,
            forwarding_number=cfg.forwarding_number or None,
        )
    except TypeError:
        return client.request_number(max_price=candidate_price, operator=selected_operator or None)


def _get_saved_sms_api_key() -> str:
    provider_name = normalize_sms_provider_name(getattr(get_settings(), "sms_provider", "herosms") or "herosms")
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
            return secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
    except Exception:
        pass
    return ""


# 兼容旧命名
_load_herosms_runtime_settings = _load_sms_runtime_settings
_get_saved_herosms_api_key = _get_saved_sms_api_key


def _load_reuse_pool() -> list[dict]:
    try:
        with get_db() as db:
            setting = crud.get_setting(db, SMS_REUSE_POOL_KEY)
            if not setting or not setting.value:
                return []
            data = json.loads(setting.value)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _load_pending_cancel_pool() -> list[dict]:
    try:
        with get_db() as db:
            setting = crud.get_setting(db, SMS_PENDING_CANCEL_KEY)
            if not setting or not setting.value:
                return []
            data = json.loads(setting.value)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_pending_cancel_pool(pool: list[dict]) -> None:
    with get_db() as db:
        crud.set_setting(
            db,
            SMS_PENDING_CANCEL_KEY,
            json.dumps(pool, ensure_ascii=False),
            description="短信平台待补取消 activation 队列",
            category="sms",
        )


def _save_reuse_pool(pool: list[dict]) -> None:
    with get_db() as db:
        crud.set_setting(
            db,
            SMS_REUSE_POOL_KEY,
            json.dumps(pool, ensure_ascii=False),
            description="短信平台成功号码复用池",
            category="sms",
        )


def _save_pending_cancel_pool(pool: list[dict]) -> None:
    with get_db() as db:
        crud.set_setting(
            db,
            SMS_PENDING_CANCEL_KEY,
            json.dumps(pool, ensure_ascii=False),
            description="短信平台待补取消 activation 队列",
            category="sms",
        )


def _claim_reusable_activation(provider_name: str, service: str, country: int, max_uses: int) -> Optional[dict]:
    """从复用池领取一个号码；领取后标记为 in_use，避免并发任务同时使用同一个号码。"""
    now = _utc_now()
    provider_name = normalize_sms_provider_name(provider_name or "herosms")
    with _REUSE_POOL_LOCK:
        pool = _load_reuse_pool()
        changed = False
        for item in pool:
            if item.get("state") != "active":
                continue
            item_provider = normalize_sms_provider_name(item.get("sms_provider") or "herosms")
            if item_provider != provider_name:
                continue
            if str(item.get("service")) != str(service) or int(item.get("country") or 0) != int(country):
                continue
            if _activation_window_expired(item):
                item["state"] = "expired"
                item["in_use"] = False
                item["updated_at"] = now
                changed = True
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


def _register_new_activation(
    activation: SMSActivation,
    *,
    service: str,
    country: int,
    max_uses: int,
) -> None:
    now = _utc_now()
    expires_at = _utc_after_seconds(SMS_ACTIVATION_WINDOW_SECONDS)
    with _REUSE_POOL_LOCK:
        pool = _load_reuse_pool()
        item = next((x for x in pool if str(x.get("activation_id")) == str(activation.activation_id)), None)
        if item is None:
            item = {"activation_id": str(activation.activation_id)}
            pool.append(item)
        item.update({
            "phone_number": activation.phone_number,
            "raw_number": activation.raw_number,
            "country_phone_code": activation.country_phone_code,
            "activation_cost": activation.activation_cost,
            "service": service,
            "country": country,
            "uses": int(item.get("uses") or 0),
            "max_uses": max_uses,
            "used_codes": item.get("used_codes", []),
            "used_texts": item.get("used_texts", []),
            "state": "active",
            "in_use": False,
            "reserved_at": "",
            "created_at": str(item.get("created_at") or now),
            "activation_started_at": str(item.get("activation_started_at") or now),
            "expires_at": str(item.get("expires_at") or expires_at),
            "updated_at": now,
        })
        _save_reuse_pool(pool[-50:])


def _record_activation_success(
    activation: SMSActivation,
    *,
    provider_name: str,
    service: str,
    country: int,
    max_uses: int,
    code: str,
    request_started_at: str,
    reused: bool,
    provider_slot: Optional[str] = None,
) -> bool:
    """记录号码成功使用次数。返回 True 表示应结束当前短信平台 activation。"""
    now = _utc_now()
    provider_name = normalize_sms_provider_name(provider_name or "herosms")
    with _REUSE_POOL_LOCK:
        pool = _load_reuse_pool()
        item = next((x for x in pool if str(x.get("activation_id")) == str(activation.activation_id)), None)
        if not item:
            item = {
                "activation_id": str(activation.activation_id),
                "phone_number": activation.phone_number,
                "sms_provider": provider_name,
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
            "sms_provider": provider_name,
            "country": country,
            "phone_number": activation.phone_number,
            "raw_number": activation.raw_number,
            "country_phone_code": activation.country_phone_code,
            "activation_cost": activation.activation_cost,
            "provider_slot": str(provider_slot or item.get("provider_slot") or "").strip() or None,
            "uses": int(item.get("uses") or 0) + 1,
            "max_uses": max_uses,
            "used_codes": used_codes[-10:],
            "used_texts": _append_unique_text(item.get("used_texts", []), code),
            "last_code": code,
            "first_code_received_at": str(item.get("first_code_received_at") or now),
            "last_code_received_at": now,
            "last_request_started_at": request_started_at,
            "last_reused": reused,
            "in_use": False,
            "reserved_at": "",
            "updated_at": now,
        })
        if int(item["uses"]) >= max_uses or _activation_window_expired(item):
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


def _queue_pending_cancel(activation_id: str, reason: str) -> None:
    now = _utc_now()
    with _REUSE_POOL_LOCK:
        pool = _load_pending_cancel_pool()
        item = next((x for x in pool if str(x.get("activation_id")) == str(activation_id)), None)
        if item is None:
            item = {"activation_id": str(activation_id)}
            pool.append(item)
        item.update({
            "reason": str(reason or "").strip()[:300],
            "created_at": str(item.get("created_at") or now),
            "last_attempt_at": now,
            "attempts": int(item.get("attempts") or 0),
        })
        _save_pending_cancel_pool(pool[-100:])


def _cleanup_pending_cancels(client: object) -> None:
    now = _utc_now()
    with _REUSE_POOL_LOCK:
        pool = _load_pending_cancel_pool()
        if not pool:
            return
        remain = []
        for item in pool:
            activation_id = str(item.get("activation_id") or "").strip()
            if not activation_id:
                continue
            last_attempt_at = str(item.get("last_attempt_at") or "")
            if last_attempt_at and not _reservation_is_stale(last_attempt_at):
                remain.append(item)
                continue
            try:
                try:
                    client.set_status(activation_id, 8)
                except Exception:
                    pass
                if client.cancel_activation(activation_id):
                    continue
            except Exception:
                pass
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["last_attempt_at"] = now
            if int(item["attempts"]) < 5:
                remain.append(item)
        _save_pending_cancel_pool(remain[-100:])


def _release_failed_activation(client: object, activation_id: str, reason: str) -> None:
    try:
        try:
            client.set_status(activation_id, 8)
        except Exception:
            pass
        if client.cancel_activation(activation_id):
            return
    except Exception:
        pass
    _queue_pending_cancel(activation_id, reason)


def _cleanup_reuse_pool(client: Optional[object] = None) -> None:
    now = _utc_now()
    changed = False
    to_cancel: list[str] = []
    with _REUSE_POOL_LOCK:
        pool = _load_reuse_pool()
        for item in pool:
            state = str(item.get("state") or "")
            activation_id = str(item.get("activation_id") or "")
            if not activation_id:
                continue
            if state == "active" and _activation_window_expired(item):
                item["state"] = "expired"
                item["in_use"] = False
                item["updated_at"] = now
                changed = True
                to_cancel.append(activation_id)
                continue
            if state in {"failed", "expired", "exhausted"} and item.get("cancelled_at") in (None, ""):
                to_cancel.append(activation_id)
        if changed:
            _save_reuse_pool(pool)
    if client:
        for activation_id in to_cancel:
            try:
                client.cancel_activation(activation_id)
            except Exception:
                try:
                    client.finish_activation(activation_id)
                except Exception:
                    pass
            finally:
                _mark_activation_cancelled(activation_id)


def _mark_activation_cancelled(activation_id: str) -> None:
    with _REUSE_POOL_LOCK:
        pool = _load_reuse_pool()
        for item in pool:
            if str(item.get("activation_id")) == str(activation_id):
                item["cancelled_at"] = _utc_now()
                item["in_use"] = False
                item["updated_at"] = _utc_now()
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


def _utc_after_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _activation_window_expired(item: dict) -> bool:
    expires_at = str(item.get("expires_at") or "")
    if expires_at:
        try:
            return datetime.now(timezone.utc) >= datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except Exception:
            pass
    created_at = str(item.get("activation_started_at") or item.get("created_at") or "")
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() >= SMS_ACTIVATION_WINDOW_SECONDS
    except Exception:
        return True


def _append_unique_text(existing: Any, code: str) -> list[str]:
    values = [str(x).strip() for x in (existing or []) if str(x).strip()]
    if code and code not in values:
        values.append(code)
    return values[-10:]


def _safe_get_balance(client: object) -> Optional[float]:
    try:
        return client.get_balance()
    except Exception:
        return None


def _log_activation_cost(
    engine: Any,
    activation: SMSActivation,
    balance_before: Optional[float],
    balance_after: Optional[float],
) -> Optional[float]:
    activation_cost = activation.activation_cost
    charged = None
    if balance_before is not None and balance_after is not None:
        charged = round(balance_before - balance_after, 6)
    parts = []
    if charged is not None:
        parts.append(f"余额扣费={charged}")
    if activation_cost is not None:
        parts.append(f"activationCost={activation_cost}")
    if balance_before is not None and balance_after is not None:
        parts.append(f"余额 {balance_before} -> {balance_after}")
    if parts:
        engine._log(f"add-phone: 取号费用信息: {', '.join(parts)}")
    return charged


def _is_phone_max_usage_error(response: Any, body: str) -> bool:
    text = (body or "").lower()
    if "phone_max_usage_exceeded" in text:
        return True
    if "maximum number of accounts" in text:
        return True
    try:
        data = response.json() if response is not None else {}
        error = data.get("error") if isinstance(data, dict) else {}
        code = str((error or {}).get("code") or "").lower()
        message = str((error or {}).get("message") or "").lower()
        return code == "phone_max_usage_exceeded" or "maximum number of accounts" in message
    except Exception:
        return False


def _should_retry_with_new_number(error_text: str) -> bool:
    text = (error_text or "").lower()
    markers = [
        "手机号已达最大绑定次数",
        "phone_max_usage_exceeded",
        "maximum number of accounts",
        "phone number is already linked",
        "phone_number_in_use",
        "phone number already in use",
        "phone number blocked",
        "phone_number_blocked",
        "phone number invalid",
        "invalid phone number",
        "phone_number_invalid",
        "phone number is not supported",
        "unsupported phone number",
        "phone_number_not_supported",
        "phone verification failed for this number",
        "phone_number_banned",
        "phone number banned",
        "too many attempts",
        "too many requests for this phone number",
        "phone number cannot be used",
        "phone number is unavailable",
        "number unavailable",
        "temporarily unavailable",
    ]
    return any(marker in text for marker in markers)


def _summarize_retry_reason(error_text: str) -> str:
    text = (error_text or "").lower()
    if "phone_max_usage_exceeded" in text or "maximum number of accounts" in text:
        return "号码已达最大绑定次数"
    if "phone_number_in_use" in text or "phone number already in use" in text:
        return "号码已被占用"
    if "phone_number_blocked" in text or "phone number blocked" in text or "phone number banned" in text or "phone_number_banned" in text:
        return "号码已被封禁"
    if "phone_number_invalid" in text or "invalid phone number" in text:
        return "号码格式无效"
    if "phone number is not supported" in text or "unsupported phone number" in text or "phone_number_not_supported" in text:
        return "号码不受支持"
    if "phone verification failed for this number" in text:
        return "号码无法用于验证"
    if "too many attempts" in text or "too many requests for this phone number" in text:
        return "号码尝试次数过多"
    if "phone number cannot be used" in text or "phone number is unavailable" in text or "number unavailable" in text or "temporarily unavailable" in text:
        return "号码当前不可用"
    return "当前号码不可用"
