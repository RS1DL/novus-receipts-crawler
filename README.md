# novus-receipts

Collects your **Novus** loyalty account's receipts, purchase details and bonus
balance over HTTP and writes them to JSON for analysis.

## Requirements

[`uv`](https://docs.astral.sh/uv/) — it fetches Python 3.12 and the dependencies
on first run. Nothing else to install.

## Use

```bash
uv sync                                   # one-time: create the environment

uv run python -m novus_receipts.login     # 1. log in once (OTP by SMS)
uv run python -m novus_receipts > receipts.json   # 2. collect -> JSON on stdout
```

`login` asks for your phone (`+380…`) and the SMS code, then saves
`NOVUS_USER_TOKEN` / `NOVUS_REFRESH_TOKEN` to `.env`. Collection reads them from
there; the refresh token keeps the session alive, so you rarely log in again.

### Options

| Flag / env var | Effect |
|---|---|
| `--from 7d` · `--from 2026-06-01T13:00:22` | only receipts since then; stops paging early |
| `NOVUS_TIMEZONE` (default `Europe/Kyiv`) | timezone for rendered dates |
| `NOVUS_PAGE_SIZE` (default `100`) | receipts fetched per request |

```bash
uv run python -m novus_receipts --from 7d > last_week.json
```

## Output

A single JSON object: `receipts` (each with a `summary` and full `detail` —
goods, prices, discounts, bonuses), `current_bonuses`, `pages_fetched`,
`total_count`, `errors`. Dates are ISO-8601 in your timezone and money is
numeric. `.env` and `*.json` are gitignored, so tokens and your data stay local.

## Develop

```bash
uv run pytest        # tests
uv run ruff check .  # lint
uv run mypy          # types
```

For your own Novus account only.
