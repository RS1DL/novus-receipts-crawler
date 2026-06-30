# novus-receipts — service overview

## Description

`novus-receipts` collects a single **Novus** loyalty account's purchase history
over the Novus mobile-app API and emits it as JSON for analysis. It is an
unofficial, personal-use tool, built by reverse-engineering the Android app; it
talks only to your own account using your own session token.

### What it does, end to end

1. **Login** (`python -m novus_receipts.login`) — runs the SMS OTP flow
   (`/auth/auth_token` → `/auth/check_user_by_phone` → `/auth/confirm_with_otp`)
   and saves `NOVUS_USER_TOKEN` / `NOVUS_REFRESH_TOKEN` to `.env`.
2. **Collect** (`python -m novus_receipts`) — paginates the receipt list, fetches
   each receipt's detail, reads the current bonus balance, and prints one JSON
   object to stdout.

### How it is built (layers)

| Layer | Responsibility |
|---|---|
| `config` | settings from env / `.env` (tokens, timeouts, retries, page size, timezone) |
| `errors` | typed exceptions (`NovusAuthError`, `NovusRateLimitError`, `NovusTransientError`, `NovusApiError`) |
| `api/client` | one thin method per endpoint: build request → classify response → parse into a DTO. No retries/pagination/logic |
| `dto/*` | Pydantic models mirroring the API's JSON 1:1 (raw shape, no transformation) |
| `crawler` | orchestration: pagination, stitch list→detail, retries + reactive token refresh, per-receipt error isolation, polite delay |
| `mapping` | the seam where raw DTOs become consumable output (ISO dates, numeric money) |
| `entrypoint` / `__main__` | wire it together; serialize result to stdout |

### Endpoints used

- **Auth:** `/auth/auth_token`, `/auth/check_number`, `/auth/check_user_by_phone`,
  `/auth/resend_otp`, `/auth/confirm_with_otp`, `/auth/refresh_token`
- **Receipts:** `GET /user/purchases_2` (list, paginated), `GET /user/purchase_2`
  (receipt detail), with `GET /v2/user/purchase` as the richer detail variant and
  `GET /user/purchases` / `GET /v2/user/purchases` available as alternates
- **Bonuses / profile:** `GET /user/bonuses/current`, `GET /user/bonuses`,
  `GET /user/bonuses_types`, `GET /user/profile`

### Key behaviours

- **Incremental** — `--from 7d` / `--from 2026-06-01T13:00:22` keeps only receipts
  at/after the cutoff and **stops paginating early** (the list is newest-first), so
  old pages and their detail calls are skipped. The API has no server-side date
  filter, so this is done client-side.
- **Page size** — `NOVUS_PAGE_SIZE` (default 100) receipts per request; the server
  default is 10, so a larger value means far fewer list requests.
- **Resilience** — transient/5xx and rate-limit errors are retried with backoff; a
  `401/403` triggers exactly one reactive token refresh (under a lock) then a
  retry; a failed *single* receipt detail is non-fatal (recorded in `errors`).
- **Privacy** — tokens live only in `.env`; output files are git-ignored.

## Returned format

`python -m novus_receipts` prints one JSON object. Dates are ISO-8601 in
`NOVUS_TIMEZONE` (default `Europe/Kyiv`); monetary fields are **numbers**;
`quantity` stays a string; identifier fields absent from the list are `null`.

