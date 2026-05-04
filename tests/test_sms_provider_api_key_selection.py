from types import SimpleNamespace

from src.config import settings as settings_module
from src.core.openai import phone_verification
from src.core.herosms_client import HeroSMSActivation
from src.core.sms.herosms_provider import HeroSMSProvider
from src.core.sms.smsbower_provider import SMSBowerClient, SMSBowerProvider
from src.core.sms.base import SMSProviderConfig
from src.web.routes import settings as settings_routes


class _Secret:
    def __init__(self, value: str):
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def test_get_saved_sms_api_key_reads_selected_provider_key(monkeypatch):
    monkeypatch.setattr(
        settings_routes,
        "get_settings",
        lambda: SimpleNamespace(
            sms_provider="5sim",
            herosms_api_key=_Secret("hero-key"),
            smsbower_api_key=_Secret("bower-key"),
            fivesim_api_key=_Secret("five-key"),
        ),
    )

    assert settings_routes._get_saved_sms_api_key("5sim") == "five-key"
    assert settings_routes._get_saved_sms_api_key("smsbower") == "bower-key"
    assert settings_routes._get_saved_sms_api_key("herosms") == "hero-key"


def test_load_sms_settings_has_api_key_matches_selected_provider(monkeypatch):
    monkeypatch.setattr(
        settings_routes,
        "get_settings",
        lambda: SimpleNamespace(
            sms_provider="5sim",
            sms_operator="",
            sms_provider_ids="",
            sms_except_provider_ids="",
            sms_phone_exception="",
            sms_country_key="england",
            sms_min_price=-1,
            sms_reuse=False,
            sms_voice=False,
            sms_forwarding=False,
            sms_forwarding_number="",
            herosms_enabled=False,
            herosms_service="dr",
            herosms_country=187,
            herosms_max_price=-1,
            herosms_proxy="",
            herosms_timeout=30,
            herosms_verify_timeout=180,
            herosms_poll_interval=3,
            herosms_lowest_price_first=True,
            herosms_max_number_attempts=1,
            herosms_target_number_index=1,
            herosms_price_relax_enabled=True,
            herosms_price_relax_max_multiplier=5,
            herosms_reuse_enabled=False,
            herosms_reuse_max_uses=2,
            herosms_api_key=_Secret("hero-key"),
            smsbower_api_key=_Secret(""),
            fivesim_api_key=_Secret(""),
        ),
    )
    monkeypatch.setattr(settings_routes, "get_db", lambda: (_ for _ in ()).throw(RuntimeError("数据库未初始化")))

    data = settings_routes._load_herosms_settings_from_db()

    assert data["provider"] == "5sim"
    assert data["provider_display_name"] == "5SIM"
    assert data["has_api_key"] is False


def test_phone_verification_saved_api_key_uses_selected_provider(monkeypatch):
    monkeypatch.setattr(
        phone_verification,
        "get_settings",
        lambda: SimpleNamespace(
            sms_provider="smsbower",
            herosms_api_key=_Secret("hero-key"),
            smsbower_api_key=_Secret("bower-key"),
            fivesim_api_key=_Secret("five-key"),
        ),
    )

    assert phone_verification._get_saved_herosms_api_key() == "bower-key"


def test_normalize_sms_provider_aliases():
    assert settings_module.normalize_sms_provider_name("fivesim") == "5sim"
    assert settings_module.normalize_sms_provider_name("five_sim") == "5sim"
    assert settings_module.normalize_sms_provider_name("smsbower") == "smsbower"
    assert settings_module.normalize_sms_provider_name(None) == "herosms"


def test_herosms_activation_supports_extended_fields():
    activation = HeroSMSActivation(
        activation_id="1",
        phone_number="+123",
        raw_number="123",
        activation_time="2026-01-01T00:00:00Z",
        activation_operator="any",
        can_get_another_sms=True,
    )
    assert activation.activation_time == "2026-01-01T00:00:00Z"
    assert activation.activation_operator == "any"
    assert activation.can_get_another_sms is True


def test_herosms_provider_request_number_accepts_extended_kwargs(monkeypatch):
    provider = HeroSMSProvider(SMSProviderConfig(api_key="k"))

    monkeypatch.setattr(
        provider.client,
        "request_number",
        lambda **kwargs: HeroSMSActivation(
            activation_id="2",
            phone_number="+86123",
            raw_number="123",
            country_phone_code="86",
            activation_cost=0.1,
            activation_time="t",
            activation_operator="op",
            can_get_another_sms=False,
        ),
    )

    act = provider.request_number(
        provider_ids="1,2",
        except_provider_ids="3",
        phone_exception="7918",
        min_price=0.01,
        country_key="england",
        reuse=True,
        voice=True,
        forwarding=True,
        forwarding_number="12345678901",
    )
    assert act.activation_operator == "op"
    assert act.activation_time == "t"
    assert act.can_get_another_sms is False


def test_smsbower_client_parses_getnumber_json_response(monkeypatch):
    client = SMSBowerClient(config=type("Cfg", (), {
        "api_key": "k", "service": "dr", "country": 151, "max_price": None, "proxy": None, "timeout": 30
    })())

    class Resp:
        text = '{"activationId":"11","phoneNumber":"79123456789","activationCost":"0.01","countryCode":"7","canGetAnotherSms":true,"activationTime":"2026-01-01","activationOperator":"tele2"}'
        def json(self):
            return {
                "activationId": "11",
                "phoneNumber": "79123456789",
                "activationCost": "0.01",
                "countryCode": "7",
                "canGetAnotherSms": True,
                "activationTime": "2026-01-01",
                "activationOperator": "tele2",
            }

    monkeypatch.setattr(client, "_get", lambda *args, **kwargs: Resp())
    act = client.request_number(service="dr", country=151, provider_ids="2421", min_price=0.01)
    assert act.activation_id == "11"
    assert act.phone_number == "+79123456789"
    assert act.activation_operator == "tele2"
    assert act.can_get_another_sms is True


def test_smsbower_provider_maps_json_error_to_bad_key():
    try:
        SMSBowerProvider._raise_provider_error(Exception("No access"))
    except Exception as exc:
        assert exc.__class__.__name__ == "SMSProviderBadKeyError"
    else:
        raise AssertionError("expected bad key error")


def test_sms_provider_ui_meta():
    meta = settings_routes._get_sms_provider_ui_meta("5sim")
    assert meta["provider"] == "5sim"
    assert meta["label"] == "5SIM"
    assert meta["supports"]["provider_quotes"] is False
    assert meta["supports"]["operators"] is True
