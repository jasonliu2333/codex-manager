"""短信平台工厂。"""

from __future__ import annotations

from .base import BaseSMSProvider, PlaceholderSMSProvider, SMSProviderConfig
from .herosms_provider import HeroSMSProvider


class SMSBowerProvider(PlaceholderSMSProvider):
    provider_name = "smsbower"


class FiveSimProvider(PlaceholderSMSProvider):
    provider_name = "5sim"


def get_sms_provider(config: SMSProviderConfig) -> BaseSMSProvider:
    provider = (config.provider or "herosms").strip().lower()
    if provider == "herosms":
        return HeroSMSProvider(config)
    if provider == "smsbower":
        return SMSBowerProvider(config)
    if provider in {"5sim", "five_sim", "fivesim"}:
        return FiveSimProvider(config)
    raise ValueError(f"不支持的短信平台: {config.provider}")
