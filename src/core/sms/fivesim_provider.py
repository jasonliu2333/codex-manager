"""5SIM 平台实现。"""

from __future__ import annotations

from typing import Any, Optional

from curl_cffi import requests as cffi_requests

from .base import (
    BaseSMSProvider,
    SMSActivation,
    SMSProviderApiUnavailableError,
    SMSProviderBadKeyError,
    SMSProviderConfig,
    SMSProviderError,
    SMSProviderNoBalanceError,
    SMSProviderNoNumbersError,
)


class FiveSimProvider(BaseSMSProvider):
    provider_name = "5sim"
    base_url = "https://5sim.net/v1"

    def __init__(self, config: SMSProviderConfig):
        super().__init__(config)
        self.proxies = {"http": config.proxy, "https": config.proxy} if config.proxy else None

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "application/json",
        }

    def _guest_headers(self) -> dict:
        return {"Accept": "application/json"}

    def _get(self, path: str, *, headers: Optional[dict] = None, params: Optional[dict] = None):
        resp = cffi_requests.get(
            f"{self.base_url}{path}",
            headers=headers or self._headers(),
            params=params or {},
            proxies=self.proxies,
            timeout=self.config.timeout,
            impersonate="chrome110",
        )
        return resp

    def get_balance(self) -> float:
        resp = self._get("/user/profile")
        if resp.status_code == 401:
            raise SMSProviderBadKeyError("5SIM API Key 无效")
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("balance") or 0)

    def get_countries(self) -> list[dict]:
        resp = self._get("/guest/countries", headers=self._guest_headers())
        resp.raise_for_status()
        data = resp.json()
        return self.parse_countries_response(data)

    def get_services(self) -> list[dict]:
        country_key = self._resolve_country_key()
        operator = self._resolve_operator()
        resp = self._get(f"/guest/products/{country_key}/{operator}", headers=self._guest_headers())
        resp.raise_for_status()
        data = resp.json()
        services = []
        if isinstance(data, dict):
            for code, payload in data.items():
                if not isinstance(payload, dict):
                    continue
                services.append({
                    "code": str(code),
                    "name": str(code),
                    "category": payload.get("Category"),
                    "qty": payload.get("Qty"),
                    "price": payload.get("Price"),
                })
        return services

    def get_prices(self, service: Optional[str] = None) -> Any:
        resp = self._get("/guest/prices", headers=self._guest_headers())
        resp.raise_for_status()
        return resp.json()

    def get_lowest_price(self, service: Optional[str] = None, country: Optional[int] = None) -> Optional[float]:
        country_key = self._resolve_country_key()
        operator = self._resolve_operator()
        service = service or self.config.service
        resp = self._get(f"/guest/products/{country_key}/{operator}", headers=self._guest_headers())
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and service in data:
            try:
                return float(data[service].get("Price"))
            except Exception:
                return None
        return None

    def list_country_prices(self, service: Optional[str] = None, countries: Optional[list[dict]] = None) -> list[dict]:
        service = service or self.config.service
        countries = countries or self.get_countries()
        priced = []
        for country in countries:
            country_key = country.get("country_key")
            if not country_key:
                continue
            try:
                resp = self._get(f"/guest/products/{country_key}/any", headers=self._guest_headers())
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and service in data:
                    payload = data[service]
                    priced.append({
                        **country,
                        "price": payload.get("Price"),
                        "count": payload.get("Qty"),
                    })
            except Exception:
                continue
        return sorted(priced, key=lambda x: (x.get("price") if x.get("price") is not None else 999999, -(x.get("count") or 0)))

    def get_top_countries_by_service(self, service: Optional[str] = None) -> list[dict]:
        return self.list_country_prices(service=service or self.config.service, countries=self.get_countries())[:50]

    def get_operators(self, country: int) -> list[str]:
        country_key = self._resolve_country_key()
        resp = self._get("/guest/countries", headers=self._guest_headers())
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and country_key in data and isinstance(data[country_key], dict):
            payload = data[country_key]
            operators = []
            for key, value in payload.items():
                if key in {"iso", "prefix", "text_en"}:
                    continue
                if isinstance(value, dict) and ("activation" in value or "hosting" in value):
                    operators.append(str(key))
            return operators
        return []

    def get_operator_quote_options(self, service: Optional[str], country: int) -> list[dict]:
        country_key = self._resolve_country_key()
        service = service or self.config.service
        operators = self.get_operators(country)
        options = []
        for operator in operators:
            try:
                resp = self._get(f"/guest/products/{country_key}/{operator}", headers=self._guest_headers())
                resp.raise_for_status()
                data = resp.json()
                payload = data.get(service, {}) if isinstance(data, dict) else {}
                options.append({
                    "operator": operator,
                    "price": payload.get("Price"),
                    "count": payload.get("Qty"),
                })
            except Exception as exc:
                options.append({"operator": operator, "price": None, "count": None, "error": str(exc)})
        return sorted(options, key=lambda x: (x.get("price") if x.get("price") is not None else 999999, -(x.get("count") or 0)))

    def get_provider_price_options(self, service: Optional[str], country: int) -> list[dict]:
        return []

    def get_static_wallet(self, coin: str, network: str) -> dict:
        raise NotImplementedError("5SIM 当前未提供静态钱包接口")

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
        service = service or self.config.service
        country_key = country_key or self._resolve_country_key()
        operator = operator or self._resolve_operator()
        query = {}
        if max_price and operator == "any":
            query["maxPrice"] = max_price
        if reuse if reuse is not None else self.config.reuse:
            query["reuse"] = 1
        if voice if voice is not None else self.config.voice:
            query["voice"] = 1
        if forwarding if forwarding is not None else self.config.forwarding:
            query["forwarding"] = 1
        if forwarding_number or self.config.forwarding_number:
            query["number"] = forwarding_number or self.config.forwarding_number
        resp = self._get(f"/user/buy/activation/{country_key}/{operator}/{service}", params=query)
        if resp.status_code in (400, 500):
            self._raise_provider_error(resp.text)
        if resp.status_code == 200 and "no free phones" in (resp.text or "").lower():
            raise SMSProviderNoNumbersError("5SIM 当前无可用号码")
        resp.raise_for_status()
        data = resp.json()
        return SMSActivation(
            activation_id=str(data.get("id")),
            phone_number=str(data.get("phone") or ""),
            raw_number=str(data.get("phone") or ""),
            activation_cost=self._to_float_or_none(data.get("price")),
            activation_time=str(data.get("created_at") or "").strip() or None,
            activation_operator=str(data.get("operator") or "").strip() or None,
            can_get_another_sms=True,
        )

    def get_status(self, activation_id: str) -> dict:
        resp = self._get(f"/user/check/{activation_id}")
        if resp.status_code == 404:
            return {"status": "cancel"}
        if resp.status_code >= 400:
            self._raise_provider_error(resp.text)
        data = resp.json()
        sms_list = data.get("sms") or []
        if sms_list:
            first = sms_list[-1]
            code = self._extract_sms_code(str(first.get("code") or first.get("text") or ""))
            return {"status": "ok", "code": code, "sms_text": str(first.get("text") or "")}
        status = str(data.get("status") or "").upper()
        if status in {"PENDING", "RECEIVED", "RECEIVED_PART"}:
            return {"status": "wait_code", "raw": data}
        if status in {"CANCELED", "CANCELLED", "BANNED"}:
            return {"status": "cancel", "raw": data}
        return {"status": "wait_code", "raw": data}

    def get_status_v2(self, activation_id: str) -> dict:
        return self.get_status(activation_id)

    def set_status(self, activation_id: str, status: int) -> str:
        if status == 6:
            resp = self._get(f"/user/finish/{activation_id}")
        elif status == 8:
            resp = self._get(f"/user/cancel/{activation_id}")
        else:
            resp = self._get(f"/user/check/{activation_id}")
        if resp.status_code >= 400:
            self._raise_provider_error(resp.text)
        return (resp.text or "").strip()

    def request_resend_sms(self, activation_id: str) -> str:
        return self._get(f"/user/check/{activation_id}").text

    def finish_activation(self, activation_id: str) -> bool:
        try:
            self._get(f"/user/finish/{activation_id}").raise_for_status()
            return True
        except Exception:
            return False

    def cancel_activation(self, activation_id: str) -> bool:
        try:
            self._get(f"/user/cancel/{activation_id}").raise_for_status()
            return True
        except Exception:
            return False

    def wait_for_code(self, activation_id: str, **kwargs):
        timeout = int(kwargs.get("timeout", 180))
        poll_interval = int(kwargs.get("poll_interval", 3))
        exclude_codes = {str(x).strip() for x in (kwargs.get("exclude_codes") or set()) if str(x).strip()}
        exclude_texts = {str(x).strip() for x in (kwargs.get("exclude_texts") or set()) if str(x).strip()}
        trace_callback = kwargs.get("trace_callback")
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self.get_status(activation_id)
            if trace_callback:
                trace_callback(f"5sim.get_status: status={result.get('status')}, code={result.get('code') or '-'}, text={(result.get('sms_text') or '')[:120]}")
            if result.get("status") == "ok":
                code = str(result.get("code") or "").strip()
                sms_text = str(result.get("sms_text") or "").strip()
                if code and code in exclude_codes:
                    time.sleep(poll_interval)
                    continue
                if sms_text and sms_text in exclude_texts:
                    time.sleep(poll_interval)
                    continue
                if code:
                    return code
            if result.get("status") == "cancel":
                return None
            time.sleep(poll_interval)
        return None

    @classmethod
    def parse_countries_response(cls, data: Any) -> list[dict]:
        results = []
        if not isinstance(data, dict):
            return results
        for slug, payload in data.items():
            if not isinstance(payload, dict):
                continue
            prefix = ""
            prefix_map = payload.get("prefix") or {}
            if isinstance(prefix_map, dict) and prefix_map:
                prefix = next(iter(prefix_map.keys()), "").lstrip("+")
            results.append({
                "country_key": str(slug),
                "apiName": str(payload.get("text_en") or slug),
                "isoCode": next(iter((payload.get("iso") or {}).keys()), "").upper() if isinstance(payload.get("iso"), dict) else "",
                "dialCode": prefix,
            })
        return results

    def _resolve_country_key(self) -> str:
        return str(self.config.country_key or "any").strip() or "any"

    def _resolve_operator(self) -> str:
        return "any"

    @staticmethod
    def _to_float_or_none(value: Any) -> Optional[float]:
        if value in (None, "", "null"):
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _extract_sms_code(text: str) -> str:
        import re
        match = re.search(r"(?<!\\d)(\\d{4,8})(?!\\d)", text or "")
        return match.group(1) if match else ""

    @staticmethod
    def _raise_provider_error(text: str):
        low = str(text or "").lower()
        if "not enough user balance" in low:
            raise SMSProviderNoBalanceError(text)
        if "bad key" in low or "unauthorized" in low:
            raise SMSProviderBadKeyError(text)
        if "no free phones" in low:
            raise SMSProviderNoNumbersError(text)
        if "bad country" in low:
            raise SMSProviderError(f"5SIM 国家无效: {text}")
        if "bad operator" in low:
            raise SMSProviderError(f"5SIM 运营商无效: {text}")
        if "no product" in low:
            raise SMSProviderError(f"5SIM 服务无效: {text}")
        if "server offline" in low or "internal error" in low:
            raise SMSProviderApiUnavailableError(text)
        raise SMSProviderError(text)
