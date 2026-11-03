# Crypto Signal Desk

A crypto market scanner that reads price action across multiple timeframes,
cross-checks it against the news cycle, and tells you what it sees — as a long
or short call with a specific entry, stop and targets, for scalping and for
swing trading, on both large caps and smaller coins.

It runs entirely on your machine. It ships with paper trading on and live
trading off.

---

## Read this part first

**This is an analysis tool, not a money machine.** A high score means a dozen
independent measurements of the market currently point the same way. That is
useful information. It is not a prediction, and it is not advice.

Some things that are true and worth internalising before you risk anything:

- Most retail derivatives traders lose money. That is the base rate you are
  trading against, and no indicator package changes it.
- Every number this tool produces is derived from the past. Markets change
  regime without warning, and the exact conditions that made a setup work last
  month can stop working tomorrow.
- Scalping is the hardest version of this. Fees and slippage are charged on
  every trade, they do not care whether you were right, and at 5-minute
  holding periods they are a large fraction of your expected edge.
- Paper results flatter you. There is no emotion, no missed fill, no funding
  rate, and no exchange outage in a simulation.

The honest way to use this: leave it in paper mode for weeks, watch which
setups actually work and which do not, and treat every signal as a starting
point for your own judgement rather than an instruction. I have no way to know
your finances or risk tolerance, and nothing here is financial advice.

---

## Install

```bash
cd crypto-signal-desk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py check          # confirms the exchange and news feeds are reachable
```

Python 3.10 or newer.

## Run

```bash
python run.py                # scan loop + live dashboard at http://127.0.0.1:8787
python run.py once --open    # one scan, writes dashboard.html, opens it
python run.py once           # one scan, prints a summary to the terminal
python run.py paper-reset    # wipe the paper portfolio
```

The dashboard rescans every 60 seconds by default and rerenders itself. Nothing
leaves your machine except read-only market-data and RSS requests.

---

## What it actually does

### 1. Picks what to scan

Every pair on your exchange is ranked by 24-hour volume. It takes the top 12 as
the **large-cap** bucket and 10 more from ranks 25–90 as the **small-cap**
bucket, then drops anything below a volume floor or above a spread ceiling.
Illiquid coins produce beautiful-looking signals you cannot actually fill.

Configure the bands in `config.yaml` under `universe`.

### 2. Reads three timeframes per signal

Each mode looks at three timeframes, and they do different jobs:

| | Entry | Confirm | Bias |
|---|---|---|---|
| **Scalp** | 5m | 15m | 1h |
| **Swing** | 4h | 1d | 1d |

The entry timeframe generates the score. The confirm timeframe adds a bonus for
agreeing and a haircut for disagreeing. The bias timeframe is a gate — trading
hard against it cuts the score roughly in half and raises a warning on the card.

### 3. Scores it

Thirteen or so indicator reads each cast a vote between −1 and +1, weighted and
combined into a single score from −100 to +100:

EMA stack · price vs the 200 EMA · MACD · RSI · Stochastic RSI · Supertrend ·
Bollinger %B · rolling VWAP · volume z-score · OBV · Money Flow Index · market
structure (higher highs / lower lows) · RSI divergence · candle close position

**The weights change with the regime.** The tool first classifies the market as
trending, ranging, compressed or mixed — using ADX and Bollinger width — and
then reweights. RSI at 30 means "buy the dip" in a range and "this is falling
apart" in a downtrend, and the scoring reflects that rather than pretending one
reading has one meaning.

Every vote and its reasoning is visible on the card under "Why".

### 4. Cross-checks the news

Eight public RSS feeds (CoinDesk, Cointelegraph, The Block, Decrypt and others)
are pulled every 15 minutes. Headlines are matched to coins by ticker and name,
scored with a transparent keyword lexicon, and time-decayed with an 8-hour
half-life so this morning's news outweighs yesterday's.

Two things come out of it:

- **A sentiment nudge**, capped at ±12 points so news can tilt a technical call
  but never manufacture one.
- **A hard veto.** A headline about a hack, exploit, delisting, insolvency or
  halted withdrawals on a coin blocks entries on it outright. Those are the
  situations where technical analysis is worthless.

