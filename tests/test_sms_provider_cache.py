import asyncio
import json
import time

import pytest
from fastapi import HTTPException

from src.web.routes import settings as settings_routes


def _cache_payload(rows, *, ts=None):
    return {
        "countries": {
            "herosms": {
                "ts": ts if ts is not None else time.time(),
                "data": rows,
            }
        }
    }


def test_sms_countries_uses_valid_cache_without_provider_call(monkeypatch, tmp_path):
    rows = [{"code": 16, "country_key": "", "name": "England", "zh_name": "英国", "en_name": "England", "display": "英国(England) - 16"}]
    cache_path = tmp_path / "sms_provider_cache.json"
    cache_path.write_text(json.dumps(_cache_payload(rows), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings_routes, "SMS_PROVIDER_CACHE_PATH", cache_path)

    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("provider should not be called when cache is valid")

    monkeypatch.setattr(settings_routes, "_build_sms_provider_from_settings", _should_not_call)

    result = asyncio.run(settings_routes.get_sms_countries(provider="herosms", refresh=False))

    assert result["countries"] == rows
    assert result["cached"] is True
    assert result["stale"] is False


def test_sms_countries_returns_stale_cache_when_provider_fails(monkeypatch, tmp_path):
    rows = [{"code": 16, "country_key": "", "name": "England", "zh_name": "英国", "en_name": "England", "display": "英国(England) - 16"}]
    cache_path = tmp_path / "sms_provider_cache.json"
    old_ts = time.time() - settings_routes.SMS_PROVIDER_CACHE_TTL - 60
    cache_path.write_text(json.dumps(_cache_payload(rows, ts=old_ts), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings_routes, "SMS_PROVIDER_CACHE_PATH", cache_path)

    def _provider_fails(*_args, **_kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(settings_routes, "_build_sms_provider_from_settings", _provider_fails)

    result = asyncio.run(settings_routes.get_sms_countries(provider="herosms", refresh=False))

    assert result["countries"] == rows
    assert result["cached"] is True
    assert result["stale"] is True
    assert "network unavailable" in result["warning"]


def test_sms_countries_raises_502_when_provider_fails_without_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_routes, "SMS_PROVIDER_CACHE_PATH", tmp_path / "missing_sms_provider_cache.json")

    def _provider_fails(*_args, **_kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(settings_routes, "_build_sms_provider_from_settings", _provider_fails)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(settings_routes.get_sms_countries(provider="herosms", refresh=False))

    assert exc.value.status_code == 502
