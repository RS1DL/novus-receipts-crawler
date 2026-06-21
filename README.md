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

First obtain a session token via the interactive OTP login (NOVUS_API.md §2).
It prompts for your phone number and the SMS code, then writes
`NOVUS_USER_TOKEN` / `NOVUS_REFRESH_TOKEN` into `.env` (preserving your other
lines; tokens are masked in the console output):

```bash
uv run python -m novus_receipts.login
```

Then collect the data (reads the tokens from `.env`):

```bash
uv run python -m novus_receipts > receipts.json
```

### Incremental collection (`--from`)

The Novus API has no server-side date filter, but the list is ordered
newest-first, so `--from` lets the crawler **stop early** — only the receipts you
want are fetched (no old pages, no old detail calls). Accepts a duration before
now (`7d`, `2w`, `24h`) or an ISO date/datetime:

```bash
uv run python -m novus_receipts --from 7d > last_week.json      # last 7 days
uv run python -m novus_receipts --from 2026-06-01 > june.json   # since a date
```

Dates are rendered in `NOVUS_TIMEZONE` (default `Europe/Kyiv`); money fields come
out as numbers. `NOVUS_USER_TOKEN` can also be supplied directly via the
environment instead of the login step if you already have a token.
