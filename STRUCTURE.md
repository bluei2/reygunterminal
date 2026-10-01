# REYGUN SuperChart: full structure (for code review)

A Python + web trading terminal for **XAUUSD on MetaTrader 5**, built by Eugene (REYGUN).

- **Master and followers:** It reads a master MT5 account (LiteFinance, symbol `XAUUSD_ph`) and copies trades to
  follower MT5 accounts.
- **Scoring:** A weighted, rule-based multi-timeframe analyser score drives the entries. There is no AI or ML.
- **Extras:** Signals, backtesting and optimisation, and alerts.
- **Where it runs:** A Windows PC or VPS next to the MT5 terminals. The UI is a local web page at
  `http://127.0.0.1:8765` (localhost only).

See `IMPROVEMENT_REPORT.md` for the master-improvement changes (A-J).

Status: feature-complete for **testing**. It has run on demo data and paper trading only, not yet on a real MT5
account.

---

## 1. What to review (suggested questions)

1. **Look-ahead bias:**
   - Do `Hub.read_at`, `backtest.py`, `analyser_signals`, `reversal.py`, `trendline.py`, `drift.py` and
     `exitguard.py` use only bars that had closed at that time?
   - Confirmation logic (reversal R✓, trend line TL✓, TREND CHANGE) uses later bars on purpose. Check that entries
     are placed on the confirmation bar, not the signal bar.
2. **Order safety:** Can anything send an order without ARM + (click + confirm) or AUTO TRADE ON? Check the rules
   in section 6.
3. **MT5 Python API usage:** Check `mt5_ops.py`: `order_send` requests, filling modes, `order_check`,
   positions/orders by magic, and `copy_rates_from_pos`. Also check that there is one MT5 connection per process
   (followers run in separate processes).
4. **Thread safety:** There is one `Hub.lock` (an RLock) for every MT5 and plugin call, and `TradeManager.lock` for
   groups. Many background threads run (section 5). Look for deadlocks and races.
5. **Copier sync:** `TradeManager.sync` mirrors master close, partial close and SL/TP changes to followers. Check
   the stale-read guards (`fresh`, `orders_read_at`) and the edge cases.
6. **Risk logic:**
   - daily DD/TP lock
   - max open, no opposite trade, re-entry after 30 minutes with a fresh confirmation
   - exit guard
   - drift monitor auto-pause
7. **Backtest validity:**
   - spread cost
   - one trade at a time
   - the 70/30 in-sample/out-of-sample split and the "robust" rule
   - the objectives
8. **Code quality and structure:** duplicated logic, error handling, and performance with 45 plugins.

---

## 2. Run modes (`superchart.py` + .bat files)

| Script | Mode |
|---|---|
| `run_superchart_live.bat` | live MT5 data + LIVE orders (still needs ARM) |
| `run_superchart_paper.bat` | live MT5 data + **paper** (simulated) orders |
| `run_superchart_demo.bat` | synthetic data (`DemoSource`) + paper orders, time can run faster (x1 to x500) |
| `run_superchart_readonly.bat` | no trade panel at all (`--no-trading`) |
| `run_replay.bat` / `run_demo.bat` / `run_live.bat` | older terminal-only UI (`reygun_term.py`, Textual) |

Flags: `--mode live|demo|replay --paper --no-trading --no-news --port --speed --record --utc-offset --mt5-path`

---

## 3. File tree (lines of code)