There is also a market-wide mood reading and a macro flag for FOMC, CPI, ETF
and regulatory news, since those move everything at once.

No API keys needed. Set `news.enabled: false` to run on pure technicals.

### 5. Gives you levels

For every signal:

- **Entry** at the current price
- **Stop** at 1.2× ATR (scalp) or 1.6× ATR (swing), tucked behind the nearest
  confirmed swing level when one is close enough to be meaningful
- **Two targets** at fixed R multiples, with the first pulled in to real
  structure if resistance sits closer than the target did
- **Position size** that risks exactly your configured percentage, capped by a
  maximum notional
- **An invalidation condition** in words

Signals with less than 0.8R to the first target are downgraded to "watch" — the
setup may be real but you are not being paid enough to take it yet.

### 6. Trades it on paper

Every fired signal opens a virtual position. Half comes off at target 1 and the
stop moves to breakeven; the rest runs to target 2 or stops out; positions that
go nowhere time out. Taker fees (0.05%) and a slippage allowance (0.02%) are
charged on both sides.

You get equity, win rate, profit factor, average R, max drawdown, and the trade
log. State persists across restarts in `state/paper_state.json`.

A daily loss limit halts new entries after a 5% drawdown day.

---

## Live trading

Off by default, behind **five independent locks**. All five must be released:

1. `live.enabled: true` in `config.yaml`
2. `live.dry_run: false`
3. `export EXCHANGE_API_KEY=…` and `export EXCHANGE_API_SECRET=…`
4. `export LIVE_CONFIRM=I_UNDERSTAND_THE_RISK`
5. `live.testnet: false` — *only* for real money; leave it `true` and steps 1–4
   trade the exchange's testnet, which is where you should start

With any lock still engaged, orders are written to `state/orders.jsonl` and
never transmitted. The dashboard shows every lock's state, and says **ARMED** in
red when real orders can leave the machine.

Even armed, it refuses to exceed your max notional, max open positions, or to
trade at all once the daily loss limit is hit. It places a protective stop
immediately after entry and shouts in the log if the exchange rejects it.

Some honest caveats about live mode:

- **Use API keys with trading enabled and withdrawals disabled.** Always. Set an
  IP allowlist too.
- Spot markets cannot short. For short signals to be actionable you need
  `market_type: swap` (perpetuals), which brings leverage and liquidation risk.
- Bracket-order support varies by exchange. If the stop placement fails you will
  see it in the log, and the position is then yours to manage manually.
- This bot does not monitor positions tick-by-tick. It checks on each scan
  interval. That is fine for swing trading and marginal for 5-minute scalping.

---

## Configuration

Everything is in `config.yaml` and commented. The knobs you will actually touch:

```yaml
universe:
  exchange: binance        # any ccxt venue: bybit, okx, kraken, kucoin…
  market_type: spot        # "swap" for perpetuals (required for real shorts)
  large_cap_top: 12
  small_cap_count: 10

scalp:
  entry_tf: 5m             # 1m if you really want it; expect more noise
  min_score: 55            # raise for fewer, higher-conviction signals

risk:
  account_equity: 1000     # what sizes are calculated against
  risk_per_trade_pct: 1.0
  max_open_positions: 5
  daily_loss_limit_pct: 5.0

server:
  scan_interval_seconds: 60
```

Raising `min_score` is the single most effective adjustment. The defaults are
deliberately on the permissive side so you can see the machinery working; once
you have watched it for a while you will probably want 65–70 for scalps.

---

## Deploy free on Render

`render.yaml` describes the whole service, so deploying is mostly clicking.

1. **Make a free Postgres database** at [neon.tech](https://neon.tech) (or
   Supabase) and copy its connection string (`postgresql://…`). Sign-ups and
   feedback are stored there, because Render's free disk is wiped on restart.
