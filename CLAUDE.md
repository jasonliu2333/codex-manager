# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenAI 自动注册系统 (codex-register-v2) — a FastAPI + SQLAlchemy Web UI for automated OpenAI account registration, proxy management, payment upgrades, and account lifecycle management. Uses Jinja2 server-side templates and vanilla JavaScript (no frontend framework).

## Build, Run, and Test Commands

```bash
# Install dependencies
uv sync                      # recommended
pip install -r requirements.txt

# Run locally
python webui.py                                    # default: 127.0.0.1:8000
python webui.py --debug                            # hot-reload, enables /api/docs
python webui.py --host 0.0.0.0 --port 8080
python webui.py --access-password mypassword

# Run tests
pytest                                    # all tests
pytest tests/test_duck_mail_service.py    # single test file

# Build standalone executables (PyInstaller)
build.bat                                 # Windows → dist/codex-register.exe
bash build.sh                             # Linux/macOS → dist/codex-register-*

# Docker
docker-compose up -d
```

## Architecture

### Entry Point and Application Wiring

`webui.py` is the CLI entry point. On startup it:
1. Loads `.env` (lower priority than existing env vars)
2. Initializes the database via `initialize_database()`
3. Loads all settings from the database via `get_settings()` (singleton)
4. Imports and serves `src.web.app:app` via uvicorn

`src/web/app.py` creates the FastAPI app, mounts `/static`, registers API routes under `/api`, sets up Jinja2 templates, and adds HMAC-based cookie authentication for the Web UI (login page, logout, password verification via `secrets.compare_digest`). Every page route checks `_is_authenticated()`.

### Settings System (Database-Backed)

Settings are **stored in the database** (not env files). The flow:
- `SETTING_DEFINITIONS` in `src/config/settings.py` maps attribute names → db keys + defaults
- `init_default_settings()` seeds defaults into the `settings` table on first run
- `get_settings()` returns a singleton `Settings` pydantic model loaded from DB
- `update_settings(**kwargs)` creates a new Settings instance AND persists to DB + backup file
- Secret fields (passwords, API keys) are wrapped in `pydantic.SecretStr`
- CLI args (`--host`, `--port`, etc.) override DB settings at runtime via `update_settings()`
- Dynamic proxy settings have a JSON file backup (`data/proxy_dynamic.json`) to survive DB restarts

### Email Service Plugin Architecture

`src/services/base.py` defines the abstract `BaseEmailService` with methods: `create_email()`, `get_verification_code()`, `list_emails()`, `delete_email()`, `check_health()`.

Concrete implementations are registered via `EmailServiceFactory.register(EmailServiceType, class)` in `src/services/__init__.py`. Available services:
- **TempmailService** (tempmail.lol API) — no config needed
- **OutlookService** — IMAP + XOAUTH2 with 3 providers: `IMAPOldProvider`, `IMAPNewProvider`, `GraphAPIProvider`. Provider priority is configurable, with health checking and auto-failover
- **TutaMailService** — Tuta mail with custom PQ crypto (uses `tuta_pq/` package and `src/services/tuta_crypto_core.py`)
- **MeoMailEmailService** (moe_mail) — custom domain REST API
- **TempMailService** — self-deployed Cloudflare Worker
- **DuckMailService** — DuckMail API compatible
- **FreemailService**, **ImapMailService** — generic IMAP

Email service priority is configurable via `email_service_priority` setting (dict mapping type → priority int).

### Registration Flow Engine

`src/core/register.py` `RegistrationEngine` is a template dispatcher. Based on `registration_flow_template` setting, it instantiates one of:
- `DefaultRegistrationEngine` (default)
- `Topic1848126RegistrationEngine`
- `Topic1840923RegistrationEngine`
- `Topic1849054RegistrationEngine`

Each engine lives in `src/core/registration_flows/` and implements a `run() -> RegistrationResult` method. The engines handle the full OAuth + signup flow, with modular components under `src/core/openai/` for oauth, token_refresh, mfa_verification, payment, and phone_verification.

### Concurrency Model

`src/web/task_manager.py` provides a global `TaskManager` singleton backed by a `ThreadPoolExecutor(max_workers=50)`. Key design:
- Task states and log queues use module-level `defaultdict` instances protected by a global `_meta_lock` for thread-safe first-key creation
- Logs are pushed to WebSocket connections via `asyncio.run_coroutine_threadsafe()` from worker threads
- Each WebSocket tracks a `sent_index` to avoid re-sending historical logs
- Task data is cleaned up with a 5-minute delay after completion to allow frontend reconnection
- Batch tasks (`init_batch`, `add_batch_log`, etc.) mirror the same pattern with `batch_` prefixed keys

### SMS Provider Architecture

`src/core/sms/` contains provider classes (HeroSMS, 5SIM, SMSBower) implementing a common base. The active provider is determined by `sms_provider` setting (normalized via `normalize_sms_provider_name()`). Each provider has its own API key field in settings. Provider failover: `sms_provider_failover_enabled` + `sms_provider_fail_threshold` control auto-switching after consecutive failures.

### Database

SQLAlchemy with SQLite (default) or PostgreSQL. Key models in `src/database/models.py`:
- `Account` — registered OpenAI accounts with tokens, subscription state, CPA upload tracking
- `EmailService` — saved email service configs
- `RegistrationTask` — task history
- `Setting` — key-value settings store
- `CpaService`, `Sub2ApiService`, `TeamManagerService` — upload target services
- `Proxy` — proxy list entries
- `PhoneVerificationAttempt` — SMS verification analytics
- `PhoneNumberReputation` — phone number blacklist/quality tracking

`src/database/session.py` provides `get_db()` context manager. `src/database/crud.py` contains CRUD helpers.

### HTTP Client

`src/core/http_client.py` — uses `curl_cffi` for browser fingerprint emulation. Supports proxy configuration from the global settings (dynamic proxy API, fixed proxy list, or direct).

### Upload Services

`src/core/upload/` contains `cpa_upload.py`, `sub2api_upload.py`, `team_manager_upload.py` — each handles exporting account data to external services. Uploads always bypass proxy. Management routes for these services are under `src/web/routes/upload/`.

### Frontend

Server-rendered Jinja2 templates in `templates/` (index.html, accounts.html, email_services.html, settings.html, payment.html, login.html). Vanilla JS in `static/js/` — each page has a corresponding JS file. Static asset versioning via `_build_static_asset_version()` (uses latest file mtime) to bust browser cache after deployment.

### PyInstaller Packaging

`codex_register.spec` defines the PyInstaller build. It bundles `templates/`, `static/`, and `src/` as data directories. Hidden imports cover uvicorn, fastapi, sqlalchemy, and all src modules. Tests and heavy libs (numpy, pandas, tkinter) are excluded. `webui.py` detects `sys.frozen` to find resources in `sys._MEIPASS`.

## Proxy Priority

When a task needs a proxy, the resolution order is:
1. Dynamic proxy (if `proxy_dynamic_enabled`) — API-based or account-based
2. Proxy list (random or default, tracked in `proxies` table)
3. Direct connection (no proxy)

CPA / Sub2API / Team Manager uploads always go direct.

## Key Conventions

- Python 3.10+, 4-space indentation, no formatter/linter configured — follow existing style
- `snake_case` for modules/functions/variables, `PascalCase` for classes
- Route handlers under `src/web/routes/`, service adapters under `src/services/`, business logic under `src/core/`
- Test filenames start with `test_`, placed in `tests/` or `tests_runtime/`
- `data/` and `logs/` are runtime output — never commit or modify as source
- Do not commit real credentials, tokens, proxy secrets, or exported account data
