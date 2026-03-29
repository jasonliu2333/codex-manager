# Repository Guidelines

## Project Structure & Module Organization
`webui.py` is the local entrypoint for the FastAPI app. Core application code lives under `src/`: `src/web` contains app wiring, routes, and task management; `src/services` contains mail provider integrations; `src/core` handles registration, OAuth/payment flows, proxies, and upload adapters; `src/database` holds SQLAlchemy models and DB setup; `src/config` centralizes settings. Server-rendered pages live in `templates/`, browser assets in `static/`, and tests in `tests/`. Build and packaging files are kept at the repository root (`Dockerfile`, `docker-compose.yml`, `codex_register.spec`, `build.sh`, `build.bat`).

## Build, Test, and Development Commands
Install dependencies with `uv sync` or `pip install -r requirements.txt`. Run the app locally with `python webui.py`; use `python webui.py --debug` for reload-friendly development. Execute tests with `pytest`. Package desktop binaries with `build.bat` on Windows or `bash build.sh` on Linux/macOS. For containerized runs, use `docker-compose up -d`.

## Coding Style & Naming Conventions
Follow existing Python style: 4-space indentation, module-level docstrings where useful, `snake_case` for functions/modules, `PascalCase` for classes, and concise inline comments only when logic is not obvious. Keep route handlers in `src/web/routes`, provider-specific mail logic in `src/services`, and shared registration/payment logic in `src/core`. Frontend files use plain HTML/CSS/JS; keep template names lowercase and align page scripts with template purpose, such as `templates/accounts.html` and `static/js/accounts.js`.

## Testing Guidelines
This repository uses `pytest`. Add tests under `tests/` with filenames starting `test_` and descriptive function names like `test_static_asset_version_is_non_empty_string`. Prefer focused unit tests for route helpers, upload adapters, and mail services. Run `pytest` before opening a PR; when fixing a regression, add or update a test in the same change.

## Commit & Pull Request Guidelines
Git history is not available in this workspace, so no verified commit convention can be inferred from local logs. Use short, imperative commit subjects such as `fix payment route redirect` or `add duck mail service tests`. PRs should describe the behavior change, list impacted areas, mention config or migration implications, and include screenshots for template/static UI updates. Link related issues when applicable.

## Security & Configuration Tips
Start from `.env.example` when local overrides are needed, but note that runtime settings are primarily persisted through the app’s database-backed configuration layer in `src/config/settings.py`. Do not commit real credentials, tokens, proxy secrets, or exported account data.

## Local First Workflow
默认先修改本地目录 `D:\IDE\chatgpt注册\topic_1761463\codex-manager-master\codex-manager-master`。
只有在用户明确要求时，才将改动同步到 `D:\IDE\chatgpt注册\topic_1761463\codex-manager-master\codex-manager-github` 并执行 Git 提交、推送。
除非用户明确要求同步，否则不要默认修改 Git 目录。