2. In [Render](https://render.com): **New → Blueprint**, connect GitHub and
   give Render access to this repo, then pick it. Render reads `render.yaml`.
3. When asked for `DATABASE_URL`, paste the string from step 1. `SECRET_KEY`
   and `ADMIN_TOKEN` are generated for you.
4. Deploy. The site comes up at `https://crypto-signal-desk-xxxx.onrender.com`.
   Read feedback at `/admin/feedback?token=<ADMIN_TOKEN>` (the value is under
   the service's **Environment** tab).

Notes:

- The service runs in **Frankfurt**. Binance refuses API calls from US servers.
- **The free plan sleeps** after 15 minutes without visitors. The next visit
  wakes it in about a minute, and the first market scan takes another ~30s.
- **The public site never trades.** `BOT_LIVE_ENABLED=false` overrides
  `config.yaml`. Don't put exchange keys on a hosted service.
- **The practice account resets** on every restart or deploy.

### Environment variables

| Variable | What it does |
|---|---|
| `PORT`, `BOT_HOST` | Where the server listens (set by the host / `0.0.0.0`) |
| `DATABASE_URL` | Postgres for sign-ups and feedback; files under `state/` if unset |
| `SECRET_KEY` | Signs login cookies; keeps users signed in across restarts |
| `ADMIN_TOKEN` | Password for `/admin/feedback`; without it, only localhost gets in |
| `BOT_LIVE_ENABLED` | `false` forces live trading off, whatever `config.yaml` says |
| `BOT_OPEN_BROWSER` | `false` on servers |
| `BOT_STATE_DIR` | Where the paper state and order log are written |
| `EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET`, `LIVE_CONFIRM` | Local live trading only; put them in `.env` (git-ignored) |

---

## HTTP API

The server exposes JSON if you want to build on it:

| Endpoint | Returns |
|---|---|
| `GET /api/state` | the last scan, in full |
| `GET /api/scan` | forces a fresh scan, returns it |
| `GET /api/signals` | flat list of firing and watched signals |
| `GET /healthz` | liveness + scan count |
| `GET /export` | a frozen HTML snapshot |
| `POST /api/paper/reset` | wipe the paper portfolio |

---

## Layout

```
crypto-signal-desk/
├── run.py                 entry point
├── config.yaml            all settings
├── bot/
│   ├── indicators.py      pandas/numpy TA — no TA-Lib needed
│   ├── data.py            ccxt market data, universe ranking, caching
│   ├── strategy.py        regime detection, vote scoring, levels, sizing
│   ├── news.py            RSS ingestion, sentiment lexicon, coin matching
│   ├── paper.py           virtual portfolio, partial exits, statistics
│   ├── live.py            guarded live execution
│   ├── scanner.py         orchestration
│   ├── dashboard.py       the HTML page
│   └── server.py          Flask app + background scan loop
├── tests/test_offline.py  87 checks, no network required
└── state/                 paper portfolio + order log (created on first run)
```

## Tests

```bash
python tests/test_offline.py
```

Runs offline against synthetic candles. Verifies indicator maths against
independent reimplementations (EMA recurrence, RSI bounds and monotonic edge
cases, MACD identity, ATR bounds, no-lookahead on swing points), checks that
uptrends score long and downtrends score short, that opposing higher-timeframe
bias cuts scores, that critical news vetoes entries, and that the paper broker's
partial exits, breakeven stops, fees and kill switch all behave. Then it drives
a full scan through a fake exchange and renders the dashboard.

## Extending it

- **More indicators**: add a function to `indicators.py`, call it in `enrich()`,
  and append a `Vote` in `strategy.py:entry_votes()`. The weight belongs to one
  of the four regime buckets (`trend`, `revert`, `volume`, `structure`).
- **Different news sources**: add RSS URLs to `news.feeds`. Anything with a
  standard feed works. Extend `BULLISH`/`BEARISH` in `news.py` to tune scoring.
- **Alerts**: poll `/api/signals` from a small script and push to Telegram or
  Discord.
- **Backtesting**: the pieces are there — `indicators.enrich()` plus
  `strategy.build_signal()` on a rolling window, fed to `PaperBroker`. Walking
  a signal engine over history correctly (no lookahead, realistic fills) is its
  own project, and it is the thing most worth building next if you intend to
  trade this seriously.
