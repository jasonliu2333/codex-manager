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


def test_sms_timeout_and_unavailable_failures_are_blacklisted():
    assert phone_verification._should_blacklist_phone_failure("sms_code_timeout", "等待短信验证码超时") is True
    assert phone_verification._should_blacklist_phone_failure("runtime_error", "phone number is unavailable") is True
    assert phone_verification._should_blacklist_phone_failure("phone_max_usage_exceeded", "maximum number of accounts") is True


def test_phone_blacklist_is_scoped_by_sms_provider(monkeypatch):
    class _Record:
        def __init__(self, *, blacklisted=False, success_count=0):
            self.blacklisted = blacklisted
            self.success_count = success_count
            self.failure_count = 1
            self.last_error_code = "sms_code_timeout"
            self.last_error_message = "等待短信验证码超时"

    class _Crud:
        @staticmethod
        def get_phone_number_reputation(_db, provider, phone):
            if provider == "herosms" and phone == "+10086":
                return _Record(blacklisted=True)
            if provider == "smsbower" and phone == "+10086":
                return _Record(blacklisted=False)
            return None

    class _DbContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(phone_verification, "crud", _Crud)
    monkeypatch.setattr(phone_verification, "get_db", lambda: _DbContext())

    assert phone_verification._is_phone_blacklisted("herosms", "+10086") is not None
    assert phone_verification._is_phone_blacklisted("smsbower", "+10086") is None


def test_phone_blacklist_hits_when_success_count_reaches_limit(monkeypatch):
    class _Record:
        blacklisted = False
        success_count = 3
        failure_count = 0
        last_error_code = None
        last_error_message = None

    class _Crud:
        @staticmethod
        def get_phone_number_reputation(_db, provider, phone):
            return _Record()

    class _DbContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(phone_verification, "crud", _Crud)
    monkeypatch.setattr(phone_verification, "get_db", lambda: _DbContext())

    info = phone_verification._is_phone_blacklisted("herosms", "+10086")
    assert info is not None
    assert info["success_count"] == 3


def test_phone_stats_country_display_uses_chinese_english_names():
    assert accounts._format_phone_stats_country("herosms", 16, "") == "英国(England)"
    assert accounts._format_phone_stats_country("herosms", None, "england") == "英国(England)"
    assert accounts._format_phone_stats_country("herosms", 187, "") == "美国(United States)"
    assert accounts._format_phone_stats_country("smsbower", 76, "") == "安哥拉(Angola)"


def test_phone_stats_country_display_prefers_sms_provider_cache(monkeypatch):
    def _fake_cache_get(section, cache_key, *, allow_stale=False):
        assert section == "countries"
        assert cache_key == "smsbower"
        assert allow_stale is True
        return {
            "data": [
                {
                    "code": 76,
                    "country_key": "",
                    "name": "Cachedland",
                    "zh_name": "缓存国",
                    "en_name": "Cachedland",
                }
            ]
        }

    monkeypatch.setattr(accounts, "_sms_provider_cache_get", _fake_cache_get)

    assert accounts._format_phone_stats_country("smsbower", 76, "") == "缓存国(Cachedland)"


def test_phone_stats_country_display_fallback_to_code():
    """无映射时回退到 country 代码或 '-'。"""
    assert accounts._format_phone_stats_country("herosms", 999, "") == "999"
    assert accounts._format_phone_stats_country("herosms", 999, "unknown_slug") == "Unknown Slug"
    assert accounts._format_phone_stats_country("herosms", None, None) == "-"


def test_all_number_invalid_errors_are_blacklisted():
    """phone_number_in_use/blocked/invalid/not_supported/unavailable 均应黑名单化。"""
    assert phone_verification._should_blacklist_phone_failure("phone_number_in_use", "phone number already in use") is True
    assert phone_verification._should_blacklist_phone_failure("phone_number_blocked", "phone number blocked") is True
    assert phone_verification._should_blacklist_phone_failure("phone_number_invalid", "invalid phone number") is True
    assert phone_verification._should_blacklist_phone_failure("phone_number_not_supported", "unsupported phone number") is True
    assert phone_verification._should_blacklist_phone_failure("phone_number_banned", "phone number banned") is True
    assert phone_verification._should_blacklist_phone_failure("runtime_error", "phone verification failed for this number") is True
    assert phone_verification._should_blacklist_phone_failure("runtime_error", "temporarily unavailable") is True


def test_phone_reputation_blacklist_flag_is_read_back_by_is_blacklisted(monkeypatch):
    """upsert 写入的 blacklisted=True 能被 _is_phone_blacklisted 读取到。"""

    class _Record:
        def __init__(self, blacklisted=False, success_count=0, failure_count=0):
            self.blacklisted = blacklisted
            self.success_count = success_count
            self.failure_count = failure_count
            self.last_error_code = "phone_max_usage_exceeded"
            self.last_error_message = "maximum number of accounts"

    stored = {}

    class _Crud:
        @staticmethod
        def get_phone_number_reputation(_db, provider, phone):
            return stored.get((provider, phone))

        @staticmethod
        def upsert_phone_number_reputation(
            _db, *, sms_provider, phone_number, success, blacklisted, error_code,
            error_message, result_label, **kwargs
        ):
            key = (sms_provider, phone_number)
            if key not in stored:
                stored[key] = _Record()
            rec = stored[key]
            if success:
                rec.success_count += 1
                if rec.success_count >= 3:
                    rec.blacklisted = True
                    rec.last_error_code = "phone_success_usage_limit"
                    rec.last_error_message = "号码成功使用已达到 3 次上限"
                rec.last_result = result_label or "success"
            else:
                rec.failure_count += 1
                rec.last_result = result_label or "failed"
            if blacklisted:
                rec.blacklisted = True
            if error_code:
                rec.last_error_code = error_code
            if error_message:
                rec.last_error_message = error_message
            return rec

    class _DbContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(phone_verification, "crud", _Crud)
    monkeypatch.setattr(phone_verification, "get_db", lambda: _DbContext())

    # 成功 3 次后应自动命中黑名单
    for _ in range(3):
        phone_verification._record_phone_reputation(
            provider_name="herosms", phone_number="+10086", service="dr",
            country=16, country_key="england", provider_slot=None,
            success=True, blacklisted=False, error_code=None, error_message=None,
            activation_cost=0.1, result_label="success",
        )

    info = phone_verification._is_phone_blacklisted("herosms", "+10086")
    assert info is not None
    assert info["success_count"] == 3