```
reygun_terminal/
├── superchart.py      1369  HTTP server (ThreadingHTTPServer), Hub (data + analyser + plugins), REST API, main()
├── engine.py           418  Bar/Tick/TFState/Read types, SMC structure (BOS/CHoCH/sweep), market_read() scoring,
│                            GROUP_ORDER + default weights
├── sources.py          354  MT5Source (live ticks + copy_rates), DemoSource (synthetic), ReplaySource (CSV), Recorder
├── plugin_api.py       293  Indicator/Strategy base classes, Overlay (lines, levels, markers, zones, hists,
│                            profiles, signals), Vote, Context (incl. history=read_at)
├── plugin_loader.py    205  (+ plugin safety contract: forbidden imports)  loads plugins/*.py, per-plugin chart/score/params/vote_tf state (plugin_settings.json)
├── trading.py         1329  TradeManager: master + followers, PaperBackend, open (request_id dedupe, lifecycle),
│                            close/modify/BE, copier sync (freshness, closing retries), protective SL watchdog,
│                            MFE/MAE, exit classes, per-account DD/TP lock (own server day), STOP NEW / EMERGENCY
├── account_worker.py    93  one follower = one process with its own MT5 connection (RemoteMT5, queues)
├── mt5_ops.py          404  every MT5 call: account, positions, open_market, close, modify, pending orders,
│                            connection_check (order_check), find_symbols (prefix/suffix), MAGIC 20260925
├── mt5_detect.py        92  finds the OPEN MT5 terminals (account, symbol) for the Accounts page + start-up pick
├── runstate.py          95  keep-alive: runtime_state.json, --resume after a crash, crash_log.txt
├── journal.py          209  trade journal (jsonl): score at entry, news, kind, regime, lifecycle, setup fingerprint,
│                            exit_reason, MFE/MAE, failed orders (not in stats), snapshots, stats, series, csv
├── autotrade.py        370  (signals -> auto filters -> MASTER GATE -> order)  AUTO TRADE: takes chart signals on the just-closed bar -> TradeManager.open(kind="auto")
├── backtest.py         720  history loader (days / custom dates, M1 + spread history), Prepared (per-bar reads),
│                            simulate (costs, delay, BE, intrabar M1), stats, optimizer (grid, IS/OOS, sensitivity),
│                            BacktestJob (backtest / optimize / walkforward), apply setup
├── walkforward.py       69  rolling TRAIN optimize -> unseen TEST window
├── monte_carlo.py       57  stress test of R list (shuffle, extra slip, skipped trades): percentiles
├── exits.py            273  ATR, VWAP bands, order blocks, swing stop, exit_plan (TP/SL sources), replay() (+BE),
│                            replay_intrabar() (M1 path)
├── caution.py          167  "⚠ caution" checks: OB/OS, divergence, near OB/PDH/PDL, VWAP stretch, big run,
│                            fresh reversal
├── conso.py            242  consolidation box detection (2 tops + 2 bottoms, 5-pip tolerance) + breakout signals
├── reversal.py         306  reversal detector: 6 liquidity sweep types + 8 candle patterns + divergence + context,
│                            confirmation
├── trendline.py        220  automatic trend lines, breakout on CLOSE outside, confirmation, retest, TREND CHANGE
├── regime.py           194  market regime (ADX, Choppiness, ATR percentile, EMA20/50): TREND UP/DOWN, RANGE,
│                            VOLATILE, QUIET, TRANSITION
├── orderflow.py        291  tick-rule footprint: delta, imbalance, stacked, absorption, divergence
├── intermarket.py      246  DXY/EURUSD/XAG/USDJPY/US10Y/US500/BTC from the master MT5: trend + correlation -> gold bias
├── news.py             244  ForexFactory calendar + RSS headlines, warning window
├── news_trader.py      398  news box: grid pending orders around a consolidation box before news (AUTO disabled:
│                            ALLOW_AUTO=False)
├── notify.py           275  Telegram sender (queue, retries, rate limit), auto chat-id discovery, settings
├── alerts.py           403  alert producers: signals, trades (+ pictures), DD lock, news, READY/BLOCKED, EOD report
├── snapshot.py         252  server-side PNG chart pictures (Pillow) for journal/Telegram
├── watchdog.py         211  health checks every 5 s, auto-reconnect followers/master, alerts on change
├── drift.py            259  live vs backtest baseline (streak, drawdown, z-test on avg R, win rate), auto-pause
├── exitguard.py        178  closes an open trade when a TREND CHANGE (or TL break) goes against it (needs ARM)
├── trade_confirmation.py 89 one-time server tokens for manual orders (bound to account/symbol/side/lot/SL/TP/request)
├── risk_engine.py      388  hard blockers before any order, master switch, risk settings (fail safe), per-account view
├── execution_guard.py  163  broker checks per account (stops/freeze level, lot step, spread, latency, SL required)
├── reconciliation.py   205  copier master vs followers every 5 s: repair / pause / orphans
├── decision_engine.py  432  setup matrix + WAIT/WATCH/READY/BLOCKED/OPEN/MANAGING + why-not-trade + timeline
│                            (read-only: never sends orders)
├── setup_fingerprint.py 43  setup key for analytics (e.g. "M5 CHOCH + DISCOUNT + FVG")
├── trade_analytics.py  125  journal analytics: by exit / session / regime / setup, MFE/MAE (descriptive)
├── market_hours.py      93  configurable UTC session table (shared by chart, decision, auto, backtest), gold hours
├── smc_engine.py       559  SMC structural engine: HH/HL/LH/LL, BOS/CHoCH events, FVG states, OB per event,
│                            liquidity + sweeps, dealing range, SMC setup chain (causal; live + history)
├── setup_quality.py    349  category results 0..1, contribution map, conflicts + policy, regime modifiers,
│                            required / confirmation / optional conditions, explanation
├── trade_gate.py       355  MASTER AUTO-TRADE GATE (15 ordered gates, BLOCKED_* codes), signal_id, signal journal
├── position_manager.py 264  single SL controller: BE / profit lock / trailing / partial, priority, tighten-only
├── data_validation.py   96  symbol / tick / OHLC / time order / completeness checks before decisions
├── error_log.py        116  categorized error log (errors.jsonl), secret redaction
├── ai_analyst.py       AI ANALYST (Claude Fable): read-only snapshot -> Anthropic API on a button press; key local
├── companion.py        read-only snapshot for the mobile companion page /m (active indicators + votes, score, trades)
├── strategies.py       TREND (2 BOS M15 + 1 BOS H4) / PULLBACK / CONSOLIDATION (vol + ADX + box) strategies, side-panel status + news row
├── forecast.py        1062 FORECAST REGIME: read-only prediction layer (regime, forecast build, lifecycle,
│                            journal, statistics, replay, live service). Never trades.
├── reygun_term.py      745  older terminal (Textual) UI, same engine/plugins
├── plugins/                 43 plugins (section 4)
├── web/
│   ├── index.html      609  single-page UI (pages: Command Center, Overview, MTF Analyser, Signal Center, Plugins,
│   │                        Accounts, Journal, Order Flow, Backtest, News, System & Alerts, Tape)
│   ├── app.js         3130  all UI logic (TradingView Lightweight Charts v5), polling the REST API
│   ├── style.css       869
│   ├── lightweight-charts.standalone.production.js  (vendor, Apache-2.0)
│   └── brand/               logo (svg/png/ico)
├── tests/                210 tests (plain python: python tests/run_tests.py), fake hub + paper backend
├── requirements.txt         MetaTrader5 (win), pillow, textual, rich
├── setup.bat, run_*.bat
└── README.md               user guide (Taglish)
```

