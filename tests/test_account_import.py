from pathlib import Path

from src.database.models import Account, Base
from src.database.session import DatabaseSessionManager
from src.web.routes.accounts import _normalize_account_import_record, _upsert_imported_accounts


def _build_test_db(name: str) -> DatabaseSessionManager:
    runtime_dir = Path("tests_runtime")
    runtime_dir.mkdir(exist_ok=True)
    db_path = runtime_dir / name
    if db_path.exists():
        db_path.unlink()

    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)
    return manager


def test_normalize_account_import_record_supports_exported_csv_headers():
    record = _normalize_account_import_record({
        "Email": "demo@example.com",
        "Password": "secret",
        "Client ID": "client-1",
        "Email Service": "outlook",
        "Registered At": "2026-03-24T12:00:00",
    })

    assert record["email"] == "demo@example.com"
    assert record["password"] == "secret"
    assert record["client_id"] == "client-1"
    assert record["email_service"] == "outlook"
    assert record["registered_at"].isoformat() == "2026-03-24T12:00:00"


def test_upsert_imported_accounts_creates_and_updates_by_email():
    manager = _build_test_db("account_import.db")

    with manager.session_scope() as session:
        result = _upsert_imported_accounts(session, [
            {
                "email": "demo@example.com",
                "password": "secret",
                "email_service": "outlook",
                "access_token": "ak-1",
                "status": "active",
            }
        ])

        assert result["created_count"] == 1
        assert result["updated_count"] == 0
        assert result["failed_count"] == 0

        account = session.query(Account).filter(Account.email == "demo@example.com").one()
        assert account.password == "secret"
        assert account.access_token == "ak-1"

        result = _upsert_imported_accounts(session, [
            {
                "Email": "demo@example.com",
                "Password": "updated-secret",
                "Access Token": "ak-2",
                "Status": "expired",
            }
        ])

        assert result["created_count"] == 0
        assert result["updated_count"] == 1
        assert result["failed_count"] == 0

        session.refresh(account)
        assert account.password == "updated-secret"
        assert account.access_token == "ak-2"
        assert account.status == "expired"
