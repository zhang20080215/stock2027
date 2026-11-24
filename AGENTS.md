# Repository Guidelines

## Project Structure & Module Organization

This repository is currently a minimal scaffold. `README.md` contains the project title, `stock2027`; no application source, tests, assets, or dependency manifests have been committed. This guide lives in the repository root as `AGENTS.md`.

When adding the first implementation, choose a structure appropriate to the selected stack and document it in `README.md`. Keep application code, tests, and static assets in clearly named directories, such as `src/`, `tests/`, and `assets/`, where appropriate. Avoid introducing empty directories or tooling without a concrete use.

## Build, Test, and Development Commands

There are currently no build, test, or local development commands. Do not assume that commands such as `npm test` or `make build` are available.

Useful repository checks include:

- `git status --short`: inspect pending changes.
- `git diff --check`: detect whitespace errors in tracked changes.
- `git diff`: review the patch before committing.

When introducing a toolchain, add reproducible installation, development, build, and test instructions to `README.md`, alongside the required runtime versions.

## Coding Style & Naming Conventions

No language-specific style, formatter, or linter is configured. Follow the conventions of the stack introduced by the implementation, keep indentation consistent within each file, and commit any shared formatting configuration. Use descriptive names and avoid unrelated formatting changes. For Markdown, use descriptive headings, fenced code blocks for commands, and repository-relative links.

## Testing Guidelines

No testing framework or coverage threshold exists yet. New executable functionality should include appropriate tests and a documented command to run them. Use descriptive test names that explain the behavior under test; adopt the selected framework’s filename conventions. Document any checks that could not be run and why.

## Commit & Pull Request Guidelines

Git history currently contains only `Initial commit`, so no established commit convention can be inferred. Use concise, imperative subjects, such as `Add data import validation`, and keep commits focused.

Pull requests should explain the change, its purpose, and validation performed. Link related issues when available, include screenshots for visible interface changes, and document any new setup requirements. Never commit credentials, tokens, or private configuration.