```json
{
  "receipts": [
    {
      "summary": {
        "id": null,
        "cash_id": null,
        "shop_id": "7016",
        "amount": 232.45,
        "bonus": 2.32,
        "check_number": "1118.29-0",
        "date": "2026-06-17T13:00:22+03:00",
        "shop_address": "м. Київ, Львівська площа, 8Б",
        "work_station_id": null
      },
      "detail": {
        "id": null,
        "amount": 232.45,
        "date": "2026-06-17T13:00:22+03:00",
        "check_number": "1118.29-0",
        "shop_id": "7016",
        "shop_address": "м. Київ, Львівська площа, 8Б",
        "payment_method": "Готівка",
        "bonuses_accrued": 2.32,
        "bonuses_written_off": 0.0,
        "total_discount_saving": -193.71,
        "total_promotion_saving": 7.34,
        "coupons": [],
        "bonuses_details": [
          { "goods_title": "Картопля Апетитна, ваг", "amount": 0.74 }
        ],
        "discounts_details": [],
        "goods": [
          {
            "id": 47088,
            "title": "Картопля Апетитна, ваг",
            "quantity": "0.224",
            "price_type": "КГ",
            "amount": 73.70,
            "item_price": 0.0,
            "old_price": 12.28,
            "image": "https://api.novus.online/file/product_scaled_images/...",
            "rating": null,
            "discounts": []
          }
        ]
      }
    }
  ],
  "current_bonuses": { "data": 9.87, "data_long": 987 },
  "pages_fetched": 11,
  "total_count": 103,
  "errors": []
}
```

### Field reference

| Field | Type | Notes |
|---|---|---|
| `receipts[]` | array | one entry per collected receipt, newest first |
| `receipts[].summary` | object | the receipt as it appears in the list endpoint |
| `summary.shop_id` | string | store id (`store` for the detail call) |
| `summary.date` | string | ISO-8601 in `NOVUS_TIMEZONE` |
| `summary.amount`, `summary.bonus` | number | money (UAH) |
| `summary.check_number` | string | receipt number; the receipt's natural key with `date` |
| `summary.id` / `cash_id` / `work_station_id` | string/int \| null | usually `null` from `/user/purchases_2` |
| `receipts[].detail` | object \| null | full receipt; `null` if the detail couldn't be fetched (see `errors`) |
| `detail.goods[]` | array | line items: `title`, `quantity` (string), `price_type`, money fields, `discounts[]` |
| `detail.bonuses_details[]` / `discounts_details[]` | array | per-line bonus / discount breakdown |
| `detail.total_discount_saving` / `total_promotion_saving` | number | totals (may be negative as returned by the API) |
| `current_bonuses.data` | number | current bonus balance (UAH) |
| `current_bonuses.data_long` | int | the same balance in cents |
| `pages_fetched` | int | list pages actually requested |
| `total_count` | int \| null | total receipts the server reports for the account |
| `errors[]` | array | non-fatal per-receipt failures: `{ "check_number", "error" }` |

## Collecting over time (SQLite store)

The one-shot CLI prints JSON; for an ongoing history use the **collect job**,
which persists into a local SQLite database and builds a per-product price
time-series. It composes the same crawler — no analysis layer yet, just durable,
queryable collection.

```bash
uv run python -m novus_receipts.collect          # incremental: resume from last run
uv run python -m novus_receipts.collect --full   # ignore watermark, re-scan all
uv run python -m novus_receipts.collect --from 30d   # explicit cutoff
uv run python -m novus_receipts.collect --db /path/data.db   # override NOVUS_DB_PATH
```

Each run prints a one-line summary, e.g.
`collected 4 receipts, 24 line items, 12 products; 0 errors; from=…`.

### How it works

- **Incremental.** The next run resumes from the stored watermark (the newest
  receipt `date` already saved) minus a small overlap window (`NOVUS_COLLECT_OVERLAP_S`,
  default 2 days) so late / same-day receipts aren't missed. An empty database or
  `--full` collects the whole history.
- **Idempotent.** Every write is an upsert on a stable key, so re-running — or
  re-pulling the overlap window — never duplicates rows. One receipt is written in
  a single transaction (shop → header → products → items). The receipt id is a
  **deterministic UUID** (`uuid5` of `check_number|date|shop_id`), so it's stable
  across rebuilds.
- **Resilient.** A receipt whose detail can't be fetched is stored header-only
  (`detail_missing = 1`) and the failure is appended to a sidecar
  `<db>.errors.jsonl` log (operational data, kept out of the DB); a later run
  *within the overlap window* backfills the detail without clobbering anything
  already stored (a persistent failure older than the window would need a future
  explicit backfill pass — the `ix_receipts_detail_missing` index is reserved for it).
