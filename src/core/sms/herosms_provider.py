"""HeroSMS 平台实现。"""

from __future__ import annotations

import json
from typing import Any, Optional

from ..herosms_client import HeroSMSClient, HeroSMSConfig
from .base import (
    BaseSMSProvider,
    SMSActivation,
    SMSProviderApiUnavailableError,
    SMSProviderBadKeyError,
    SMSProviderConfig,
    SMSProviderNoBalanceError,
    SMSProviderNoNumbersError,
)


class HeroSMSProvider(BaseSMSProvider):
    provider_name = "herosms"

    def __init__(self, config: SMSProviderConfig):
        super().__init__(config)
        self.client = HeroSMSClient(HeroSMSConfig(
            api_key=config.api_key,
            service=config.service,
            country=config.country,
            max_price=config.max_price,
            proxy=config.proxy,
            timeout=config.timeout,
        ))

    def get_balance(self) -> float:
        return self.client.get_balance()

    def get_countries(self) -> list[dict]:
        raw = self.client.get_countries()
        return self.parse_countries_response(raw)

    def request_number(
        self,
        service: Optional[str] = None,
        country: Optional[int] = None,
        max_price: Optional[float] = None,
        operator: Optional[str] = None,
    ) -> SMSActivation:
        try:
            activation = self.client.request_number(
                service=service or self.config.service,
                country=country if country is not None else self.config.country,
                max_price=max_price,
                operator=operator,
            )
        except Exception as exc:
            self._raise_provider_error(exc)
        return SMSActivation(
            activation_id=activation.activation_id,
            phone_number=activation.phone_number,
            raw_number=activation.raw_number,
            country_phone_code=activation.country_phone_code,
            activation_cost=activation.activation_cost,
        )

    def get_lowest_price(self, service: Optional[str] = None, country: Optional[int] = None) -> Optional[float]:
        service = service or self.config.service
        country = self.config.country if country is None else country
        prices = self.get_prices(service=service)
        extracted = self.extract_country_price(prices, country, service)
        if extracted and extracted.get("price") is not None:
            return extracted["price"]
        return self.client.get_lowest_price(service=service, country=country)

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
        return sorted(priced, key=lambda x: (x.get("price") if x.get("price") is not None else 999999, -(x.get("count") or 0)))

    def get_top_countries_by_service(self, service: Optional[str] = None) -> list[dict]:
        service = service or self.config.service
        actions = ("getTopCountriesByServiceRank", "getTopCountriesByService")
        last_error = None
        for action in actions:
            try:
                resp = self.client._get({"action": action, "service": service})
                data = resp.json() if hasattr(resp, "json") else resp
                rows = self.parse_top_countries_response(data)
                if rows:
                    return sorted(rows, key=lambda x: (x.get("price") if x.get("price") is not None else 999999, -(x.get("count") or 0)))
                last_error = RuntimeError(f"{action} 返回空结果")
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        return []

    def get_operators(self, country: int) -> list[str]:
        resp = self.client._get({"action": "getOperators", "country": country})
        try:
            data = resp.json()
        except Exception:
            return []
        raw = None
        if isinstance(data, dict):
            raw = data.get("countryOperators", {}).get(str(country)) or data.get("countryOperators", {}).get(country) or []
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    def get_operator_quote_options(self, service: Optional[str], country: int) -> list[dict]:
        service = service or self.config.service
        operators = self.get_operators(country)
        options = []
        for operator in operators:
            try:
                resp = self.client._get({"action": "getPrices", "service": service, "country": country, "operator": operator})
                data = resp.json()
                parsed = self.extract_country_price(data, country, service)
                options.append({
                    "operator": operator,
                    "price": parsed.get("price") if parsed else None,
                    "count": parsed.get("count") if parsed else None,
                    "source": "operator",
                })
            except Exception as exc:
                options.append({
                    "operator": operator,
                    "price": None,
                    "count": None,
                    "source": "operator",
                    "error": str(exc),
                })
        return options

    def set_status(self, activation_id: str, status: int) -> str:
        return self.client.set_status(activation_id, status)

    def request_resend_sms(self, activation_id: str) -> str:
        return self.client.request_resend_sms(activation_id)

    def finish_activation(self, activation_id: str) -> bool:
        return self.client.finish_activation(activation_id)

    def cancel_activation(self, activation_id: str) -> bool:
        return self.client.cancel_activation(activation_id)

    def wait_for_code(self, *args, **kwargs):
        return self.client.wait_for_code(*args, **kwargs)

    def get_prices(self, service: Optional[str] = None) -> Any:
        actions = ("getPricesVerification", "getPrices")
        last_error = None
        for action in actions:
            try:
                resp = self.client._get({"action": action, "service": service or self.config.service})
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

    @classmethod
    def parse_countries_response(cls, data: Any) -> list[dict]:
        result: list[dict] = []

        def push_country(country_id: Any, payload: Any):
            hero_sms_country = cls._parse_int(country_id)
            if hero_sms_country is None:
                return
            if isinstance(payload, str):
                result.append({"heroSmsCountry": hero_sms_country, "apiName": payload.strip()})
                return
            if not isinstance(payload, dict):
                return
            api_name = str(payload.get("name") or payload.get("country") or payload.get("title") or payload.get("eng") or payload.get("en") or payload.get("label") or "").strip()
            iso_code = str(payload.get("isoCode") or payload.get("iso") or payload.get("code") or payload.get("iso2") or "").strip().upper()
            dial_code = str(payload.get("dialCode") or payload.get("phoneCode") or payload.get("prefix") or "").lstrip("+").strip()
            result.append({
                "heroSmsCountry": hero_sms_country,
                "apiName": api_name,
                "isoCode": iso_code,
                "dialCode": dial_code,
            })

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    push_country(item.get("id") or item.get("countryId") or item.get("country_id"), item)
            return result

        if not isinstance(data, dict):
            return result

        for key, value in data.items():
            if str(key).isdigit():
                push_country(key, value)
                continue
            if isinstance(value, dict):
                nested_id = value.get("id") or value.get("countryId") or value.get("country_id")
                if nested_id is not None:
                    push_country(nested_id, {
                        **value,
                        "name": value.get("name") or value.get("chn") or value.get("eng") or value.get("rus") or key,
                        "isoCode": value.get("isoCode") or value.get("iso") or value.get("code") or value.get("iso2") or "",
                    })
        if result:
            return result

        for value in data.values():
            if isinstance(value, (list, dict)):
                parsed = cls.parse_countries_response(value)
                if parsed:
                    result.extend(parsed)
                    break
        return result

    @classmethod
    def parse_top_countries_response(cls, data: Any) -> list[dict]:
        rows: list[dict] = []

        def push_row(item: Any):
            if not isinstance(item, dict):
                return
            hero_sms_country = cls._parse_int(item.get("country") or item.get("countryId") or item.get("country_id") or item.get("id"))
            price = cls._parse_float(item.get("price") or item.get("cost") or item.get("retail_price") or item.get("retailPrice"))
            count = cls._parse_int(item.get("count") or item.get("qty") or item.get("available") or item.get("stock") or item.get("total"))
            if hero_sms_country is None or price is None:
                return
            rows.append({
                "heroSmsCountry": hero_sms_country,
                "price": price,
                "count": count,
                "apiName": str(item.get("name") or item.get("countryName") or item.get("country_name") or item.get("title") or item.get("text") or item.get("label") or item.get("countryText") or "").strip(),
                "isoCode": str(item.get("isoCode") or item.get("iso") or item.get("code") or item.get("iso2") or "").strip().upper(),
                "dialCode": str(item.get("dialCode") or item.get("phoneCode") or item.get("prefix") or item.get("phone_prefix") or "").lstrip("+").strip(),
            })

        if isinstance(data, list):
            for item in data:
                push_row(item)
            return rows
        if not isinstance(data, dict):
            return rows
        for key, value in data.items():
            if str(key).isdigit() and isinstance(value, dict):
                push_row(value)
        if rows:
            return rows
        for key in ("data", "result", "response"):
            nested = data.get(key)
            if isinstance(nested, (list, dict)):
                parsed = cls.parse_top_countries_response(nested)
                if parsed:
                    return parsed
        return rows

    @classmethod
    def unwrap_price_matrix(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        for key in ("data", "result", "prices", "countries", "response"):
            value = raw.get(key)
            if isinstance(value, dict):
                return cls.unwrap_price_matrix(value)
        return raw

    @classmethod
    def extract_country_price(cls, raw: Any, country_id: int, service: str) -> Optional[dict]:
        matrix = cls.unwrap_price_matrix(raw)
        if isinstance(matrix, list):
            for item in matrix:
                if not isinstance(item, dict):
                    continue
                item_country_id = cls._parse_int(item.get("countryId") or item.get("country_id") or item.get("country") or item.get("id"))
                if item_country_id != int(country_id):
                    continue
                direct = cls.extract_price_from_node(item)
                if direct:
                    return direct
                service_node = item.get(str(service)) or item.get("serviceData") or item.get("data")
                return cls.extract_price_from_node(service_node)
            return None
        if not isinstance(matrix, dict):
            return None
        id_key = str(country_id)
        service_key = str(service)
        candidates = [
            matrix.get(service_key, {}).get(id_key) if isinstance(matrix.get(service_key), dict) else None,
            matrix.get(id_key, {}).get(service_key) if isinstance(matrix.get(id_key), dict) else None,
            matrix.get(id_key, {}).get("default") if isinstance(matrix.get(id_key), dict) else None,
            matrix.get(id_key),
            matrix.get(service_key),
        ]
        for candidate in candidates:
            parsed = cls.extract_price_from_node(candidate)
            if parsed:
                return parsed
        return None

    @classmethod
    def extract_price_from_node(cls, node: Any) -> Optional[dict]:
        if not isinstance(node, dict):
            return None
        price = cls._parse_float(node.get("cost") or node.get("price") or node.get("activationCost") or node.get("amount") or node.get("rate"))
        count = cls._parse_int(node.get("count") or node.get("qty") or node.get("available") or node.get("stock") or node.get("total"))
        if price is None and count is None:
            return None
        return {"price": price, "count": count}

    @staticmethod
    def _parse_float(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        text = "".join(ch for ch in str(value) if ch.isdigit() or ch in ".-")
        try:
            return float(text)
        except Exception:
            return None

    @staticmethod
    def _parse_int(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        text = "".join(ch for ch in str(value) if ch.isdigit() or ch == "-")
        try:
            return int(text)
        except Exception:
            return None

    @staticmethod
    def _raise_provider_error(exc: Exception):
        text = str(exc or "")
        upper = text.upper()
        if "NO_BALANCE" in upper or "余额不足" in text:
            raise SMSProviderNoBalanceError(text)
        if "BAD_KEY" in upper or "API KEY 无效" in text.upper() or "API KEY" in text.upper():
            raise SMSProviderBadKeyError(text)
        if "NO_NUMBERS" in upper or "当前无可用号码" in text:
            raise SMSProviderNoNumbersError(text)
        if "HTTP ERROR" in upper or "API 不可用" in text or "TIMED OUT" in upper:
            raise SMSProviderApiUnavailableError(text)
        raise exc
