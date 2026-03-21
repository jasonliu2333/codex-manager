import base64
import json

from src.config.constants import EmailServiceType
from src.core.register import RegistrationEngine
from src.services.base import BaseEmailService


def _b64url(data):
    return base64.urlsafe_b64encode(json.dumps(data).encode("utf-8")).decode("ascii").rstrip("=")


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


class DummyCookies:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, key, default=None):
        return self.mapping.get(key, default)


class DummySession:
    def __init__(self, cookie_value):
        self.cookies = DummyCookies({"oai-client-auth-session": cookie_value})


def test_get_workspace_id_reads_jwt_payload_segment():
    header = _b64url({"alg": "HS256", "typ": "JWT"})
    payload = _b64url({"workspaces": [{"id": "ws_from_payload"}]})
    engine = RegistrationEngine(DummyEmailService())
    engine.session = DummySession(f"{header}.{payload}.sig")

    assert engine._get_workspace_id() == "ws_from_payload"


def test_get_workspace_id_supports_auth_claim_workspace_id():
    header = _b64url({"alg": "HS256", "typ": "JWT"})
    payload = _b64url({"https://api.openai.com/auth": {"chatgpt_workspace_id": "ws_auth_claim"}})
    engine = RegistrationEngine(DummyEmailService())
    engine.session = DummySession(f"{header}.{payload}.sig")

    assert engine._get_workspace_id() == "ws_auth_claim"


def test_get_workspace_id_prefers_create_account_response_data():
    engine = RegistrationEngine(DummyEmailService())
    engine._create_account_response_data = {"organizations": [{"workspace_id": "ws_from_response"}]}

    assert engine._get_workspace_id() == "ws_from_response"


def test_get_continue_url_after_signup_prefers_create_account_response_url():
    engine = RegistrationEngine(DummyEmailService())
    engine._post_signup_continue_url = "https://auth.openai.com/continue/example"

    assert engine._get_continue_url_after_signup(None) == "https://auth.openai.com/continue/example"
