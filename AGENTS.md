# Repository Guidelines

## Project Structure & Module Organization

`app/collector.py` imports OpenClaw records into SQLite; `app/server.py` serves authenticated, read-only APIs. `app/store.py` owns the schema. Static HTML, CSS, and JavaScript live in `app/static/`; tests live in `tests/`; systemd units and the SSH access script live in `deploy/`. See `README.md` for deployment and data semantics.

## Build, Test, and Development Commands

Use Python 3.12 and its standard library. No dependency installation or frontend build is required.

Useful repository checks include:

- `git status --short`: inspect pending changes.
- `git diff --check`: detect whitespace errors in tracked changes.
- `git diff`: review the patch before committing.
- `python3 -m unittest discover -s tests -v`: run tests with temporary sources and databases.
- `python3 -m compileall -q app`: check Python syntax.
- `node --check app/static/app.js`: check JavaScript syntax when Node.js is available.

Configure source paths and the database, run the collector once, then start `python3 -m app.server` with a password hash. Refer to `README.md` for environment variables.

## Coding Style & Naming Conventions

Use four-space Python indentation, snake_case functions, and descriptive names. Follow existing vanilla JavaScript conventions. No formatter or linter is configured. Escape source text before inserting HTML and keep formatting changes focused.

## Testing Guidelines

Use `unittest` and `test_*.py` filenames. Cover ingestion, identity, authentication, and read-only boundaries when changing them. No coverage threshold is configured. Never commit real conversations or credentials as fixtures.

## Commit & Pull Request Guidelines

Use concise, imperative subjects, following `Add repository contributor guidelines`, and keep commits focused.

Pull requests should explain the change, its purpose, and validation performed. Link related issues when available, include screenshots for visible interface changes, and document any new setup requirements. Never commit credentials, tokens, or private configuration.

## Data & Deployment Boundaries

Treat OpenClaw as read-only. Do not change WeChat, model settings, or the separate 8787 service. Keep credentials, databases, transcripts, and screenshots containing personal data outside Git. Distinguish quote universes, recommendations, and confirmed trades. Web chat remains out of scope for this phase.