Runtime files, created on first use and not shipped:
- settings: `accounts.json`, `plugin_settings.json`, `scoring_settings.json`, `auto_trade_settings.json`,
  `telegram_settings.json`, `regime_settings.json`, `exit_guard_settings.json`, `drift_*.json`,
  `intermarket_settings.json`, `decision_settings.json`, `execution_settings.json`, `smc_settings.json`,
  `risk_settings.json`, `master_switch.json`, `position_settings.json`, `session_settings.json`
- trading state: `journal*.jsonl`, `trade_groups*.json`, `risk_state*.json`, `signals*.jsonl`, `risk_peak.json`,
  `errors.jsonl`
- folders: `snapshots/`, `reports/`, `recordings/`

---

## 4. Plugins (`plugins/`)

**Indicator categories are score groups.**

`GROUP_ORDER` = Structure, SMC, Trend, Momentum, Volatility, Volume, Order Flow, Levels, Reversal, Intermarket,
Other.

| Category | Plugins (vote = takes part in the score) |
|---|---|
| Structure | Trend Line Breakout (vote: fresh TL✓ / TREND CHANGE / failed break = opposite) |
| Trend | ADX/DMI, EMA 20/50, EMA Ribbon, Ichimoku, Parabolic SAR, SMA 20/50/200, Supertrend (all vote) |
| Momentum | CCI, MACD, RSI, Stochastic, Williams %R (vote) |
| Volatility | Bollinger, Donchian, Keltner (vote), ATR pane, Consolidation Box |
| Volume | CVD (est.), MFI, OBV, VWAP, Volume Profile (vote), Volume |
| Order Flow | Order Flow (absorption / imbalance / divergence votes, live ticks only) |
| Levels | Pivot Points (vote), Auto Fibonacci, Prev Day H/L/C |
| SMC | SMC Structure, Premium/Discount (switch built-in analyser modules) |
| Reversal | Reversal (vote only when a fresh reversal exists) |
| Intermarket | Intermarket (vote = majority implication for gold) |
| Signals | Analyser Signals (READY arrows + SL/TP/result), Conso Breakout Signals |
| TP/SL | VWAP Bands, Order Blocks, ATR Exits (chandelier), TP/SL Map |
| Time | Sessions, Period separator |
| Strategies (alerts only) | Conso Breakout alert, EMA cross M5 (H1 filter), SMC READY alert |

