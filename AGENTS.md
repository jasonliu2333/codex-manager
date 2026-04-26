# Repository Guidelines

## Project Structure & Module Organization
`webui.py` is the local FastAPI entrypoint. Application code lives in `src/`: `src/web` contains app setup, routes, and task management; `src/core` holds registration, proxy, OAuth, and payment logic; `src/services` contains mailbox provider integrations; `src/database` defines SQLAlchemy models and sessions; `src/config` stores shared settings and constants. Server-rendered views are in `templates/`, browser assets in `static/`, integration notes and helper scripts in `docs_integration/`, and automated tests in `tests/` and `tests_runtime/`. Treat `data/` and `logs/` as runtime output, not source.

## Build, Test, and Development Commands
Install dependencies with `uv sync` or `pip install -r requirements.txt`. Run locally with `python webui.py`; use `python webui.py --debug` for reload-friendly development. Common variants include `python webui.py --host 0.0.0.0 --port 8080` and `python webui.py --access-password mypassword`. Run the test suite with `pytest`. Build packaged binaries with `build.bat` on Windows or `bash build.sh` on Linux/macOS. Start the containerized stack with `docker-compose up -d`.

## Coding Style & Naming Conventions
Use Python 3.10+ and 4-space indentation. Follow existing style closely because no formatter or linter is configured. Use `snake_case` for modules, functions, and variables, and `PascalCase` for classes. Keep route handlers under `src/web/routes`, provider-specific logic under `src/services`, and reusable business logic under `src/core`. Match template and asset names by feature, for example `templates/accounts.html` with `static/js/accounts.js`.

## Testing Guidelines
This repository uses `pytest`. Add tests under `tests/` with filenames starting with `test_` and descriptive names such as `test_payment_redirect_requires_account_id`. Prefer focused unit tests for routes, service adapters, and registration helpers. When fixing a bug, add or update a regression test in the same change, then run `pytest` before submitting.

## Commit & Pull Request Guidelines
Git history is not available in this workspace, so no verified commit convention can be inferred locally. Use short, imperative commit messages such as `fix websocket reconnect logic` or `add duck mail service tests`. Pull requests should summarize the behavior change, list affected areas, note config or migration impact, and include screenshots for changes in `templates/` or `static/`.

## Security & Configuration Tips
Do not commit real credentials, tokens, proxy secrets, or exported account data from `data/`. Use `.env.example` only as a starting point for local overrides; most runtime configuration is managed through the app and database. Keep changes in this workspace unless explicitly asked to sync elsewhere.
