# novus-receipts

Service that collects a single Novus account's receipts, purchase details and
bonuses over HTTP, deserialises them into typed DTOs and returns them for
downstream analysis.

- Source of truth for the API: `../NOVUS_API.md`
- Design: `../PLAN.md`
- Task backlog (TDD): `../TASKS.md`

## Layout

`src/`-layout package `novus_receipts` with `config`, `errors`, `api/`, `dto/`,
`crawler/`, `mapping/` and `entrypoint`. Tests live under `tests/`.

## Development

```bash
uv sync                 # create venv + install deps (downloads Python 3.12 if needed)
uv run pytest           # run the test suite
uv run ruff check .     # lint
uv run mypy             # type-check
```

## Run

```bash
NOVUS_USER_TOKEN=... uv run python -m novus_receipts
```
