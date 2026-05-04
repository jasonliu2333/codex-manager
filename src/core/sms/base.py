"""短信接码平台抽象层。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(slots=True)
class SMSActivation:
    activation_id: str
    phone_number: str
    raw_number: str
    country_phone_code: str = ""
    activation_cost: Optional[float] = None
    activation_time: Optional[str] = None
    activation_operator: Optional[str] = None
    can_get_another_sms: Optional[bool] = None


@dataclass(slots=True)
class SMSProviderConfig:
    api_key: str
    provider: str = "herosms"
    service: str = "dr"
    country: int = 187
    country_key: str = ""
    max_price: Optional[float] = None
    min_price: Optional[float] = None
    proxy: Optional[str] = None
    timeout: int = 30
    provider_ids: str = ""
    except_provider_ids: str = ""
    phone_exception: str = ""
    reuse: bool = False
    voice: bool = False
    forwarding: bool = False
    forwarding_number: str = ""


class SMSProviderError(RuntimeError):
    code = "provider_error"


class SMSProviderNoBalanceError(SMSProviderError):
    code = "no_balance"


class SMSProviderBadKeyError(SMSProviderError):
    code = "bad_key"


class SMSProviderNoNumbersError(SMSProviderError):
    code = "no_numbers"


class SMSProviderApiUnavailableError(SMSProviderError):
    code = "api_unavailable"


class BaseSMSProvider(ABC):
    def __init__(self, config: SMSProviderConfig):
        self.config = config

    @abstractmethod
    def get_balance(self) -> float:
        raise NotImplementedError

    @abstractmethod
    def get_countries(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_services(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def get_lowest_price(self, service: Optional[str] = None, country: Optional[int] = None) -> Optional[float]:
        raise NotImplementedError

    @abstractmethod
    def list_country_prices(self, service: Optional[str] = None, countries: Optional[list[dict]] = None) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_top_countries_by_service(self, service: Optional[str] = None) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_operators(self, country: int) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def get_operator_quote_options(self, service: Optional[str], country: int) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_provider_price_options(self, service: Optional[str], country: int) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_static_wallet(self, coin: str, network: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def set_status(self, activation_id: str, status: int) -> str:
        raise NotImplementedError

    @abstractmethod
    def request_resend_sms(self, activation_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def finish_activation(self, activation_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def cancel_activation(self, activation_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError


class PlaceholderSMSProvider(BaseSMSProvider):
    provider_name = "placeholder"

    def _not_impl(self, *_args: Any, **_kwargs: Any):
        raise NotImplementedError(f"{self.provider_name} 平台尚未实现")

    get_balance = _not_impl
    get_countries = _not_impl
    get_services = _not_impl
    request_number = _not_impl
    get_lowest_price = _not_impl
    list_country_prices = _not_impl
    get_top_countries_by_service = _not_impl
    get_operators = _not_impl
    get_operator_quote_options = _not_impl
    get_provider_price_options = _not_impl
    get_static_wallet = _not_impl
    set_status = _not_impl
    request_resend_sms = _not_impl
    finish_activation = _not_impl
    cancel_activation = _not_impl
    wait_for_code = _not_impl