---

## 5. How it works (data flow)

```
MT5 master ──ticks/bars──> Source ──> Hub.step() every 250 ms
                                       ├─ SMC state per TF (H4 H1 M15 M5) ── engine
                                       ├─ plugin votes (enabled for SCORE) ── market_read() -> Read
                                       │     Read = bias (H4/H1), trigger (M5/M15 CHoCH/BOS), group %, OVERALL %,
                                       │     READY when bias + trigger + OVERALL >= passing
                                       ├─ order flow (ticks), recorder
                                       └─ Hub.read_at(t) = the same Read rebuilt from bars closed by t
                                             (cached; used by signals, backtest, auto trade confirmation)

Browser (web/app.js) ── polls /api/state, /api/chart, /api/trading, ... ── draws the chart and cards

Background threads:
  TradeManager._loop (1 s)    refresh accounts -> risk_check (DD/TP lock) -> sync (copier)
  AutoTrader._loop (1 s)      new closed bar -> signals -> filters -> TradeManager.open(kind="auto")
  NewsFeed / NewsTrader       calendar, headlines / news box (auto disabled)
  Intermarket (30 s)          other symbols from the master MT5
  Alerts (2 s)                Telegram signals / news / end-of-day report
  Notifier                    Telegram send queue + auto chat-id discovery
  Watchdog (5 s)              health + reconnect
  Drift (30 s)                live vs baseline
  ExitGuard (2 s)             trend change against open trades
  BacktestJob                 on demand
  DecisionEngine (1 s)        setup matrix -> decision state, timeline, READY/BLOCKED alerts (read-only)
  Reconciler (5 s)            master vs followers: repair / pause / orphans
Followers: one child process each (account_worker.py), called through queues.

Final hierarchy (no lower layer can bypass a higher safety layer; only the order engine sends orders):
  MARKET DATA (sources) -> DATA VALIDATION (data_validation) -> INDICATORS / PLUGINS (plugin_loader contract)
  -> CHART LAYER (Hub.chart, owner-tagged objects) -> SIGNAL / VOTE LAYER (plugin votes, signals)
  -> MARKET STRUCTURE ENGINE (engine.analyze + smc_engine.structure) -> SMC SETUP ENGINE (smc_engine)
  -> GROUPED CONFIRMATION ENGINE (setup_quality) -> DECISION ENGINE (decision_engine)
  -> RISK ENGINE (risk_engine) -> EXECUTION GUARD (execution_guard) -> [AUTO: MASTER GATE (trade_gate)]
  -> ORDER ENGINE (TradeManager.open -> mt5_ops) -> POSITION MANAGER (protect + position_manager)
  -> JOURNAL / AUDIT (journal, signal journal, error log, analytics)
```

**Scoring (`engine.market_read`):**
- Each group's % = the items agreeing with the HTF bias ÷ the items in the group. A neutral vote counts as "not
  agreeing".
- OVERALL = Σ(group% × weight) ÷ Σweight, over the groups that have items.
- The user sets the passing score (default 70) and the weights.

**Trade path:**
0. Manual: `/api/trade/prepare` (server checks) -> one-time token (15 s) -> `/api/trade/confirm`.
1. `TradeManager.open(side, lot, sl_dist, tp_dist, kind, request_id)`, which runs these checks in order:
   - request_id already processed -> ALREADY_EXECUTED
   - `check_request`: armed, not halted, valid values, no-opposite, `risk_engine.hard_blockers`,
     `execution_guard.check` (master hard, followers soft)
   - decision snapshot + setup fingerprint saved BEFORE the order
2. Master `open_market` (comment `RGSC-<gid>`, magic 20260925).
3. The same order goes to each enabled follower, with its own fixed lot. SL and TP are sent as $ distances from
   each account's own fill.
4. A group is created, then the journal entry (score snapshot, news, regime, server time, lifecycle).
5. An alert is sent with the entry picture.
6. After the fill: protective SL watchdog, MFE/MAE tracking, copier sync (fresh data only), reconciliation.

---

## 6. Safety rules (must always hold)

