# finance - household ledger

[Firefly III](https://www.firefly-iii.org) on Postgres, kept up to date by a custom ingest
worker that reads bank alert e-mails and monthly e-statement PDFs.

```
bank alerts + e-statement PDFs -> forwarding mailbox (IMAP)
    -> finance-ingest, every 15 min -> Firefly III REST API -> Postgres
    -> report.* SQL functions -> Grafana "Finance" dashboard (read-only role)
```

| Service | Image | Port | Notes |
|---|---|---|---|
| `finance-db` | postgres:16-alpine | `127.0.0.1:5432` | loopback only - nothing else on the network needs it |
| `firefly` | fireflyiii/core | `3002` | LAN, and through the tunnel behind Cloudflare Access |
| `firefly-cron` | alpine:3 | - | calls Firefly's cron endpoint daily at 03:00 (recurring transactions, bills) |
| `finance-ingest` | built from `ingest/` | - | the worker; `docker logs finance-ingest` |

## The ingest worker

- Polls the mailbox every 15 minutes (`INGEST_INTERVAL=900`), parses alert e-mails and
  password-protected statement PDFs (`pdftotext -layout`), and posts transactions to
  Firefly through its API.
- Statements are the source of truth: they reconcile the accounts, and alerts fill in the
  days after the last statement.
- Categories come from the note on a payment and the payee's shop name, plus payee rules
  that are stated explicitly. A category set by hand is never overwritten and never
  copied to other payments.
- Every cycle it re-creates the `report` schema: SQL *functions* (not views, so Firefly
  migrations are never blocked) that the Grafana dashboard reads as a read-only role.

**The worker's code is private.** Its Python modules, rules and account map, the dashboard
generator (`grafana_dashboard.py`) and the dashboard it generates are full of personal
financial details. Only `ingest/Dockerfile` and `ingest/requirements.txt` are published;
everything else is in the encrypted `BUNDLE_FINANCE_*` secrets and comes back with
`tools/homelab_sync.py restore`.

## Config

| File | Holds |
|---|---|
| `db.env` | Postgres database, user and password |
| `firefly.env` | Firefly's `APP_KEY`, database connection, cron token, locale |
| `secrets/firefly.env` | API URL and the access token the worker uses |
| `secrets/mail.env` | mailbox login and the statement PDF passwords |
| `secrets/ask.env` | the *Ask my finances* login password and cookie signing key |

The `*.env.example` files list the keys; the values are in the bundle.

## Ask my finances

A LAN page on port `8085` (`finance/ask/`) that answers plain-language questions about
the ledger: "how much did I spend on food last month?", "what's left on each loan?".

- It is a small standard-library Python server on the host, not a container, because it
  runs an AI command-line tool that is logged in on the host (headless, tools disabled,
  system prompt replaced, nothing saved as a session) on a subscription rather than API
  keys. Cron starts it `@reboot` through `start.sh`; run `start.sh` again after editing it.
- A question takes two calls: one turns it into a single SQL `SELECT` over the `report.*`
  functions, the other turns the rows into a short answer. The SQL runs as `grafana_ro`
  inside a read-only transaction with a 15 s timeout, so it can read exactly what the
  Grafana dashboard reads and can change nothing.
- Every call's token counts go to `ask/state/usage.jsonl` and onto the page, next to what
  the same tokens would cost on an API model, to size a move from the subscription to API
  keys. A question costs about 4,000 tokens in and 350 out.
- One password (`ASK_PASSWORD` in `secrets/ask.env`, created on first run), a signed
  30-day cookie, and every POST needs the header `X-Ask: 1`. Not on the tunnel.
