# ipodesk - a paper trading account for recent IPO listings, run by an AI

A small web app in plain Python (standard library only) with a SQLite database. It
holds a pretend account that started with Rs 1,00,000 and may only deal in two things:
companies that listed on NSE through a mainboard IPO in the last two years (about 185),
and mainboard IPOs that are open for bids. An AI on the host makes the calls from live
technicals, news and fundamentals, and every result feeds back into the next decision.

Web UI: `http://192.168.0.10:8091` on the LAN only (no tunnel route, no login of its own).
No orders ever go to a broker: prices are live, fills are simulated.

## Where the data comes from

- **Upstox**, with the account's read-only *Analytics Token* (generated on the Upstox
  Developer Apps page, Analytics tab; valid a year; it cannot place orders). It lives in
  `secrets/upstox.env` (`UPSTOX_TOKEN=...`, mode 600), mounted read-only at `/secrets`
  and re-read whenever the file changes, so a new token needs no restart. It never goes
  into the database, the logs, the AI's prompts or any API answer.
  - **Market data feed** (WebSocket, V3, protobuf): on trading days from 08:58 to 15:40,
    every tracked company plus NIFTY 50 and India VIX in `full` mode: each trade, the
    5-level order book, total buy/sell quantity, exchange VWAP, volume, day OHLC,
    1-minute bars and the auction's indicative price in the pre-open. Standard-library
    WebSocket client (`wsock.py`) and protobuf decoder (`pb.py`, checked field by field
    against Upstox's `MarketDataFeed.proto` in the tests).
  - REST: full quotes (circuit limits, 52-week range), daily and minute candles (history
    backfill, end of day, gaps after a reconnect), IPO status with ISIN and expected
    listing date, headlines, market timings (special sessions).
- **NSE's public website**: category-wise IPO subscription (QIB / NII / retail), the daily
  `sec_bhavdata_full` file (official close and delivery %, from 18:30), index history and
  levels (NIFTY IPO), the holiday list, filings and board meetings; and the fallback
  quotes / minute chart whenever Upstox is unavailable.
- **Headlines** also from Bing News RSS, the Economic Times IPO feed and Mint markets.
- **Fundamentals**: Screener's public company page, at most weekly.

History is adjusted for splits and bonuses (a one-day fall below 0.75x is one, since a
day's move is capped at 20%).

## The paper account

- Long only, delivery, cash only. One stock may not be more than 50% of the account
  when bought. The mandate is maximum profit, with a stop-loss on every position.
- Every buy must carry a stop or a trailing stop. Stops only rise and trails only tighten;
  a position left without either (e.g. fresh IPO shares) gets a 12% trailing stop.
- **Fills follow the live book.** A market order walks the 5-level order book (buys pay
  the asks, sells hit the bids) and never fills beyond the day's circuit limits; with
  nobody on the other side (a circuit lock, or a thin stock between trades) it waits.
  Orders entered before the pre-open order entry closes (09:08; 09:45 on a listing day)
  fill at the discovered opening price, later ones at the book when they arrive.
- Limit orders fill only when a trade goes through the limit (a limit already crossed
  by the book fills at the book). **Trigger orders** are buy-stops for breakouts: they buy
  when a trade reaches the trigger.
- Stops, targets and trailing stops are checked against every trade (every 2 seconds on
  the feed; every minute on the NSE fallback). A gap through a stop fills at the bids of
  that moment; after a feed outage the missing minutes are replayed from 1-minute bars,
  low before high, so a stop counts as hit first.
- Charges are Zerodha's delivery charges: STT 0.1% both sides, stamp duty 0.015% on
  buys, NSE transaction 0.00297%, SEBI Rs 10/crore, 18% GST on the fees, Rs 15.34 DP
  charge per stock on a sell day.
- IPO bids are retail bids at the cut-off price, whole lots, up to Rs 2 lakh, blocked
  until allotment and drawn like the real lottery from the final retail subscription.
  Allotted shares are credited at the issue price on listing morning.

## Technicals and the signal engine

`tech.py` builds one snapshot per stock: today's VWAP, opening range (09:15-09:30, or
10:00-10:15 on a listing day), relative volume against the usual intraday volume curve,
5-minute RSI / MACD / EMA 9-21 trend and ATR, order-book balance, spread; and the daily
structure since listing: EMA 20/50, RSI, ATR, high since listing, listing-day high and
low, anchored VWAP from the listing day, the recent base (depth, tightness, volume
dry-up), 20-day strength vs NIFTY IPO, circuit band, and the price levels nearest to
the price.

`signals.py` looks at every name once a minute in market hours and decides when the AI
is worth waking. Triggers fire when a condition starts, once per symbol and kind a day:
opening-range breakouts on volume above VWAP, new highs since listing on 2x volume, a
holding losing VWAP and its opening range (or 1.5 daily ATRs off the high), gaps of 4%+,
big moves (min(5%, 0.6 x band)), circuits, the listing day's 10:00-10:15 range, and the
AI's own **price alerts**. Triggers are batched into one run; at most 7 signal runs a
day (2 for holdings and listings, 3 for alerts, 1 watchlist, 1 universe scan), at least
20 minutes apart (5 for holdings), one wake-up per symbol per 90 minutes. Every trigger
is logged with what became of it and shown on the Overview's Live card.

## The decisions

The container never talks to a model. `ai/worker.py` on the host long-polls
`/api/ai/next` (key in `state/worker.key`), runs the job through the AI command-line
tool logged in on the host (subscription, headless, through the lab AI gateway) and
posts the answer back. Models are set in `ai/config.json`: `default` (Sonnet) for
decisions, `light` (Haiku) for post-mortems, `deep` (Sonnet) for the weekly review.

| Run | When | What it sees and does |
|---|---|---|
| Opening | 09:01 on trading days, in the pre-open | the full data pack with indicative opens; web search (max 3) and market tools; plans the day: orders, alerts, trigger orders |
| Listing | 09:25 on a day a stock it holds, bids for or watches lists | same, with the listing auction's indicative price |
| Signal | when the signal engine fires | a short pack: what fired, live technicals, account, alerts, playbook; market tools (max 4 calls), no web |
| Closing | 15:00, only with positions, orders or alerts | what to carry overnight; market tools |
| On request | the page's "Run now" | the full pack |
| Post-mortem | after every fully sold position | verdict (decision vs outcome), mistakes, one lesson |
| Weekly review | Saturday 10:00 | rewrites the playbook (max 12 rules, each with evidence) |

**Market tools** are the read-only `ipodesk-market` MCP server
(`/docker/lab-ai/mcp/ipodesk_market.py`, host, stdio): `quote`, `candles`, `technicals`,
`scan`, `ipo_book`, `market`, `news`. It only calls this app's `/api/md/*` endpoints with
the market-data key in `state/md.key`, which works on nothing else. The AI gateway lets a
run load it only through one allow-listed, hash-pinned config file.

The **technical playbook** is the `ipo-technicals` skill in `app/skills/ipo-technicals/`
(linked into the host's skills folder): the full text goes into the opening, listing and
on-request runs, its core rules into signal and closing runs.

Every action is stored as a decision with its thesis, signals and a 1-5 confidence; the
app validates each one and records why it was rejected. A signal run that can't start
within 15 minutes (or is held because the AI plan is at its limit) is dropped quietly,
since the setup has gone stale; other runs are retried once after the limit resets, and
the phone gets an alert only if one misses its deadline.

## Schedule inside the container

| When | What |
|---|---|
| 06:30 | market timings for the coming days (special sessions) |
| 07:15 | fundamentals, watchlist and alert expiry, missing history |
| 08:30 | allotted shares credited on listing day |
| 08:58-15:40 | live feed; stops and orders every 2 s; signals every minute; quotes table every 5 s |
| 09:16 | today's circuit limits (and 10:01 on listing days) |
| every 15 min in session | Upstox quotes for anything the feed doesn't cover (everything when it is down) |
| IPO lists | NSE hourly in the day; Upstox every 15 min while an issue is open |
| 15:31 / 15:50 | day orders expire / daily rows and snapshot from Upstox, volume curve, pruning |
| 18:00 / 18:30 | allotments / bhavcopy: official close and delivery %, snapshot again |

## Files

- `app/` - `server.py` (HTTP, scheduler, trader and signal loops, `/api/md`), `feed.py`,
  `wsock.py`, `pb.py`, `upstox.py`, `ta.py` (indicators), `tech.py` (snapshots),
  `signals.py`, `nse.py`, `sources.py`, `market.py`, `broker.py`, `brain.py`, `store.py`,
  `skills/`, `web/`.
- `ai/` - `worker.py`, `start.sh`, `config.json` (host side, not in the container).
- `secrets/upstox.env` - the token (not synced, not in the image).
- `state/ipodesk.db` - everything. Stack dump of a stuck container:
  `docker kill -s USR1 ipodesk` and read `docker logs ipodesk`.
- `tests/` - `test_live.py` (fills, auctions, triggers, signals, packs), `test_feed_wire.py`
  (protobuf, WebSocket), `test_feed_loop.py` (connect, subscribe, reconnect). Run each with
  `python3 -B <file>`.

Testing a copy: `JOBS=off DB_PATH=<scratch copy> PORT=18091 python3 -B server.py` from
`app/` serves the page and `/api/md` without the scheduler or the feed (`FEED=off` keeps
the jobs but skips the feed).