1. **Nothing trades unless ARMED.** ARM is always OFF when the app starts, and it is also turned off when the
   master account changes.
2. **Manual entries** need ARM + click + confirm, backed by a **one-time server token** bound to the order values
   (15 s expiry) and a `request_id` (never executed twice).
3. **AUTO TRADE:**
   - It is always OFF at start. Turning it on needs ARM and a confirm dialog, and it turns itself off on DISARM.
   - It only takes a signal on the bar that just closed. It never takes old arrows: existing ones are marked
     "done" when AUTO is turned on.
   - Filters:
     - direction, session, news window
     - max cautions, min score, max per day
     - max open, **no opposite trade**, **re-entry only after 30 min + still in trend + still passing**
     - spread, regime
     - breakout/trendline signals must be **confirmed by the analyser score**
4. **Exit guard** acts only while ARMED (otherwise alert only), and only on events after the entry that are fresh
   (last 3 bars).
5. **Drift monitor** can only switch AUTO TRADE **off**, never on.
6. **News Trader auto** is disabled in code (`news_trader.ALLOW_AUTO = False`).
7. **Passwords are never stored.** MT5 terminals are logged in by hand with "Save password". The Telegram token is
   stored locally.
8. **Only SuperChart trades are managed** (magic 20260925 / comment `RGSC-`). Other EAs' and manual MT5 trades are
   untouched, except by "Close all" with scope "all".
9. **Daily DD/TP lock per account:** it closes that account's trades and blocks new entries until the next server
   day.
10. **Localhost only:** the web server binds to 127.0.0.1.
11. **Decision engine never sends orders.** Score, confirmations and blockers stay separate; confirmations never
    change the score. MT5 calls happen only in `mt5_ops` / `account_worker`.
12. **STOP NEW TRADES / EMERGENCY STOP:** AUTO off, disarm, cancel REYGUN pending orders (+ close SuperChart
    positions only when ticked).
13. **Protective SL:** a leg without SL gets 3 attach attempts, then is closed (`close_unprotected`).
14. **Stale data is never acted on** (copier sync, close, reconciliation).
15. **Optimizer output is never applied automatically**; Monte Carlo is labelled a stress test, not a prediction.
16. **MASTER TRADING switch** (persisted): OFF = no new trade of any kind. An unreadable switch or risk-settings file
    blocks trading (fail safe).
17. **Every automated order passes the master gate** (15 ordered gates, BLOCKED_* code on the first failure, every
    signal journaled). No gate module, no automated order.
18. **Unknown news is not CLEAR** (NEWS_UNKNOWN; default policy for AUTO: block).
19. **Plugins cannot trade:** forbidden imports refused at load, no trader/gate in the Context, a failed / slow /
    malformed plugin is UNAVAILABLE (never a fake neutral vote).
20. **SL is tighten-only** for every automatic change (position manager, BE button); one controller moves SLs.
21. **Forecast is read-only:** it imports no trading / execution module, never touches the trader or the gate,
    and its endpoints (`/api/forecast*`) never reach the trade API. Its records live in `forecast_journal.jsonl`.

---

## 7. REST API (all JSON, localhost)

GET:
```
/api/state          ticks, analyser Read, plugins, scoring, events, signals
/api/chart?tf=&n=   bars + overlays
/api/trading        accounts, groups, armed
/api/journal        entries, stats, series
/api/journal.csv
/api/auto           /api/exitguard      /api/regime     /api/system (telegram, health, drift, report)
/api/backtest[?light=1]                 /api/news       /api/newstrade
/api/orderflow?tf=&n=                   /api/intermarket
/api/decision       /api/decision/why?side=     /api/setup?side=
/api/tradeplan?side=&lot=&sl=&tp=
/api/risk           /api/risk/accounts          /api/execution          /api/reconciliation
/api/analytics[/exit|session|regime|setup|mfe|mae]
/api/walkforward    /api/montecarlo?n=&slip_r=&skip_pct=&source=backtest|journal
/api/smc            /api/gate (= /api/signals/journal)   /api/errors   /api/positions/manager
/api/decision/settings                  /api/sessions
/api/forecast   /api/forecast/history   /api/forecast/stats   /api/forecast/settings   /api/forecast/replay
/snap/<file>.png    /report/<day>.png
```

