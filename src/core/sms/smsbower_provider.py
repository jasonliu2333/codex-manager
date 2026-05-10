"""SMSBower 平台实现。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from curl_cffi import requests as cffi_requests

from ..herosms_client import (
    HeroSMSActivation,
    HeroSMSClient,
    HeroSMSConfig,
    _compact_status_trace,
    extract_sms_code,
    normalize_phone_number,
)
from .base import (
    SMSActivation,
    SMSProviderApiUnavailableError,
    SMSProviderBadKeyError,
    SMSProviderConfig,
    SMSProviderError,
    SMSProviderNoBalanceError,
    SMSProviderNoNumbersError,
)
from .herosms_provider import HeroSMSProvider

logger = logging.getLogger(__name__)


class SMSBowerClient(HeroSMSClient):
    base_url = "https://smsbower.page/stubs/handler_api.php"
    payment_base_url = "https://smsbower.page/api/payment/getActualWalletAddress"

    def get_status_v2(self, activation_id: str) -> dict:
        return self.get_status(activation_id)

    def wait_for_code(
        self,
        activation_id: str,
        *,
        timeout: int = 180,
        poll_interval: int = 3,
        resend_business_code=None,
        exclude_codes=None,
        exclude_texts=None,
        request_started_at: Optional[str] = None,
        trace_callback=None,
    ):
        # SMSBower 无 getStatusV2 端点，直接用 get_status 单一 getter，避免父类双 getter 导致的重复 API 调用
        deadline = time.time() + timeout
        last_herosms_resend = time.time()
        business_resent = False
        exclude_codes = {str(code).strip() for code in (exclude_codes or set()) if str(code).strip()}
        exclude_texts = {str(text).strip() for text in (exclude_texts or set()) if str(text).strip()}
        last_trace = ""

        while time.time() < deadline:
            try:
                result = self.get_status(activation_id)
                if trace_callback:
                    trace = _compact_status_trace("getStatus", result)
                    if trace and trace != last_trace:
                        trace_callback(trace)
                        last_trace = trace
                if result.get("status") == "ok":
                    code = str(result.get("code") or "").strip()
                    sms_text = str(result.get("sms_text") or "").strip()
                    if code and code in exclude_codes:
                        if trace_callback:
                            trace_callback(f"getStatus: 检测到旧验证码 {code}，已跳过")
                        continue
                    if sms_text and sms_text in exclude_texts:
                        if trace_callback:
                            trace_callback("getStatus: 检测到旧短信正文，已跳过")
                        continue
                    if code and code not in exclude_codes:
                        return code
                if result.get("status") == "cancel":
                    return None
            except Exception as exc:
                logger.debug("SMSBower status query failed: %s", exc)

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

    def request_number(
        self,
        service: Optional[str] = None,
        country: Optional[int] = None,
        max_price: Optional[float] = None,
        operator: Optional[str] = None,
        provider_ids: Optional[str] = None,
        except_provider_ids: Optional[str] = None,
        phone_exception: Optional[str] = None,
        min_price: Optional[float] = None,
        ref: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> HeroSMSActivation:
        service = service or self.config.service
        country = self.config.country if country is None else country
        max_price = self.config.max_price if max_price is None else max_price
        common: dict = {"service": service, "country": country}
        if max_price and max_price > 0:
            common["maxPrice"] = max_price
        if min_price and min_price > 0:
            common["minPrice"] = min_price
        if provider_ids:
            common["providerIds"] = provider_ids
        if except_provider_ids:
            common["exceptProviderIds"] = except_provider_ids
        if phone_exception:
            common["phoneException"] = phone_exception
        if operator:
            common["activationOperator"] = operator
        if ref:
            common["ref"] = ref
        if user_id:
            common["userID"] = user_id

        # 优先使用 getNumberV2 (JSON 响应)，失败后回退到 getNumber V1
        v2_error = None
        try:
            resp = self._get({"action": "getNumberV2", **common})
            text = (resp.text or "").strip()
            try:
                data = resp.json()
            except Exception:
                data = None
            if isinstance(data, dict):
                if data.get("status") == 0 and data.get("message"):
                    v2_error = str(data.get("message"))
                elif data.get("activationId"):
                    raw = str(data.get("phoneNumber") or "")
                    country_code = str(data.get("countryCode") or data.get("countryPhoneCode") or "")
                    return HeroSMSActivation(
                        activation_id=str(data["activationId"]),
                        raw_number=raw,
                        phone_number=normalize_phone_number(raw, country_code),
                        country_phone_code=country_code,
                        activation_cost=self._to_float_or_none(data.get("activationCost")),
                        activation_time=str(data.get("activationTime") or "").strip() or None,
                        activation_operator=str(data.get("activationOperator") or "").strip() or None,
                        can_get_another_sms=bool(data.get("canGetAnotherSms")) if data.get("canGetAnotherSms") is not None else None,
                    )
            v2_error = text[:200]
        except Exception as exc:
            v2_error = str(exc)[:200]

        # 回退到 V1 (返回 ACCESS_NUMBER:id:phone 文本格式)
        resp = self._get({"action": "getNumber", **common})
        text = (resp.text or "").strip()
        if text.startswith("ACCESS_NUMBER:"):
            _, activation_id, raw = text.split(":", 2)
            return HeroSMSActivation(
                activation_id=str(activation_id),
                raw_number=str(raw),
                phone_number=normalize_phone_number(str(raw), ""),
            )
        raise ValueError(f"SMSBower 取号失败: V2={v2_error}; V1={text[:200]}")

    @staticmethod
    def _to_float_or_none(value: Any) -> Optional[float]:
        if value in (None, "", "null"):
            return None
        try:
            return float(value)
        except Exception:
            return None


class SMSBowerProvider(HeroSMSProvider):
    provider_name = "smsbower"

    def __init__(self, config: SMSProviderConfig):
        self.config = config
        self.client = SMSBowerClient(HeroSMSConfig(
            api_key=config.api_key,
            service=config.service,
            country=config.country,
            max_price=config.max_price,
            proxy=config.proxy,
            timeout=config.timeout,
        ))

    def request_number(
        self,
        service: Optional[str] = None,
        country: Optional[int] = None,
        max_price: Optional[float] = None,
        operator: Optional[str] = None,
        provider_ids: Optional[str] = None,
        except_provider_ids: Optional[str] = None,
        phone_exception: Optional[str] = None,
        min_price: Optional[float] = None,
        country_key: Optional[str] = None,
        reuse: Optional[bool] = None,
        voice: Optional[bool] = None,
        forwarding: Optional[bool] = None,
        forwarding_number: Optional[str] = None,
    ) -> SMSActivation:
        del country_key, reuse, voice, forwarding, forwarding_number
        try:
            activation = self.client.request_number(
                service=service or self.config.service,
                country=country if country is not None else self.config.country,
                max_price=max_price,
                operator=operator,
                provider_ids=provider_ids or self.config.provider_ids,
                except_provider_ids=except_provider_ids or self.config.except_provider_ids,
                phone_exception=phone_exception or self.config.phone_exception,
                min_price=min_price if min_price is not None else self.config.min_price,
            )
        except Exception as exc:
            self._raise_provider_error(exc)
        return SMSActivation(
            activation_id=activation.activation_id,
            phone_number=activation.phone_number,
            raw_number=activation.raw_number,
            country_phone_code=activation.country_phone_code,
            activation_cost=activation.activation_cost,
            activation_time=activation.activation_time,
            activation_operator=activation.activation_operator,
            can_get_another_sms=activation.can_get_another_sms,
        )

    def get_services(self) -> list[dict]:
        resp = self.client._get({"action": "getServicesList"})
        data = resp.json()
        services = []
        if isinstance(data, dict):
            raw = data.get("services") or data.get("data") or []
        else:
            raw = data if isinstance(data, list) else []
        for item in raw:
            if not isinstance(item, dict):
                continue
            services.append({
                "code": str(item.get("code") or item.get("service") or "").strip(),
                "name": str(item.get("name") or item.get("title") or "").strip(),
            })
        return services

    def get_countries(self) -> list[dict]:
        resp = self.client._get({"action": "getCountries"})
        data = resp.json()
        return self.parse_countries_response(data)

    def get_prices(self, service: Optional[str] = None, country: Optional[int] = None) -> Any:
        actions = ("getPricesV3", "getPricesV2", "getPrices")
        last_error = None
        for action in actions:
            try:
                params: dict = {"action": action, "service": service or self.config.service}
                if country is not None:
                    params["country"] = country
                resp = self.client._get(params)
                if isinstance(resp.text, str):
                    try:
                        return resp.json()
                    except Exception:
                        return json.loads(resp.text)
                return resp.json()
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        return {}

    def get_top_countries_by_service(self, service: Optional[str] = None) -> list[dict]:
        service = service or self.config.service
        countries = self.get_countries()
        priced = self.list_country_prices(service=service, countries=countries)
        return sorted(priced, key=lambda x: (x.get("price") if x.get("price") is not None else 999999, -(x.get("count") or 0)))[:50]

    def get_status_v2(self, activation_id: str) -> dict:
        return self.get_status(activation_id)

    def get_operators(self, country: int) -> list[str]:
        raise NotImplementedError("SMSBower 当前未提供公开运营商列表接口")

    def get_operator_quote_options(self, service: Optional[str], country: int) -> list[dict]:
        raise NotImplementedError("SMSBower 当前未提供公开运营商报价接口")

    def get_provider_price_options(self, service: Optional[str], country: int) -> list[dict]:
        service = service or self.config.service
        raw = self.get_prices(service=service, country=country)
        return self.extract_provider_prices(raw, country, service)

    def list_country_prices(self, service: Optional[str] = None, countries: Optional[list[dict]] = None) -> list[dict]:
        service = service or self.config.service
        countries = countries or self.get_countries()
        matrix = self.get_prices(service=service)
        priced = []
        for country in countries:
            hero_country = self._parse_int(country.get("heroSmsCountry") or country.get("code") or country.get("hero_sms_country"))
            if hero_country is None:
                continue
            parsed = self.extract_country_price(matrix, hero_country, service)
            if not parsed or parsed.get("price") is None:
                continue
            priced.append({
                **country,
                "heroSmsCountry": hero_country,
                "price": parsed.get("price"),
                "count": parsed.get("count"),
            })
        return priced

    @classmethod
    def extract_country_price(cls, raw: Any, country_id: int, service: str) -> Optional[dict]:
        matrix = cls.unwrap_price_matrix(raw)
        id_key = str(country_id)
        service_key = str(service)

        service_node = None
        if isinstance(matrix, dict):
            if isinstance(matrix.get(id_key), dict):
                service_node = matrix.get(id_key, {}).get(service_key)
            if service_node is None and isinstance(matrix.get(service_key), dict):
                service_node = matrix.get(service_key, {}).get(id_key)
        if not isinstance(service_node, dict):
            return None

        if all(not isinstance(v, dict) for v in service_node.values()):
            # 检测 V1 格式: {"cost": cost, "count": count}
            if "cost" in service_node:
                price = cls._parse_float(service_node.get("cost"))
                count = cls._parse_int(service_node.get("count"))
                if price is None:
                    return None
                return {"price": price, "count": count}
            # V2 格式: {"价格1": 数数, "价格2": 数数, ...} — 键为价格
            lowest_price = None
            total_count = 0
            for price_text, count_value in service_node.items():
                price = cls._parse_float(price_text)
                count = cls._parse_int(count_value)
                if price is None:
                    continue
                if lowest_price is None or price < lowest_price:
                    lowest_price = price
                if count is not None:
                    total_count += count
            if lowest_price is None:
                return None
            return {"price": lowest_price, "count": total_count}

        best_price = None
        total_count = 0
        for payload in service_node.values():
            if not isinstance(payload, dict):
                continue
            price = cls._parse_float(payload.get("price") or payload.get("cost"))
            count = cls._parse_int(payload.get("count") or payload.get("qty") or payload.get("available"))
            if price is not None and (best_price is None or price < best_price):
                best_price = price
            if count is not None:
                total_count += count
        if best_price is None:
            return None
        return {"price": best_price, "count": total_count}

    def get_static_wallet(self, coin: str, network: str) -> dict:
        resp = cffi_requests.get(
            self.client.payment_base_url,
            params={"api_key": self.config.api_key, "coin": coin, "network": network},
            proxies=self.client.proxies,
            timeout=self.config.timeout,
            impersonate="chrome110",
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data
        raise SMSProviderError(f"SMSBower 静态钱包响应异常: {str(resp.text)[:200]}")

    def wait_for_code(self, activation_id: str, **kwargs):
        return self.client.wait_for_code(activation_id, **kwargs)

    @classmethod
    def extract_provider_prices(cls, raw: Any, country_id: int, service: str) -> list[dict]:
        matrix = cls.unwrap_price_matrix(raw)
        id_key = str(country_id)
        service_key = str(service)
        service_node = None
        if isinstance(matrix, dict):
            if isinstance(matrix.get(id_key), dict):
                service_node = matrix.get(id_key, {}).get(service_key)
            if service_node is None and isinstance(matrix.get(service_key), dict):
                service_node = matrix.get(service_key, {}).get(id_key)
        if not isinstance(service_node, dict):
            return []

        rows = []
        for provider_id, payload in service_node.items():
            if not isinstance(payload, dict):
                continue
            rows.append({
                "provider_id": str(payload.get("provider_id") or provider_id),
                "price": cls._parse_float(payload.get("price") or payload.get("cost")),
                "count": cls._parse_int(payload.get("count") or payload.get("qty") or payload.get("available")),
            })
        rows = [row for row in rows if row["price"] is not None or row["count"] is not None]
        return sorted(rows, key=lambda x: (x.get("price") if x.get("price") is not None else 999999, -(x.get("count") or 0)))

    @staticmethod
    def _raise_provider_error(exc: Exception):
        text = str(exc or "")
        upper = text.upper()
        if "NO_BALANCE" in upper or "NOT ENOUGH USER BALANCE" in upper or "余额不足" in text:
            raise SMSProviderNoBalanceError(text)
        if "BAD_KEY" in upper or "NO ACCESS" in upper:
            raise SMSProviderBadKeyError(text)
        if "NO_NUMBERS" in upper or "NO FREE PHONES" in upper:
            raise SMSProviderNoNumbersError(text)
        if "BAD_SERVICE" in upper:
            raise SMSProviderError(f"服务代码无效: {text}")
        if "BAD_COUNTRY" in upper:
            raise SMSProviderError(f"国家代码无效: {text}")
        if "BAD_ACTION" in upper:
            raise SMSProviderError(f"接口动作无效: {text}")
        if "NO_ACTIVATION" in upper:
            raise SMSProviderError(f"激活 ID 无效: {text}")
        if "EARLY_CANCEL_DENIED" in upper:
            raise SMSProviderError(f"当前号码暂不可取消（购买后 2 分钟内限制）: {text}")
        if "BAD_STATUS" in upper:
            raise SMSProviderError(f"激活状态值无效: {text}")
        if "TIMED OUT" in upper or "HTTP ERROR" in upper or "REMOTE END CLOSED" in upper:
            raise SMSProviderApiUnavailableError(text)
        raise exc
