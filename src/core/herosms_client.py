"""HeroSMS 接码平台最小客户端。"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional, Any

from curl_cffi import requests as cffi_requests

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HeroSMSConfig:
    api_key: str
    service: str = "dr"
    country: int = 187
    max_price: Optional[float] = None
    proxy: Optional[str] = None
    timeout: int = 30


@dataclass(slots=True)
class HeroSMSActivation:
    activation_id: str
    phone_number: str
    raw_number: str
    country_phone_code: str = ""
    activation_cost: Optional[float] = None


class HeroSMSClient:
    base_url = "https://hero-sms.com/stubs/handler_api.php"

    def __init__(self, config: HeroSMSConfig):
        self.config = config
        self.proxies = {"http": config.proxy, "https": config.proxy} if config.proxy else None

    def _get(self, params: dict, *, needs_key: bool = True, timeout: Optional[int] = None):
        payload = dict(params)
        if needs_key:
            payload["api_key"] = self.config.api_key
        resp = cffi_requests.get(
            self.base_url,
            params=payload,
            proxies=self.proxies,
            timeout=timeout or self.config.timeout,
            impersonate="chrome110",
        )
        resp.raise_for_status()
        return resp

    def request_number(
        self,
        service: Optional[str] = None,
        country: Optional[int] = None,
        max_price: Optional[float] = None,
    ) -> HeroSMSActivation:
        service = service or self.config.service
        country = self.config.country if country is None else country
        max_price = self.config.max_price if max_price is None else max_price
        common = {"service": service, "country": country}
        if max_price and max_price > 0:
            common["maxPrice"] = max_price

        v2_error = None
        try:
            resp = self._get({"action": "getNumberV2", **common})
            data = resp.json()
            if isinstance(data, dict) and data.get("activationId"):
                raw = str(data.get("phoneNumber") or "")
                country_code = str(data.get("countryPhoneCode") or "")
                return HeroSMSActivation(
                    activation_id=str(data["activationId"]),
                    raw_number=raw,
                    phone_number=normalize_phone_number(raw, country_code),
                    country_phone_code=country_code,
                    activation_cost=_to_float_or_none(data.get("activationCost")),
                )
            v2_error = resp.text[:200]
        except Exception as exc:
            v2_error = str(exc)

        resp = self._get({"action": "getNumber", **common})
        text = (resp.text or "").strip()
        if text.startswith("ACCESS_NUMBER:"):
            _, activation_id, raw = text.split(":", 2)
            return HeroSMSActivation(
                activation_id=str(activation_id),
                raw_number=str(raw),
                phone_number=normalize_phone_number(str(raw), ""),
            )
        raise ValueError(f"HeroSMS 取号失败: V2={v2_error}; V1={text[:200]}")

    def get_balance(self) -> float:
        resp = self._get({"action": "getBalance"})
        text = (resp.text or "").strip()
        if text.startswith("ACCESS_BALANCE:"):
            return float(text.split(":", 1)[1])
        raise ValueError(f"HeroSMS 余额响应异常: {text[:200]}")

    def get_countries(self) -> list:
        """获取 HeroSMS 国家列表。该接口不需要 api_key。"""
        resp = self._get({"action": "getCountries"}, needs_key=False)
        data = resp.json()
        if isinstance(data, list):
            return data
        raise ValueError(f"HeroSMS 国家列表响应异常: {(resp.text or '')[:200]}")

    def get_services(self, country: Optional[int] = None, lang: str = "cn") -> list:
        """获取 HeroSMS 服务列表。该接口不需要 api_key。"""
        params = {"action": "getServicesList", "lang": lang}
        if country is not None:
            params["country"] = country
        resp = self._get(params, needs_key=False)
        data = resp.json()
        if isinstance(data, dict) and data.get("status") == "success":
            return data.get("services", [])
        if isinstance(data, list):
            return data
        raise ValueError(f"HeroSMS 服务列表响应异常: {(resp.text or '')[:200]}")

    def get_prices(self, service: Optional[str] = None, country: Optional[int] = None) -> dict:
        params = {"action": "getPrices"}
        if service is not None:
            params["service"] = service
        if country is not None:
            params["country"] = country
        resp = self._get(params)
        try:
            data = resp.json()
        except Exception as exc:
            raise ValueError(f"HeroSMS 价格列表响应异常: {exc}") from exc
        if not isinstance(data, (dict, list)):
            raise ValueError(f"HeroSMS 价格列表响应异常: {(resp.text or '')[:200]}")
        return data

    def get_lowest_price(self, service: Optional[str] = None, country: Optional[int] = None) -> Optional[float]:
        data = self.get_prices(service=service or self.config.service, country=country or self.config.country)
        prices: list[float] = []
        _collect_positive_prices(data, prices)
        return min(prices) if prices else None

    def get_status(self, activation_id: str) -> dict:
        resp = self._get({"action": "getStatus", "id": activation_id})
        return parse_status_text(resp.text)

    def get_status_v2(self, activation_id: str) -> dict:
        resp = self._get({"action": "getStatusV2", "id": activation_id})
        text = (resp.text or "").strip()
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return parse_status_text(text)

        if isinstance(data, str):
            return parse_status_text(data)
        if isinstance(data, dict):
            raw_status = data.get("status")
            if isinstance(raw_status, str):
                parsed = parse_status_text(raw_status)
                if parsed.get("status") != "unknown":
                    return parsed
            for key in ("sms", "call"):
                item = data.get(key)
                if isinstance(item, dict):
                    sms_text = str(item.get("text") or item.get("message") or "")
                    code = str(item.get("code") or "").strip() or extract_sms_code(sms_text)
                    if code:
                        return {"status": "ok", "code": code, "sms_text": sms_text}
        return {"status": "wait_code", "raw": data}

    def get_active_activations(self) -> list:
        resp = self._get({"action": "getActiveActivations", "start": 0, "limit": 20})
        try:
            data = resp.json()
        except Exception:
            return []
        return data.get("data", []) if isinstance(data, dict) and isinstance(data.get("data"), list) else []

    def set_status(self, activation_id: str, status: int) -> str:
        resp = self._get({"action": "setStatus", "id": activation_id, "status": status})
        return (resp.text or "").strip()

    def request_resend_sms(self, activation_id: str) -> str:
        return self.set_status(activation_id, 3)

    def finish_activation(self, activation_id: str) -> bool:
        try:
            resp = self._get({"action": "finishActivation", "id": activation_id})
            return resp.status_code in (200, 204) or "ACCESS" in (resp.text or "")
        except Exception:
            try:
                return "ACCESS" in self.set_status(activation_id, 6)
            except Exception:
                return False

    def cancel_activation(self, activation_id: str) -> bool:
        try:
            resp = self._get({"action": "cancelActivation", "id": activation_id})
            if resp.status_code == 204 or "ACCESS_CANCEL" in (resp.text or ""):
                return True
        except Exception:
            pass
        try:
            return "ACCESS_CANCEL" in self.set_status(activation_id, 8)
        except Exception:
            return False

    def wait_for_code(
        self,
        activation_id: str,
        *,
        timeout: int = 180,
        poll_interval: int = 3,
        resend_business_code: Optional[Callable[[], None]] = None,
        exclude_codes: Optional[set[str]] = None,
        exclude_texts: Optional[set[str]] = None,
        request_started_at: Optional[str] = None,
        trace_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        deadline = time.time() + timeout
        last_herosms_resend = time.time()
        business_resent = False
        exclude_codes = {str(code).strip() for code in (exclude_codes or set()) if str(code).strip()}
        exclude_texts = {str(text).strip() for text in (exclude_texts or set()) if str(text).strip()}
        last_trace = ""

        while time.time() < deadline:
            for getter in (self.get_status_v2, self.get_status):
                try:
                    result = getter(activation_id)
                    if trace_callback:
                        trace = _compact_status_trace(getter.__name__, result)
                        if trace and trace != last_trace:
                            trace_callback(trace)
                            last_trace = trace
                    if result.get("status") == "ok":
                        code = str(result.get("code") or "").strip()
                        sms_text = str(result.get("sms_text") or "").strip()
                        if code and code in exclude_codes:
                            if trace_callback:
                                trace_callback(f"{getter.__name__}: 检测到旧验证码 {code}，已跳过")
                            continue
                        if sms_text and sms_text in exclude_texts:
                            if trace_callback:
                                trace_callback(f"{getter.__name__}: 检测到旧短信正文，已跳过")
                            continue
                        if code and code not in exclude_codes:
                            return code
                    if result.get("status") == "cancel":
                        return None
                except Exception as exc:
                    logger.debug("HeroSMS status query failed: %s", exc)

            try:
                for item in self.get_active_activations():
                    if str(item.get("activationId")) == str(activation_id):
                        sms_text = str(item.get("smsText") or item.get("sms") or item.get("text") or "")
                        code = str(item.get("smsCode") or "").strip() or extract_sms_code(sms_text)
                        if trace_callback and (code or sms_text):
                            trace_callback(f"getActiveActivations: code={code or '-'} text={sms_text[:120]}")
                        if code and code in exclude_codes:
                            if trace_callback:
                                trace_callback(f"getActiveActivations: 检测到旧验证码 {code}，已跳过")
                            break
                        if sms_text and sms_text.strip() in exclude_texts:
                            if trace_callback:
                                trace_callback("getActiveActivations: 检测到旧短信正文，已跳过")
                            break
                        if code and code not in exclude_codes:
                            return code
                        break
            except Exception:
                pass

            elapsed = timeout - int(deadline - time.time())
            if not business_resent and elapsed >= 90 and resend_business_code:
                try:
                    resend_business_code()
                except Exception as exc:
                    logger.debug("business resend failed: %s", exc)
                business_resent = True
                try:
                    self.request_resend_sms(activation_id)
                    last_herosms_resend = time.time()
                except Exception:
                    pass
            elif time.time() - last_herosms_resend >= 30:
                try:
                    self.request_resend_sms(activation_id)
                    last_herosms_resend = time.time()
                except Exception:
                    pass

            time.sleep(poll_interval)

        return None


def parse_status_text(text: str) -> dict:
    text = (text or "").strip()
    if text == "STATUS_WAIT_CODE":
        return {"status": "wait_code"}
    if text.startswith("STATUS_WAIT_RETRY"):
        return {"status": "wait_retry", "raw": text}
    if text == "STATUS_WAIT_RESEND":
        return {"status": "wait_resend"}
    if text.startswith("STATUS_OK:"):
        return {"status": "ok", "code": text.split(":", 1)[1]}
    if text == "STATUS_CANCEL":
        return {"status": "cancel"}
    return {"status": "unknown", "raw": text}


def extract_sms_code(text: str) -> str:
    """从短信正文中兜底提取 4-8 位数字验证码。"""
    if not text:
        return ""
    match = re.search(r"(?<!\d)(\d{4,8})(?!\d)", text)
    return match.group(1) if match else ""


def normalize_phone_number(raw_number: str, country_phone_code: str) -> str:
    raw = str(raw_number or "").strip()
    code = str(country_phone_code or "").strip()
    if raw.startswith("+"):
        return raw
    if code and raw.startswith(code):
        return f"+{raw}"
    if code:
        return f"+{code}{raw}"
    return f"+{raw}"


def _to_float_or_none(value) -> Optional[float]:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _collect_positive_prices(node: Any, out: list[float]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            key_lower = str(key).lower()
            if key_lower in {"cost", "price", "rate", "activationcost"}:
                number = _to_float_or_none(value)
                if number and number > 0:
                    out.append(number)
            _collect_positive_prices(value, out)
        return
    if isinstance(node, list):
        for item in node:
            _collect_positive_prices(item, out)


def _compact_status_trace(source: str, result: dict) -> str:
    status = result.get("status")
    code = str(result.get("code") or "").strip()
    sms_text = str(result.get("sms_text") or "")
    raw = result.get("raw")
    if status == "ok" or sms_text:
        return f"{source}: status={status}, code={code or '-'}, text={sms_text[:120]}"
    if raw not in (None, "", {}, []):
        raw_text = json.dumps(raw, ensure_ascii=False) if not isinstance(raw, str) else raw
        return f"{source}: status={status}, raw={raw_text[:180]}"
    return ""
