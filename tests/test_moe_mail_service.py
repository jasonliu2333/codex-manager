from src.services.base import EmailServiceError
from src.services.moe_mail import MeoMailEmailService


class FakeMoeMailService(MeoMailEmailService):
    def __init__(self):
        super().__init__({
            "base_url": "https://mail.example.test",
            "api_key": "test-key",
        })
        self.mailboxes_by_id = {}
        self.list_pages = []
        self.created = []

    def _make_request(self, method: str, endpoint: str, **kwargs):
        if method == "GET" and endpoint.startswith("/api/emails/") and endpoint.count("/") == 3:
            email_id = endpoint.rsplit("/", 1)[-1]
            if email_id in self.mailboxes_by_id:
                return self.mailboxes_by_id[email_id]
            raise EmailServiceError("API 请求失败: 404 - {'error': '邮箱不存在'}")

        if method == "GET" and endpoint == "/api/emails":
            cursor = (kwargs.get("params") or {}).get("cursor")
            return self.list_pages_by_cursor.get(cursor)

        raise AssertionError(f"Unexpected request: {method} {endpoint} {kwargs}")

    @property
    def list_pages_by_cursor(self):
        pages = {None: {"emails": []}}
        for cursor, payload in self.list_pages:
            pages[cursor] = payload
        return pages

    def create_email(self, config=None):
        config = config or {}
        self.created.append(config)
        email = f"{config['name']}@{config['domain']}"
        return {
            "email": email,
            "service_id": "new-mailbox-id",
            "id": "new-mailbox-id",
        }


def test_ensure_mailbox_reuses_existing_mailbox_id():
    service = FakeMoeMailService()
    service.mailboxes_by_id["known-id"] = {"messages": []}

    mailbox = service.ensure_mailbox("known@example.com", "known-id")

    assert mailbox["email"] == "known@example.com"
    assert mailbox["service_id"] == "known-id"
    assert service.created == []


def test_ensure_mailbox_falls_back_to_existing_address_after_stale_id():
    service = FakeMoeMailService()
    service.list_pages = [
        (None, {"emails": [{"id": "existing-id", "email": "known@example.com"}]}),
    ]

    mailbox = service.ensure_mailbox("known@example.com", "stale-id")

    assert mailbox["email"] == "known@example.com"
    assert mailbox["service_id"] == "existing-id"
    assert service.created == []


def test_ensure_mailbox_creates_when_address_does_not_exist():
    service = FakeMoeMailService()
    service.list_pages = [(None, {"emails": []})]

    mailbox = service.ensure_mailbox("new@example.com", "stale-id")

    assert mailbox["email"] == "new@example.com"
    assert mailbox["service_id"] == "new-mailbox-id"
    assert service.created == [{"name": "new", "domain": "example.com"}]


def test_ensure_mailbox_recovers_from_create_conflict_by_relisting():
    class ConflictThenReuseService(FakeMoeMailService):
        def create_email(self, config=None):
            self.created.append(config or {})
            raise EmailServiceError("API 请求失败: 409 - {'error': '该邮箱地址已被使用'}")

    service = ConflictThenReuseService()
    service.list_pages = [
        (None, {"emails": []}),
        ("cursor-1", {"emails": [{"id": "existing-id", "email": "known@example.com"}]}),
    ]

    original_find = service.find_email_by_address
    calls = {"count": 0}

    def wrapped_find(email: str, max_pages: int = 50):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return {
            "email": "known@example.com",
            "service_id": "existing-id",
            "id": "existing-id",
        }

    service.find_email_by_address = wrapped_find

    mailbox = service.ensure_mailbox("known@example.com", "stale-id")

    assert mailbox["service_id"] == "existing-id"
    assert service.created == [{"name": "known", "domain": "example.com"}]