- **Money as integer cents.** All `*_cents` columns are minor units (kopiykas) to
  keep aggregation exact; `quantity` is kept verbatim plus a derived `quantity_num`.

### Schema (normalized)

No descriptive data is duplicated across tables — `address` lives only in
`shops`, `title`/`price_type` only in `products`.

| Table | What it holds |
|---|---|
| `shops` | one row per store (`shop_id` PK, `address`) — referenced by receipts, not copied |
| `receipts` | one row per receipt (header), **UUID id**, dedup key `(check_number, date, shop_id)`, `shop_id` → `shops` |
| `products` | the dynamic catalogue, keyed by the stable `GoodResponse.id`; the sole home of `title`/`price_type` |
| `receipt_items` | the **junction** — which products appeared in which receipts, one row per goods line = a price observation (`unit_price_cents` + `unit_price_source`); no copied descriptive columns |
| `meta` | bookkeeping (`schema_version`, `last_collect_ts`) |
| `v_receipt_contents` *(view)* | "what products were in which receipt" — the readable join |
| `v_price_series` *(view)* | a product's price over time — the entry point for trend analysis |

Crawl errors are **not** in the database — they go to the sidecar
`<db>.errors.jsonl`. `unit_price_cents` is the per-unit price: `item_price` when
the API gives a non-zero one (piece goods), otherwise `amount / quantity` (weighed
goods, where the API sends `item_price = 0`); `unit_price_source` records which
rule applied (`item_price` / `amount_per_qty` / `unknown`).

### Example queries

```sql
-- what products were in a given receipt
SELECT product_title, quantity, unit_price_cents / 100.0 AS unit_uah,
       amount_cents / 100.0 AS line_uah
FROM v_receipt_contents WHERE check_number = '1880.231-0';

-- a product's price-per-unit over time
SELECT ts_iso, unit_price_cents / 100.0 AS price, price_type
FROM v_price_series WHERE product_id = 1715 ORDER BY ts;

-- spend per receipt
SELECT date_iso, amount_cents / 100.0 AS uah FROM receipts ORDER BY date DESC;
```

### Scheduling

Run it periodically; WAL mode lets an ad-hoc query read while a collect writes.

cron (every 6h):

```cron
0 */6 * * *  cd /path/to/novus-receipts && uv run python -m novus_receipts.collect >> collect.log 2>&1
```

macOS launchd — `~/Library/LaunchAgents/online.novus.collect.plist` with
`ProgramArguments` running the command, `StartInterval` `21600`, a
`WorkingDirectory`, and `StandardOut/ErrorPath`; then
`launchctl load …/online.novus.collect.plist`.

> **Sync caveat.** The `.db` is git-ignored. Keep it out of any cloud-synced
> folder (iCloud/Dropbox) — a binary SQLite file plus a real-time sync daemon
> risks corruption. Run collection on one machine.

## Conclusion

The service turns a phone number + SMS code into a clean, analysis-ready JSON
export of a Novus account's receipts, line items, discounts and bonus balance.
Its design keeps the API/DTO layers a faithful raw mirror of the responses and
isolates every transformation (dates, money, future domain shaping) behind a
single mapping seam, while the crawler stays pure — it returns data, and *where*
to persist it (file, DB, pipeline) is the caller's choice. That makes it easy to
drop into automation: schedule it, feed the result into a database, and use
`--from <last run>` for incremental syncs.

It was validated end to end against the live API (full history of 103 receipts,
816 line items, zero errors). Known limitations come from the source being
reverse-engineered and partly undocumented: there is no server-side date filter
(incremental collection is client-side), the `date` value is treated as Unix
seconds, and some response fields are tolerated rather than strictly specified —
so the tool ignores unknown keys and degrades gracefully if the API changes.
