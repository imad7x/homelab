# ipodesk - a paper trading account for recent IPO listings, run by an AI

A small web app in plain Python (standard library only) with a SQLite database. It
holds a pretend account that started with Rs 1,00,000 and may only deal in two things:
companies that listed on NSE through a mainboard IPO in the last two years (about 185),
and mainboard IPOs that are open for bids. An AI on the host makes the calls from news,
fundamentals and technicals, and every result feeds back into the next decision.

Web UI: `http://192.168.0.10:8091` on the LAN only (no tunnel route, no login of its own).
No broker account is involved: prices come from public sources and fills are simulated.

## Where the data comes from

- **NSE's public website** (the same JSON its pages use, with a quote page visited first
  for cookies, requests spaced 0.45 s apart): the IPO lists (current, forthcoming, past),
  per-issue details (lot size, price band, subscription by QIB / NII / retail, RHP link),
  live quotes, the minute chart, daily history per stock (backfill), the daily
  `sec_bhavdata_full` file (every stock's day with delivery %), index history (NIFTY 50,
  NIFTY IPO, NIFTY SMALLCAP 250), live index levels and GIFT Nifty, the trading holiday
  list, corporate filings and board meetings.
- **Headlines**: Bing News RSS per company (holdings, watchlist, live issues, fresh
  listings), plus the Economic Times IPO feed and Mint's markets feed.
- **Fundamentals**: Screener's public company page (ratios, last five quarters, growth,
  pros and cons), for the same names plus a few screen candidates, at most weekly.

## The paper account

- Long only, delivery, cash only. One stock may not be more than 50% of the account
  when bought. The mandate is maximum profit: concentrated bets are allowed, with a
  stop-loss on every position.
- Every buy must carry a stop or a trailing stop (otherwise it is rejected). Stops can
  only be raised and trails only tightened, so protection ratchets up along the way;
  a position left without either (e.g. fresh IPO shares) gets a 12% trailing stop.
- Market orders fill at the last price plus or minus 0.05%; orders placed while the
  market is shut fill at the next opening price (10:00 listing price on a listing day).
  Limit orders fill when the minute chart crosses the limit and last 1-10 trading days.
- Stop, target and trailing stop on a position are checked every minute against the
  minute chart, like a GTT, and sell the whole position.
- Charges are Zerodha's delivery charges: STT 0.1% both sides, stamp duty 0.015% on
  buys, NSE transaction 0.00297%, SEBI Rs 10/crore, 18% GST on the fees, Rs 15.34 DP
  charge per stock on a sell day.
- IPO bids are retail bids at the cut-off price, whole lots, up to Rs 2 lakh. The money
  is blocked until the allotment day (trading day after the close, 18:00). Allotment is
  drawn like the real lottery from the final retail subscription: under 1x everything
  is allotted; above that, a proportional share when it comes to a lot or more, else one
  lot with odds of about 1.5 / retail times. The draw uses a fixed seed per issue.
  Allotted shares are credited at the issue price on listing morning.

## The decisions

The container never talks to a model. `ai/worker.py` on the host long-polls
`/api/ai/next` (key in `state/worker.key`), runs the job through the AI command-line
tool that is logged in on the host (on the subscription, headless, system prompt
replaced, no session saved, answer forced into the job's JSON schema) and posts the
answer back. Decision runs may use web search and web fetch; nothing else is allowed.
Models are set in `ai/config.json`: `default` (Sonnet) for decision runs, `light`
(Haiku) for post-mortems, `deep` (Sonnet; Opus if wanted) for the weekly review;
`max_budget_usd` caps one call as a runaway guard. Token use is kept low: the data pack
is about 15k characters (the universe is trimmed to holdings, watchlist, leaders,
movers and lock-in events plus a one-line summary of the rest), web search is capped at
3 per run and only allowed in the pre-market, listing and on-request runs. Cron starts the worker `@reboot`; after an
edit run `ai/start.sh`. Usage per call goes to `ai/state/usage.jsonl`.

| Run | When | What it sees and does |
|---|---|---|
| Pre-market | 08:50 on trading days | the data pack below (web search allowed); places orders, IPO bids and skips, exits, watchlist |
| Listing day | 10:20 when a stock it holds, bid for or watches lists | same |
| Afternoon | 14:30 on trading days, only with open positions or orders, an IPO closing that day or a 4%+ watchlist move | same, no web search |
| On request | the page's "Run now" | same |
| Post-mortem | after every fully sold position | the trade, its thesis, price path, headlines; writes a verdict (decision vs outcome), mistakes, one lesson |
| Weekly review | Saturday 10:00 | the week's runs and decisions, closed trades with 5/20-day after-exit moves, post-mortems, IPO calls vs listing gains, record by signal and confidence, the benchmarks and a bid-every-IPO baseline; rewrites the playbook (max 12 rules, each with evidence) |

The data pack for a decision: time and market phase, indices with 1-week and 1-month
moves, GIFT Nifty, the account and positions (with thesis, stop, target, horizon left),
open orders and bids, the watchlist with its notes, the playbook, the last eight
lessons, the track record, its own notes and rejected actions from the last run, open
and coming issues with subscription, listings of the last five weeks, every tracked
company in one line (vs issue, 1d / 5d / 20d, from high, RSI, vs SMA, volume ratio,
delivery %, market cap, lock-in ends), fundamentals, headlines and filings, and coming
board meetings. Every action is stored as a decision with its thesis (the evidence and what would
prove it wrong), signals and a 1-5 confidence; the app validates each one and records
why it was rejected. The Trades tab is the diary: daily P&L after each close, open
trades with why they were bought and every stop change, and closed trades with why,
how they ended, what happened and the lesson.

A queued decision run waits for fresh news, quotes and fundamentals (about two minutes)
and expires after 40 minutes if the worker is offline, so it never acts on stale data.
"Pause" stops new runs; the minute checks (fills, stops, targets) keep working.

## Schedule inside the container

Issue lists every 30 minutes on trading days (3 hours otherwise); news every 2 hours;
fundamentals and watchlist expiry daily; quotes for holdings and the watchlist every
minute in market hours and the whole universe every 15 minutes; day orders expire after
15:31; the bhavcopy, the daily snapshot and the after-exit moves from 18:10; allotments
from 18:00; allotted shares credited from 08:30 on listing day.

## Files

- `app/` - `server.py` (HTTP, scheduler, minute loop), `nse.py`, `sources.py`, `market.py`
  (calendar, universe, indicators), `broker.py` (paper account), `brain.py` (runs, data
  pack, schemas, applying answers, learning record), `store.py`, `web/`.
- `ai/` - `worker.py`, `start.sh`, `config.json` (host side, not in the container).
- `state/ipodesk.db` - everything. Stack-trace dump of a stuck container:
  `docker kill -s USR1 ipodesk` and read `docker logs ipodesk`.

Testing: `JOBS=off DB_PATH=<scratch copy> PORT=18091 python3 -B server.py` from `app/`
serves the page without the scheduler; point a worker at it with `DESK_URL` and
`DESK_KEY_FILE` and `--once`.
