from types import SimpleNamespace

from src.web.routes import settings as settings_routes


class _DbContext:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


def test_load_proxy_settings_backfills_seekproxy_profile_from_compat_fields(monkeypatch):
    db_values = {
        "proxy.dynamic_provider": "seekproxy",
        "proxy.dynamic_mode": "api",
        "proxy.dynamic_profiles": "{}",
        "proxy.dynamic_seekproxy_trade_no": "TRADE-001",
        "proxy.dynamic_seekproxy_key": "KEY-001",
        "proxy.dynamic_seekproxy_auth_type": "2",
        "proxy.dynamic_seekproxy_ip_count": "3",
        "proxy.dynamic_seekproxy_protocol": "0",
        "proxy.dynamic_seekproxy_pattern": "0",
        "proxy.dynamic_seekproxy_valid_code": "0",
        "proxy.dynamic_country": "US",
    }

    monkeypatch.setattr(settings_routes, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(settings_routes, "get_db", lambda: _DbContext())
    monkeypatch.setattr(
        settings_routes.crud,
        "get_setting",
        lambda _db, key: SimpleNamespace(value=db_values[key]) if key in db_values else None,
    )

    data = settings_routes._load_proxy_settings_from_db()
    profile = data["dynamic_profiles"]["seekproxy::api"]

    assert profile["trade_no"] == "TRADE-001"
    assert profile["key"] == "KEY-001"
    assert profile["auth_type"] == 2
    assert profile["ip_count"] == 3
    assert profile["country"] == "US"


def test_seekproxy_profile_payload_keeps_saved_secret_when_request_is_empty(monkeypatch):
    monkeypatch.setattr(
        settings_routes,
        "_load_proxy_settings_from_db",
        lambda: {
            "dynamic_profiles": {
                "seekproxy::api": {
                    "trade_no": "TRADE-OLD",
                    "key": "KEY-OLD",
                    "auth_type": 2,
                    "ip_count": 1,
                }
            }
        },
    )
    monkeypatch.setattr(settings_routes, "_get_saved_dynamic_seekproxy_trade_no", lambda: "TRADE-FALLBACK")
    monkeypatch.setattr(settings_routes, "_get_saved_dynamic_seekproxy_key", lambda: "KEY-FALLBACK")

    payload = settings_routes._build_dynamic_profile_payload(
        settings_routes.DynamicProxySettings(
            mode="api",
            provider="seekproxy",
            seekproxy_trade_no="",
            seekproxy_key="",
            country="US",
        )
    )

    assert payload["trade_no"] == "TRADE-OLD"
    assert payload["key"] == "KEY-OLD"