POST:
```
/api/plugin                 {key, chart, score, params, vote_tf}
/api/scoring                {passing, weights}
/api/trade/<act>            arm | prepare | confirm | open (needs token) | close | modify | be | closeall | unlock |
                            reconnect | symbols | test | plan | stopnew | emergency | safety
/api/execution/settings     /api/reconciliation/resume {id}   /api/reconciliation/settings {auto_repair}
/api/master {enabled}       /api/risk/settings   /api/risk/reset_peak   /api/decision/settings
/api/smc/settings           /api/positions/settings              /api/sessions
/api/forecast/create | cancel {id} | settings | replay {days}   (read-only: no order is ever sent)
/api/accounts               master + followers config
/api/auto/enable|settings   /api/exitguard/settings   /api/regime/settings
/api/backtest/run           {mode backtest|optimize|walkforward, days | date_from+date_to, tf, spread, lot,
                             objective, min_trades, opt_weights, setup (+be_r), spread_mode, commission_per_lot,
                             slippage, swap_per_lot, execution_delay_ms, intrabar, train_days, test_days}
/api/backtest/apply         {setup}
/api/telegram/settings|test|findchat   /api/report/send {preview}
/api/drift/baseline|clear|settings
/api/news/settings|refresh  /api/newstrade/enable|settings|cancel
/api/intermarket            /api/orderflow/row   /api/pause   /api/speed   /api/record
```

---

## 8. Key defaults

| Setting | Default |
|---|---|
| Passing score | 70% (all group weights 1) |
| Analyser Signals | SL 1.5×ATR (max $10), TP 2R, min RR 1, one_at_a_time ON, max_cautions -1 (show only) |
| Auto trade | M5, source analyser, lot 0.01, max 3/day, max open 1, sessions London+NY, news block ON, max cautions 0, max spread $0.60, no_opposite ON, re-entry 30 min, score_confirm ON |
| Reversal | min score 60, confirm within 3 bars, vote within 3 bars (M15) |
| Trend line | swing 4, breakout buffer max(0.1×ATR, 5 pips), confirmed ≥ 55 pts, retest within 12 bars, TREND CHANGE = CHoCH within 24 bars, vote within 6 bars (M15) |
| Exit guard | ON, trigger TREND CHANGE, action close all, M15, all trades, needs ARM |
| Regime | M15 + H1, trend ADX ≥ 25, range ADX < 20, choppy ≥ 61.8, volatile ATR pct ≥ 85, quiet ≤ 15 |
| Execution | SL required, broker data stale after 60 s, confirmation 15 s |
| Reconciliation | every 5 s, auto repair ON, orphan close after 30 s, pause after 3 failed repairs |
| Safety | close unprotected ON, 3 SL retries × 2 s, follower stale 6 s, closing timeout 600 s, close scope superchart |
| Decision | required = score + HTF bias + trigger, min confidence 0, conflict policy structure_priority, regime rules ON |
| Confidence weights | STRUCTURE 25, SMC 25, TREND 20, MOMENTUM 15, LOCATION 10, VOLUME 5 (others 0) |
| SMC engine | M15 setup TF, H1 dealing range (htf_swing), swing n 2, FVG >= 0.15 ATR with displacement >= 0.8 ATR, partial 50 %, sweep needs a structure reaction within 12 bars |
| Risk settings | max risk/trade 2 % of equity, news unknown = block AUTO, everything else off (0) |
| Position manager | OFF |
| Sessions (UTC) | Asia 0-9, London 7-16, New York 12-21 |
| Backtest | spread $0.30, 70/30 IS/OOS, robust = OOS trades ≥ max(3, min_trades/4) and OOS R > 0 and PF ≥ 1.1 |
| Telegram | events all ON, pictures ON, daily report 23:00 UTC+8 |
| Watchdog | every 5 s, alert after 2 bad checks, no-tick alert after 3 min while gold is open |

---

## 9. Known limits

- **Not yet tested on a real MT5 account.** Only demo data, paper trading, and a fake MT5 and fake Telegram server
  in tests have been used.
- **Order Flow, Intermarket and News have no history,** so they are not in backtests. They are forward-test only.
- **Backtest:**
  - Its speed and history length are limited by MT5 "Max bars in chart".
  - It models one trade at a time. Costs (spread incl. variable, commission, slippage, swap, delay) and intrabar M1
    resolution are available but approximate; variable spread and M1 need MT5 history.
- **Session hours and gold market hours** are approximated in UTC.
- **Sandbox:** It has not been run on Windows yet. Development and tests ran on Linux with the MT5 package mocked.
