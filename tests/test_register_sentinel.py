from src.config.constants import EmailServiceType
from src.core.register import RegistrationEngine
from src.services.base import BaseEmailService


class DummyEmailService(BaseEmailService):
    def __init__(self):
        super().__init__(EmailServiceType.TEMPMAIL, "dummy")

    def create_email(self, config=None):
        return {"email": "dummy@example.com", "service_id": "svc-1"}

    def get_verification_code(self, email, email_id=None, timeout=120, pattern=r"(?<!\d)(\d{6})(?!\d)", otp_sent_at=None):
        return "123456"

    def list_emails(self, **kwargs):
        return []

    def delete_email(self, email_id: str) -> bool:
        return True

    def check_health(self) -> bool:
        return True


class DummyResponse:
    status_code = 200
    text = "{}"


class DummySession:
    def __init__(self):
        self.last_headers = None
        self.last_url = None

    def post(self, url, headers=None, data=None):
        self.last_url = url
        self.last_headers = headers or {}
        return DummyResponse()


def test_create_user_account_includes_sentinel_header(monkeypatch):
    engine = RegistrationEngine(DummyEmailService())
    engine.session = DummySession()
    engine.device_id = "did-123"

    monkeypatch.setattr(
        "src.core.register.generate_random_user_info",
        lambda: {"name": "Test User", "birthdate": "1990-01-01"},
    )
    monkeypatch.setattr(engine, "_check_sentinel", lambda did: "sen-token" if did == "did-123" else None)

    assert engine._create_user_account() is True
    assert "openai-sentinel-token" in engine.session.last_headers
    assert engine.session.last_headers["openai-sentinel-token"] == (
        '{"p":"","t":"","c":"sen-token","id":"did-123","flow":"authorize_continue"}'
    )
