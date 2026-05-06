from src.core.openai import phone_verification
from src.web.routes import accounts


def test_accounts_phone_stats_has_timedelta_import():
    assert accounts.timedelta(days=1).days == 1


def test_fraud_guard_is_extracted_and_policy_blocked():
    text = '''提交手机号失败: 400 {
      "error": {
        "message": "We've detected suspicious behavior from phone numbers similar to yours.",
        "code": "fraud_guard"
      }
    }'''

    assert phone_verification._extract_error_code_from_text(text) == "fraud_guard"
    assert phone_verification._classify_phone_failure_type("fraud_guard", text) == "policy_blocked"


def test_sms_timeout_failure_updates_invalid_status(monkeypatch):
    captured = {}

    monkeypatch.setattr(phone_verification, "_ensure_phone_stats_schema", lambda: None)

    class _Crud:
        @staticmethod
        def update_phone_verification_attempt(_db, attempt_id, **updates):
            captured["attempt_id"] = attempt_id
            captured.update(updates)
            return object()

    class _DbContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(phone_verification, "crud", _Crud)
    monkeypatch.setattr(phone_verification, "get_db", lambda: _DbContext())

    phone_verification._update_phone_verification_record(
        123,
        invalid=True,
        failure_stage="wait_sms_code",
        error_code="sms_code_timeout",
        error_message="等待短信验证码超时",
    )

    assert captured["attempt_id"] == 123
    assert captured["invalid"] is True
    assert captured["result_status"] == "invalid"
    assert captured["failure_type"] == "transient"
    assert captured["failure_stage"] == "wait_sms_code"
    assert captured["error_code"] == "sms_code_timeout"
