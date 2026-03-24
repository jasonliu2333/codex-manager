from pathlib import Path

from src.database.models import Account, Base, EmailService
from src.database.session import DatabaseSessionManager
from src.web.routes.accounts import _find_mailbox_service_for_account


def _build_test_db(name: str) -> DatabaseSessionManager:
    runtime_dir = Path("tests_runtime")
    runtime_dir.mkdir(exist_ok=True)
    db_path = runtime_dir / name
    if db_path.exists():
        db_path.unlink()

    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)
    return manager


def test_find_mailbox_service_for_account_scans_beyond_200_candidates():
    manager = _build_test_db("mailbox_match_large.db")

    with manager.session_scope() as session:
        for idx in range(250):
            email = f"user{idx:03d}@outlook.com"
            session.add(
                EmailService(
                    service_type="outlook",
                    name=email,
                    config={"email": email, "password": "pw"},
                    enabled=True,
                    priority=0,
                )
            )

        target_email = "mary@example.com"
        target_service = EmailService(
            service_type="outlook",
            name=target_email,
            config={"email": target_email, "password": "pw"},
            enabled=True,
            priority=0,
        )
        session.add(target_service)
        session.flush()

        account = Account(
            email=target_email,
            email_service="outlook",
        )

        matched = _find_mailbox_service_for_account(session, account)

        assert matched is not None
        assert matched.id == target_service.id


def test_find_mailbox_service_for_account_returns_none_without_exact_match():
    manager = _build_test_db("mailbox_match_exact.db")

    with manager.session_scope() as session:
        session.add(
            EmailService(
                service_type="outlook",
                name="first@example.com",
                config={"email": "first@example.com", "password": "pw"},
                enabled=True,
                priority=0,
            )
        )
        session.add(
            EmailService(
                service_type="outlook",
                name="second@example.com",
                config={"email": "second@example.com", "password": "pw"},
                enabled=True,
                priority=0,
            )
        )

        account = Account(
            email="missing@example.com",
            email_service="outlook",
        )

        matched = _find_mailbox_service_for_account(session, account)

        assert matched is None
