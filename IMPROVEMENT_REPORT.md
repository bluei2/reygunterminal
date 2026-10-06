# REYGUN Terminal: master improvement report

This report covers the implementation of the "REYGUN TERMINAL – MASTER IMPROVEMENT SPECIFICATION" (47 sections).

Scope of the work:
- **No rewrite.** Every existing module, REST endpoint, settings file and journal format still works.
- **The existing analyser score is unchanged.** It is still the `score` condition of the decision.
- **New layers sit on top.** They are separate modules, each with its own tests.

Test status: **123 passed, 0 failed, 0 skipped**. Only demo data, paper trading and fake MT5 backends were used.
Nothing has been tested on a real MT5 account yet.

---

## A. Changed files

**New modules**

| File | Purpose |
|---|---|
| `smc_engine.py` | SMC structural engine: structure (HH/HL/LH/LL, BOS/CHoCH with strength), FVG states, OB per structure event, liquidity levels + sweeps, dealing range, the SMC setup chain |
| `setup_quality.py` | Category results (0..1), contribution map, conflict detection + policy, regime modifiers, required / confirmation / optional conditions, explanation text |
| `trade_gate.py` | MASTER AUTO-TRADE GATE: 15 ordered gates with `BLOCKED_*` codes, `signal_id`, setup de-duplication, signal journal |
| `position_manager.py` | The single SL controller: break-even (R / $), profit lock, ATR / step / structure trailing, partial close, priority, tighten-only, broker stops / freeze level |
| `data_validation.py` | Symbol, tick, spread, OHLC, time order, future bars, completeness |
| `error_log.py` | Categorized error log (`errors.jsonl`), secret redaction, rate-limited `report()` for handled exceptions |

**New test files**

`tests/test_smc.py`, `test_quality.py`, `test_gate.py`, `test_position.py`, `test_plugins.py`, `test_time.py`, and
`test_data.py`. A partial-fill test was also added to `tests/test_reconciliation.py`.

**Modified files**

| File | Change |
|---|---|
| `superchart.py` | Hub: data validation, `smc` / `smc_at()`, plugin contract, error routing, plus new REST endpoints (smc, gate, errors, positions, decision settings, master switch, risk settings, sessions). Wires in the gate and position manager. |
| `decision_engine.py` | Consumes the setup-quality object. User-selectable REQUIRED conditions. Conflict blockers. Uses the SMC dealing range for P/D. Adds SMC setup and sweep rows, NEWS_UNKNOWN, explanation, and richer journal snapshot fields. |
| `risk_engine.py` | Master switch, risk settings (fail safe), and these blockers: DATA_INVALID, NEWS_UNKNOWN (policy), RISK_PER_TRADE, MAX_TOTAL_DD, EXPOSURE_LIMIT, INVALID_SL, INVALID_TP, TP_REQUIRED, MIN_RR, MAX_TRADES_REACHED and MAX_OPEN_REACHED (for every order kind). Uses the broker contract size. |
| `autotrade.py` | Every filter now has a code, the master gate is required, and `request_id = signal_id`. Session hours come from the shared table. Re-entry fails safe when the regime is unavailable. |
| `trading.py` | `open(meta=…)` saves signal_id / setup_id in the journal. `modify_group(source=…)`. The BE button is tighten-only. New group fields `pm_mode` / `pm_done`. Paper broker gets a contract size. |
| `backtest.py` | Signal counters (generated / blocked / executed, by reason). `integrity()` report. `smc_filter` uses the same `smc_engine`. Session hours come from the shared table. |
| `plugin_loader.py` | Static safety contract: forbidden imports and tokens. |
| `news.py` | `news_state()` returns NEWS_CLEAR / NEWS_HIGH_IMPACT / NEWS_UNKNOWN. |
| `market_hours.py` | Configurable UTC session table (`session_settings.json`), used everywhere. |
| `mt5_ops.py` | `broker_info` returns `contract_size`. |
| `alerts.py`, `regime.py` | Minor: MASTER_DISABLED is not alerted as BLOCKED; no silent exception. |
| `web/index.html`, `web/app.js`, `web/style.css` | Command Center state chips (green / yellow / red / gray). New cards: setup quality, decision rules, SMC setup, master gate, risk engine, position manager. MASTER switch in the sidebar. Error log on the System page. Backtest SMC filter and integrity report. |
| `tests/harness.py` | Moves every new settings / state file into the temp folder. |
| `README.md`, `STRUCTURE.md` | Documentation. |

---

## B. Architecture (final data flow)

```
MARKET DATA            sources.py (MT5 / demo / replay)
  -> DATA VALIDATION   data_validation.validate()          every analysis cycle -> Hub.data_status
  -> INDICATORS/PLUGINS plugin_loader (contract) -> Hub.collect_votes / chart / strategies (isolated, timed)
  -> CHART LAYER       Hub.chart(): every object tagged {owner, type, timeframe, created_at[, signal_id]}
  -> SIGNAL / VOTES    plugin votes (engine groups), chart signals (Analyser / Breakout / Trend line)
  -> STRUCTURE         engine.analyze (existing score) + smc_engine.structure (HH/HL/LH/LL, BOS/CHoCH)
  -> SMC SETUP         smc_engine: sweep -> BOS/CHoCH -> FVG/OB -> retest = ONE setup object
  -> GROUPED CONFIRM   setup_quality.evaluate(): categories -> confidence, conflicts, regime, conditions
  -> DECISION          decision_engine.decide(): WAIT / WATCH / READY / BLOCKED / OPEN / MANAGING + explanation
  -> RISK              risk_engine.hard_blockers()
  -> EXECUTION GUARD   execution_guard.check()
  -> [AUTO] MASTER GATE trade_gate.MasterGate.evaluate(): 15 ordered gates, signal journal
  -> ORDER ENGINE      TradeManager.open() (check_request again) -> mt5_ops / account_worker (only place with order_send)
  -> POSITION MANAGER  TradeManager.protect() (emergency) + position_manager.PositionController (SL rules)
  -> JOURNAL / AUDIT   journal.jsonl (trades), signals.jsonl (every signal), errors.jsonl, analytics
```

Rules that hold across the flow:
- **Signal ≠ decision ≠ order.** Every signal is logged in four stages: SIGNAL → DECISION → RISK → EXECUTION
  (for example `BUY_SETUP_DETECTED` → `BUY_ALLOWED` → `RISK_ALLOWED` → `EXECUTION_ALLOWED`).
- **Only the order engine sends orders.** No other module does:
  - The decision engine never sends.
  - The gate never sends.
  - Plugins cannot import the order modules.

---

## C. Safety gates (all of them)

**Manual order:** ARM → click → server `prepare` (every check below) → one-time token (15 s) → `confirm` →
`check_request` again → order.

**Gates in `risk_engine.hard_blockers` (every kind of order):**
- MASTER_DISABLED, CONFIG_INVALID, HALTED, ARM_OFF
- MASTER_DISCONNECTED, ALGO_TRADING_OFF
- DAILY_DD_LOCK, DAILY_TP_LOCK, MAX_TOTAL_DD
- OPPOSITE_POSITION, MAX_OPEN_REACHED, MAX_TRADES_REACHED
- SPREAD_TOO_HIGH, NEWS_BLOCK and NEWS_UNKNOWN (for auto, or manual when configured)
- DATA_INVALID, STALE_TICK, STALE_ANALYSER, MARKET_CLOSED
- INVALID_SL, INVALID_TP, TP_REQUIRED, MIN_RR, RISK_PER_TRADE, EXPOSURE_LIMIT
- DRIFT_PAUSE (auto)

**Gates in `execution_guard` (per account; master = hard, follower = skip):**
- NOT_CONNECTED, ACCOUNT_LOCKED, EXECUTION_NOT_READY, BROKER_DATA_STALE
- TRADING_NOT_ALLOWED, SYMBOL_TRADE_DISABLED, SYMBOL_NOT_SELECTED
- INVALID_LOT, NO_PROTECTIVE_SL
- BROKER_STOPS_LEVEL, BROKER_FREEZE_LEVEL
- SPREAD_TOO_HIGH, HIGH_LATENCY, NO_MARGIN

**Other protections**

| Area | Protection |
|---|---|
| After the order | Protective SL watchdog: attach up to 3 times, then close the leg. Copier freshness guards. Reconciliation: side mismatch → pause, extra volume → partial close, never top up, orphan close. |
| Duplicates | `request_id` (never executed twice). `signal_id` (never processed twice, survives restarts). `setup_id` (the same SMC setup is not traded again while its trade is open or within the re-entry window). |
| Master switch | Persisted. Fail safe if the file is unreadable. |
| Invalid risk config | CONFIG_INVALID blocks trading. It is never silently "allow". |
| Plugins | Cannot import order / broker modules, and get no trader in their Context. A failed plugin is UNAVAILABLE, never a neutral vote. |

---

## D. SMC

| Concept | Implementation |
|---|---|
| **BOS / CHoCH** | CLOSE beyond the latest confirmed swing (fractal, n bars each side; a swing is only known n bars after its extreme). A break in the current trend direction is a BOS; against it, a CHoCH. Each event: `timestamp, price, direction, timeframe, event_type, strength (0..1 from body and close distance in ATR), source`. |
| **HH / HL / LH / LL** | Every confirmed swing is labelled against the previous swing of the same kind. Structure strength = the share of the last 4 labels that agree with the trend. |
| **FVG** | 3-candle gap, at least `fvg_min_atr × ATR`, with a displacement candle (body at least `fvg_min_displacement × ATR`). Tracked bar by bar: `fill_percentage`, then FRESH → PARTIALLY_FILLED → MITIGATED (fill ≥ `fvg_partial`) or INVALIDATED (close or wick beyond the far edge, configurable). Expires after `fvg_max_age` bars. A new gap that overlaps an active zone in the same direction is not added again. |
| **Order block** | For each BOS / CHoCH event, the last opposite candle before the breaking bar (within `ob_lookback`). At most one per event, linked through `source_structure_event`. Status FRESH → MITIGATED (first touch) or INVALIDATED (close or wick beyond), EXPIRED. |
| **Liquidity** | Levels: equal highs / lows (tolerance × ATR), previous-day high / low, previous completed session high / low, swing highs / lows. Each is marked INTERNAL or EXTERNAL to the dealing range. A **sweep** is a wick beyond a level with a CLOSE back inside (rejection). A close beyond is recorded as a **breakout**, not a sweep. The sweep is *confirmed* when a structure break in its direction follows within `sweep_reaction_bars` (configurable). Several levels swept by the same bar count as one sweep. |
| **Premium / discount** | Explicit dealing range with a configurable `range_source`: `htf_swing` (last confirmed H1 swings), `prev_day`, `session`, or `custom`. Exposes `DEALING_RANGE_HIGH / LOW`, `EQUILIBRIUM`, position, and zone DISCOUNT / EQUILIBRIUM / PREMIUM (±`eq_band`). An invalid range is UNKNOWN, never guessed. |
| **SMC setup** | One chain: confirmed sweep → BOS / CHoCH in the sweep direction within the reaction window → FVG / OB from that displacement leg → retest (the zone is touched and not invalidated). Output: `direction, stage 0–4, status` (FORMING / WAITING_RETEST / RETESTED / INVALIDATED), `quality` (LOW / MEDIUM / HIGH), `confidence` 0..1, `invalidation` (the sweep extreme), `entry_zone`, `components`, and `setup_id`. A close beyond the invalidation price kills the setup (confidence 0). The components are **not** added as separate points. |

---

## E. Scoring: how double counting is prevented

1. **The existing analyser score is kept as it was.** It averages *within* a group (group % = agreeing items ÷
   items), then takes a weighted average of groups. Five trend indicators therefore already make one Trend %,
   not five points.
2. **The setup-quality layer gives each information type ONE result** (direction, support 0..1, confidence 0..1):
   - STRUCTURE: HTF bias, M15 structure and the trigger, combined into one value.
   - SMC: the setup chain's confidence, once.
   - LOCATION: the dealing range.
   - TREND, MOMENTUM, VOLATILITY, VOLUME: majority of that category's plugin votes.
   - LIQUIDITY: weight 0 by default, because the sweep is already inside the SMC chain.
3. **Contribution map** (configurable; defaults STRUCTURE 25, SMC 25, TREND 20, MOMENTUM 15, LOCATION 10,
   VOLUME 5):
   - confidence = Σ(weight × support) ÷ Σ(weight of the *available* categories).
   - An unavailable category is excluded and shown GRAY; it is not counted as neutral.
   - A category can never contribute more than its weight, however many indicators it has. This is tested:
     1 vs 10 identical trend votes give the same confidence.
4. **Conflicts are reported, not averaged.**
   - `<CATEGORY>_CONFLICT` for each opposing category, plus `STRUCTURE_CONFLICT` (against the HTF bias, or against
     both the M15 and H1 structure) and `SMC_CONFLICT` (an opposite HIGH-quality retested setup).
   - Policy `structure_priority` (default): structure / SMC conflicts block, and momentum etc. only warn.
   - Policy `block_high_conflict`: also blocks when N categories conflict.
   - Policy `warn_only`: never blocks.
5. **Regime** only changes the requirements; it never creates an entry:
   - TRENDING counter-trend: +0.15 confidence required.
   - RANGING without a sweep: +0.10.
   - BREAKOUT: displacement is required.
   - HIGH_VOLATILITY: +0.10 and auto lot ×0.5.
   - TRANSITION: +0.05.
6. **Conditions** are classed REQUIRED / CONFIRMATION / OPTIONAL. The user picks which ones are required
   (Command Center → Decision rules). The default (score + HTF bias + trigger) equals the old READY rule.

---

## F. AutoTrade: every gate before an order

1. The chart signal is on the bar that JUST closed, and was not handled before.
2. The AUTO TRADE filters each give a code: BLOCKED_DIRECTION, BLOCKED_SESSION, score confirmation (breakout /
   trend line: `read_at` of the signal bar), BLOCKED_REGIME, BLOCKED_CAUTIONS, BLOCKED_SCORE, BLOCKED_MAX_TRADES,
   BLOCKED_OPPOSITE_POSITION, BLOCKED_REENTRY (30 min plus a fresh trend and score confirmation),
   BLOCKED_MAX_OPEN_POSITIONS, BLOCKED_INVALID_SL.
3. The **master gate** evaluates, in order:

| # | Gate | Block code |
|---|---|---|
| 1 | DATA | BLOCKED_STALE_DATA |
| 2 | MASTER | BLOCKED_MASTER_DISABLED |
| 3 | SESSION | BLOCKED_SESSION |
| 4 | NEWS | BLOCKED_NEWS |
| 5 | BIAS | BLOCKED_STRUCTURE |
| 6 | STRUCTURE | BLOCKED_STRUCTURE |
| 7 | SMC | BLOCKED_SMC |
| 8 | LOCATION | BLOCKED_LOCATION |
| 9 | CONFIRM | BLOCKED_SCORE |
| 10 | CONFLICT | BLOCKED_CONFLICT |
| 11 | RISK | BLOCKED_DD_LIMIT / _DAILY_TARGET / _MAX_TRADES / _MAX_OPEN_POSITIONS / _OPPOSITE_POSITION / _INVALID_SL / _INVALID_TP / _RISK / _SPREAD |
| 12 | EXECUTION | BLOCKED_EXECUTION |
| 13 | DUPLICATE | BLOCKED_DUPLICATE_SIGNAL / _SETUP |
| 14 | AUTO | BLOCKED_AUTO_DISABLED |
| 15 | ARM | BLOCKED_NOT_ARMED |

   - The first failing gate gives the block code, and every failing gate is listed.
   - An unknown blocker code still blocks (as BLOCKED_RISK / BLOCKED_EXECUTION).
   - If the gate module is missing, no automated order is sent.
4. The lot is adjusted by the regime / news risk multipliers, rounded down to the broker lot step.
5. `TradeManager.open(request_id = signal_id)` runs `check_request` again (ARM, risk, execution) and sends the order.
6. The signal journal links `signal_id → setup_id → trade_id`. The trade journal stores the signal_id, setup_id,
   confidence, conflicts, SMC state and indicator groups.

---

## G. Backtest integrity: how look-ahead is prevented

- **Analyser:** `Hub.read_at(t)` rebuilds the analyser read from bars with `bar.t + tf ≤ t` only, so the forming
  candle (including the forming HTF candle) is excluded.
  - Tested: a read is identical whether the future exists or not.
  - Tested: changing the forming H1 candle does not change an earlier read.
- **SMC:** `Hub.smc_at(t)` / `smc_engine.analyze` use the same cut and a single forward pass:
  - swings are only known n bars later;
  - FVG / OB status is updated bar by bar;
  - PDH / PDL come from closed D1 bars;
  - the previous session is only used once it has ended.
  - Tested: `smc_at` equals the analysis of truncated history, and structure events are never redrawn.
- **Trade simulation:**
  - Entries happen on the close of the signal bar, or the next open with an execution delay.
  - Exits use later bars only.
  - Trailing uses closed bars.
  - Intrabar mode resolves SL / TP on M1.
- **Same engines, live and backtest:** `market_read`, `smc_engine`, and the same session table (`market_hours`).
  The backtest SMC filter uses the live SMC engine.
- **Report:**
  - trades, win rate, loss rate, profit factor, expectancy (R)
  - max drawdown, average RR (planned), average win / loss
  - max consecutive losses, worst day (daily drawdown in R), daily target hit rate
  - **signals generated / blocked (by reason) / executed**
- **Regression:** the new code gives IDENTICAL results to the previous version in 6 setup and cost combinations.

---

## H. Tests: 123 passed, 0 failed, 0 skipped

Run with `python tests/run_tests.py` (plain Python; runs in a temp folder).

<details><summary>All test names</summary>

- **test_analytics:** analytics_groups_and_old_entries, classify_exit, mfe_mae_recorded_and_exit_class
- **test_backtest:** breakeven_event, costs_reduce_results, delay_fills_next_open,
  intrabar_uses_m1_and_no_lookahead, monte_carlo, sensitivity_flags, trailing_uses_only_closed_bars,
  walkforward_runs
- **test_data:** backtest_signals_generated_blocked_executed, error_log_categories_and_redaction,
  hub_errors_go_to_error_log, invalid_ohlc_time_order_future, news_states, symbol_mismatch, tick_and_spread,
  valid_bars_pass
- **test_decision:** blocked_when_disarmed, decision_blocked_by_spread, decision_ready,
  decision_wait_low_score_and_why, decision_watch, fingerprint_and_journal_fields, max_trades_auto,
  risk_engine_blocks_manual_open_on_stale_tick
- **test_execution:** follower_skipped_master_trades, freeze_level, invalid_lot_step, missing_sl_blocked,
  monitor_rows, stops_level
- **test_gate:** all_gates_pass, auto_disabled, autotrade_goes_through_the_gate, corrupt_master_file_fails_safe,
  dd_limit_and_daily_target, duplicate_signal, every_signal_is_journaled, gate_order_master_before_arm,
  invalid_lot, invalid_risk_config_fails_safe, invalid_sl_and_tp, master_disabled, max_trades_and_max_open,
  news_unknown_policy, not_armed, opposite_position, risk_per_trade, score_and_structure_required, stale_data,
  total_dd_limit
- **test_plugins:** chart_objects_have_owners, failed_plugin_isolated_and_not_neutral,
  forbidden_imports_detected_and_not_loaded, malformed_compute_rejected, malformed_vote_rejected,
  plugin_context_has_no_order_access, plugin_timeout_strikes, shipped_plugins_follow_the_contract, indicator_drawn_only_on_chosen_timeframes, score_follows_chart_timeframes, module_checks_follow_chart_timeframes
- **test_position:** break_even_then_profit_lock_most_protective_wins, breakeven_button_never_loosens,
  broker_stops_and_freeze_level, controller_moves_sl_on_all_accounts_and_journals,
  controller_skips_unprotected_and_disarmed, never_loosens, partial_close_once, step_and_structure_trailing
- **test_quality:** confidence_is_0_to_1, explanation_never_bare, high_conflict_policy_blocks,
  invalid_config_falls_back_to_safe_defaults, many_similar_indicators_do_not_outvote_structure,
  momentum_conflict_is_reported_not_averaged, regime_modifies_requirements_not_entries,
  required_conditions_are_configurable, smc_counts_once, structure_conflict_blocks_unless_warn_only
- **test_reconciliation:** extra_volume_partially_closed, orphan_closed_after_age, other_ea_untouched,
  partial_fill_reported_never_topped_up, side_mismatch_pauses_follower_and_resume, sl_mismatch_repaired,
  stale_follower_no_action, sync_ok
- **test_safety:** arm_off_blocks, close_group_waits_for_stale_follower, close_scope_default_and_migration,
  daily_dd_lock_per_account, duplicate_request_id, emergency_stop, failed_order_journaled_and_stats_ok,
  missing_sl_attached, missing_sl_closed_when_it_cannot_be_attached, old_journal_compatible, opposite_blocked,
  per_account_risk_day, stale_follower_is_not_touched_then_closed_when_fresh, token_expiry,
  token_reuse_and_binding
- **test_smc:** choch_on_reversal (incl. LH/LL + BOS down), equal_highs, fvg_no_duplicates, fvg_states,
  order_block_one_per_event_and_mitigation, premium_discount_explicit_range, smc_no_lookahead,
  smc_setup_is_one_chain, structure_labels_and_bos, sweep_needs_reaction_when_configured, sweep_vs_breakout,
  swings_known_only_after_confirmation
- **test_time:** broker_time_is_not_utc, htf_uses_closed_candles_only, read_at_ignores_future_bars,
  session_boundaries_utc, sessions_configurable_and_validated, smc_at_equals_truncated_history

</details>

**Also run:**
- Integrated demo run of about 400 simulated minutes with the full live stack. One auto trade went through all 15
  gates and closed. A later signal from the same SMC setup was blocked with `BLOCKED_DUPLICATE_SETUP`. The error
  log stayed empty.
- Browser check of every new card: no page errors.

---

## I. Remaining issues and known limits (not hidden)

1. **No real MT5 account yet.** Everything ran on demo data, paper trading, and fake MT5 / Telegram in tests.
2. **Plugin sandboxing is not absolute.** CPython cannot sandbox code.
   - The contract is enforced by a static import / token check, a Context without trader / gate references, and
     output validation.
   - A deliberately malicious plugin could still reach objects through modules it is allowed to import (for
     example `intermarket.CURRENT` holds the hub).
   - A plugin that never returns blocks its caller: the timeout rule counts slow calls but cannot interrupt one.
3. **The backtest does not replay the whole live decision stack per bar.**
   - Shared with live: the analyser (`read_at`), the SMC engine (optional filter), sessions, cautions and costs.
   - Not modelled: setup-quality confidence / conflicts / regime modifiers, the risk-engine limits (DD, exposure,
     news), and the position-manager rules (profit lock, partial close). Only BE and trailing are modelled.
   - Replay mode (`--mode replay`, recorded ticks) does run the full live stack, including the gate.
4. **Two structure implementations exist:** `engine.analyze` (drives the existing score, unchanged as required) and
   `smc_engine.structure` (the new structural view). Both are fractal / close-based but can differ slightly (swing
   n, window).
5. **Heuristic thresholds.** The SMC thresholds (FVG size / displacement, sweep reaction window, setup confidence
   formula) are sensible defaults, not optimized. SMC setups are relatively rare, so the SMC filter cuts trades
   a lot (in the demo: 45 signals → 8 trades).
6. **Many categories may be unavailable.** Setup-quality categories only exist when plugins of that category are
   ON for SCORE. With few plugins on, confidence comes mostly from STRUCTURE / SMC / LOCATION.
7. **News defaults can block auto trading.** News uses the ForexFactory weekly calendar. When it cannot be loaded
   (offline VPS, blocked site) the state is NEWS_UNKNOWN, and the default policy blocks AUTO. Set
   `news_unknown = allow` or `reduce_risk` to change it.
8. **Session hours are fixed in UTC.** They do not follow DST, and gold market hours are approximated.
   - The Asia session was unified to 0–9 UTC. The old chart header used 23–8, which differed from auto trade /
     backtest.
9. **Some broad exceptions remain.** 13 `except Exception: pass`-style handlers remain in non-decision code
   (sources, snapshot, watchdog, drift, the old Textual UI). The ones in the decision / risk / execution /
   auto-trade paths now report to the error log, and re-entry fails safe.
10. **Thread safety relies on locks, not a single state store.** There is one `Hub.lock` for MT5 / plugins and
    `TradeManager.lock` for groups. The decision engine and gate read `hub.read` / `hub.smc` / `hub.states` without
    the lock (atomic reference swaps in CPython). There is no single transactional state store.
11. **Some duplicate timing uses the wall clock.** The duplicate-setup window uses wall-clock time. That is correct
    live, but at demo speed it is compressed.
12. **Contract-size fallback.** Risk $ uses the broker `contract_size`, and falls back to 100 (flagged in the UI)
    when the broker gives none.
13. **Position manager is off by default** and has only been tested on the paper backend.

---

## J. Final code

The complete project is in `reygun_terminal.zip`: all modified and new files, with runtime files removed. Module
responsibilities are in `STRUCTURE.md`, and the user guide (Taglish) is in `README.md`.

---

## Addendum: FORECAST REGIME (read-only prediction layer)

- **New module `forecast.py`:** ForecastEngine, ForecastJournal, clean_settings (the config), statistics, replay,
  and ForecastService (live). The forecast record is a dict: ID, snapshot and lifecycle.
- **Tests:** `tests/test_forecast.py` has 39 tests. The full suite is **164 passed, 0 failed**.

**What it reads and what it touches**
- It reads only through `Hub.read_at(t)`: the unchanged score, bias, trigger and groups, plus the structure states
  as a read-only reference (`Read.tf_states`).
- It also reads `Hub.smc_at(t)`, `regime.classify` on cut bars, sessions, and news / spread (live only).
- It never recalculates the score. It never imports or touches trading, execution, the gate, or the trade API. This
  is enforced by an AST test and an endpoint-block test.

**Lifecycle**

WAITING → TRIGGERED → RUNNING → TP_HIT / SL_HIT / EXPIRED, or WAITING → EXPIRED / INVALIDATED (with a reason).

- NOT TRIGGERED + INVALIDATED is never SL.
- Same-bar SL + target is AMBIGUOUS_BAR, with the rule `sl_first` (default), `tp_first` or `exclude`.
- On the trigger bar only the stop is checked.
- Live ticks use the real order.

**Replay and statistics**
- Replay uses `read_at` / `smc_at` for every closed bar, so there is no look-ahead. This is tested: a replay
  without the last day creates identical early forecasts. Replay is reproducible (tested).
- Statistics: overall, by regime / direction / quality / session / day / month / entry type, confidence
  calibration (with a minimum sample flag), and analyser score vs result.

**Regression**
- Backtest results are IDENTICAL to before.
- Every existing score / TF-selection / plugin / gate / safety test still passes.
- AUTO TRADE gate decisions are identical with or without the forecast service (tested).

**Known limits**
- In the demo replay, most not-triggered forecasts end as `SETUP_REPLACED`. When the trend moves on,
  the LIMIT zone is replaced by a newer one (entry moved more than 1 ATR). This is recorded explicitly, not hidden.
- Forecasts are evaluated on the forecast bar TF (M5 default). Only live ticks give intrabar order.
- If the app is closed, bars missed while it was off are not replayed into the active forecasts.

**Forecast profile and chart (follow-up)**

- The forecast can read the LIVE setup (default) or its own profile (`forecast_profile.json`). The profile sets,
  per indicator, CHART / SCORE / timeframes / vote TF / parameters, plus the passing score and weights.
- The profile runs in a separate analyser instance: its own Hub and plugin objects over the same price data. Tests
  confirm that the live scoring and plugin settings stay byte-identical.
- Each forecast records which profile produced it (`profile`).
- The Forecast page has its own chart: candles, the profile's CHART indicators, violet forecast levels, and
  created / entry / result markers.

### Comparison forecast (Structure vs Volume Profile)
- **Setups:** `setups` (structure, vp_poc, vp_edge) run side by side, with one active forecast per setup type.
  The fingerprint includes setup_type.
- **vp_poc:** the previous-day POC rejection. A LONG targets pVAH; a SHORT targets pVAL.
- **vp_edge:** a pVAH / pVAL poke with the close back inside. It fades toward pPOC.
- **Levels:** both setups take their levels from the plugin's `prev_day_profile` (previous complete day only).
- **Filters:** `vp_min_rr` (1.5), `vp_bias_filter` (HTF bias), `vp_tol_atr` (0.10).
- **Statistics:** `statistics()` returns `by_setup` and `comparison`. The UI shows the comparison table in the
  live stats and in the replay, and flags a sample as insufficient under 30 triggered forecasts.
- **Safety:** still read-only. There are no orders, and nothing changes in the score, gate or auto trade.
- **Tests:** there are 6 new tests: POC long → VAH, POC short → VAL, edge fade → POC, the RR / bias filters,
  independent setups, and previous-day-only levels.

### Combined setup: Structure + POC pullback (`struct_poc`)
- **Main indicator:** market structure. The regime must be TREND or PULLBACK, from the H4 / H1 bias and the M15 structure.
- **Place:** the previous day's POC. Price must be on the trend side of pPOC and within `sp_max_dist_atr` ATR (3.0).
  The POC must also be inside the structure: above the M15 HL for a long, below the M15 LH for a short.
- **Entry and exits:** LIMIT at pPOC ± tolerance. The SL goes beyond the M15 swing (else pVAL / pVAH, else ATR).
  TP2 is the M15 swing the pullback started from, used when RR ≥ `vp_min_rr`; otherwise TP2 is `rr` × risk.
- **Invalidation:** a new code, `MISSED_NO_PULLBACK`, fires when price reaches TP2 before the pullback.
- **Tests:** 4 new tests: long, short mirror, rules (POC beyond HL / wrong side / too far / range), and wait → trigger / missed.

---

## Addendum: score explanation and AI Analyst (Claude Fable)

**Score explanation (engine.py)**
- `score_overall(raw, bias, weights, neutral, mode)` returns the OVERALL and the exact points each non-agreeing item
  costs. The lost points always add up to 100 − OVERALL.
- `Read.lost` and `Read.alt` hold the OVERALL under all 4 option combinations. Both go to the UI ("BAKIT X% ANG SCORE?").
- New scoring options:
  - `neutral`: `against` (default, original) or `ignore`.
  - `mode`: `group` (default, original) or `indicator`.
- Defaults reproduce the original formula exactly. The backtest regression and every existing test are unchanged.
- **Finding (demo, all indicators scored):** most indicators agree with the HTF bias about 40–55% of the time.
  Structure is always present and averages about 62%. Single-indicator groups swing the OVERALL by a full group
  weight on one neutral vote. The low score is the formula working as designed, not a bug.

**AI Analyst (ai_analyst.py, new)**
- **Service:** the `AIAnalyst` service builds a read-only snapshot and calls the Anthropic Messages API
  (urllib, no new dependency). The default model is `claude-fable-5-1`.
- **Modes and endpoints:** modes are analysis / score / question. Endpoints: `GET /api/ai`, `POST /api/ai/settings`,
  `POST /api/ai/ask`.
- **Key handling:** the key lives only in `ai_settings.json` (chmod 600). It is never returned by the API (only
  key_set + last 4 characters). The error log redacts `sk-ant-…`.
- **Call limits:** one request per button press, with a cost guard of `min_interval_s`. There is no background calling.
- **Tests:** `tests/test_ai.py` has 5 tests: key never exposed, snapshot content / model / headers, cost guard,
  readable API errors, and the no-trade AST + armed-account check. `tests/test_scoring.py` has 3 tests.
- **Full suite:** 172 passed, 0 failed (184 with the later additions below).
- **Not verified here:** a live call to api.anthropic.com. The sandbox has no network access to it and no key; tests
  use a fake HTTP opener.

### Companion view (`/m`, read-only)
- **`companion.py`:** builds `snapshot(hub)` from:
  - safety state and score (groups, lost points, alt scores);
  - **active indicators** (CHART and / or SCORE on): TFs, per-plugin vote and state, plus modules and strategies;
  - master-side trades, active forecasts, the last AI answer, and recent events (redacted).
- **Data wiring:** `GET /api/companion` serves the snapshot and `/m` serves `web/m.html`, a mobile page polling
  every 3 s. The hub records `votes_by_plugin` in `collect_votes` (read-only attribution, the score is unchanged).
- **Tests:** `tests/test_companion.py` has 3 tests: only active indicators with votes, a complete JSON snapshot,
  and no secrets / no trading. The full suite is **175 passed, 0 failed**.

### Reversal arrows as a trade source + active-indicator strip
- **AUTO TRADE:** new source `reversal` (Reversal plugin). It is OFF unless selected.
  - `reversal_confirmed_only` (True): only R✓, traded on the confirming bar.
  - `reversal_min_score` (60).
  - `reversal_confirm`: "bias" (default) or "score".
  - The HTF bias must agree with the arrow; counter-trend arrows are always blocked.
  - `MasterGate.evaluate(..., waive=)` waives only `trigger` (and `score` in "bias" mode), and only for the reversal
    source. HTF is never waived, and analyser signals are unaffected (tested).
- **Forecast:** new setups `rev_ok` and `rev_any`, using `_reversal_at` with the plugin's own detector settings.
  Detection is lazy per view, so replays without these setups keep their speed.
- **Backtest:** `entry` = analyser | reversal_ok | reversal_any, with `rev_min_score`, `rev_bias`, and
  `rev_sl` = wick | setup. The new `reversal_array()` is causal. Analyser mode is unchanged (regression tested).
- **UI:** an active-indicators strip under the main chart (name, TF, current vote, C / S). The state exposes
  `plugin_votes`.
- **Tests:** `tests/test_reversal_trade.py` has 9 tests, including detector no-look-ahead. The full suite is
  **184 passed, 0 failed**.
- **Demo result (SYNTHETIC, 30 days M5, RR 2):**

  | Entry | Trades | Win % | Total R |
  |---|---|---|---|
  | Analyser READY | 126 | 34% | +3R |
  | Arrow R✓ | 118 | 17% | −58R |
  | Arrow R? + R✓ | 262 | 12% | −169R |

  - Forecast replay agrees: rev_ok PF 0.36 and rev_any PF 0.45, vs structure PF 1.34.
  - Candle patterns on generated data mean nothing, so this is NOT evidence either way.
  - It is the reason this source must be validated on real MT5 history before AUTO use.

### Regime router (trend 1:2 / range arrow 1:1)
- **AUTO TRADE `router`** (OFF by default) uses `_route()` with the live regime (regime.py):
  - TREND labels: trend sources only, in the trend direction, TP = `router_trend_rr` × SL.
  - RANGE labels: reversal arrows only, both directions but not against a clear HTF bias
    (`router_range_against_htf`), TP = `router_range_rr` × SL.
  - Any other regime: no trade (`BLOCKED_REGIME`).
  - In range mode the gate waives trigger / score / setup-confidence, and HTF only when the bias is neutral. This
    applies to the reversal source only; the router has already blocked a clearly opposite bias.
- **Backtest:** `entry="router"` uses `regime_labels()` (causal, one classification per regime-TF bar) and
  `router_array()`, with per-entry RR through `P.rr_over`. `current_setup()` carries the AUTO TRADE router settings.
- **Forecast:** a `router` setup.
- **Tests:** 6 new router tests in `tests/test_reversal_trade.py`.
- **Test isolation fix:** `tests/harness.py` now redirects `autotrade.SETTINGS_FILE` and the settings files of
  execution_guard, exitguard, intermarket, news, news_trader, notify and regime to the temp folder. One new test had
  written `auto_trade_settings.json` into the app folder; that file is removed and was never shipped.
- **Full suite:** 190 passed, 0 failed.
- **Demo (SYNTHETIC, 30 d M5):**

  | Entry | Trades | Win % | Total R | Notes |
  |---|---|---|---|---|
  | Analyser 1:2 | 123 | 32.5% | −3R | |
  | Router | 41 | 34% | −3R | 31 trend (−1R) + 10 range (−2R) |

  Not a conclusion: validate on MT5 history.

### 2 SCREEN + AUTO TF from the chart (UI only)
- **Second chart:** `#chart2` is a second Lightweight Charts instance with its own TF (`rg_tf2`) and toggle
  (`rg_split`). It draws candles, price-pane lines, levels and markers from `/api/chart`, polled every 3 s while shown.
- **AUTO TF:** ⚡ buttons set the AUTO TRADE timeframe through `/api/auto/settings {tf}` after a confirm modal
  (a danger style when AUTO is ON on a live account). The TF bars mark the AUTO TF with ⚡.
- **Pop-out:** `/?tf=XX` opens a window with its own TF that is not persisted.
- **Server:** no change.
- **Checked:** with Playwright on the demo (split, TF switch, AUTO TF → M15 confirmed through `/api/auto`,
  pop-out TF H4); no page errors.

### NEWS TRADE auto (Eugene's request)
- **Enabled:** `news_trader.ALLOW_AUTO = True`. It is still OFF at start, needs ARM (refused otherwise) and a confirm,
  and switches OFF with every news pending cancelled on DISARM.
- **New settings:**
  - `close_before_news` / `close_before_sec` close every open SuperChart trade before the release, once per event
    (`tm.close_everything`, SuperChart scope only).
  - `nobox_mode` = distance | skip, with `nobox_offset` and `nobox_sl`, sets the stops at price ± offset when no
    consolidation box is found.
- **Risk check:** MASTER TRADING and the risk engine's hard blockers are checked before placing. The straddle
  ignores MAX_OPEN / OPPOSITE / EXPOSURE.
- **Tests:** `tests/test_news_trade.py` has 4 tests: off until ARM + ON; close then both stops without a box on
  master and follower; skip mode, MASTER off and DISARM; too early, and close can be turned off.
- **Flaky test fixed:** `test_backtest.test_costs_reduce_results` now asserts the $ result. Commission and swap are
  dollar costs; the R total on time-aligned synthetic data could tie.
- **Full suite:** 194 passed, 0 failed.

### Trade Settings page + MANUAL/AUTO mode card + build check (UI)
- **Settings page:** the AUTO TRADE and NEWS TRADE settings forms moved from the overview cards to a new "Trade
  Settings" page (`#view-tradeset`). The gear buttons navigate there.
- **Mode card:** the new overview card (`#modeCard`) shows MANUAL / AUTO TRADE, ARM / LIVE / PAPER / NEWS chips, the
  AUTO summary, and every account (master + followers) with its connection, lock and enabled state, equity, lot and
  day P/L.
- **Build check:** `superchart.BUILD` is exposed in `/api/state`. `web/app.js` has the same `BUILD` and shows a red
  banner when they differ. This catches the case seen on 2026-09-29: new web files served by a server process still
  running old Python code (router / reversal source missing).

### Error locations + clearer signal list
- **Error locations:** `error_log.where(e)` adds `file.py:line in function` to every reported error. It is used by
  `report()`, the Hub loop, the AUTO TRADE loop and the NEWS TRADE loop (which also print the full traceback), and
  by every failing GET endpoint (now also written to the error log as `API_GET_FAILED`, rate-limited).
  - Reason: a live-only `IndexError: list index out of range` showed in Eugene's console without a location. It
    could not be reproduced on demo data with AUTO (all sources), NEWS TRADE, 2 SCREEN and all chart TFs.
- **Signal list:** the result chip shows the R (`WIN +2R` / `LOSS -1R`) with a tooltip. The legend explains that
  BUY / SELL is the signal direction and WIN / LOSS its outcome. A summary of the last 30 shows W / L / total R, and
  the side chip's tooltip names the source plugin.
- **BUILD:** 2026-09-29.4.

### Trading menu (UI)
- **Moved:** the AUTO TRADE, NEWS TRADE and EXIT GUARD cards moved from the overview's right column to the
  "Trading" page (sidebar item 2, `#view-tradeset`). The controls sit on top in a 3-column grid, with the AUTO / NEWS
  settings forms below.
- **Overview:** the right column keeps the MANUAL / AUTO mode card (with the accounts and a "⚡ Trading →" link),
  the manual Trade box, open trades, verdict and regime.
- **Navigation:** the gear buttons scroll to their settings card. The nav dot shows when AUTO or NEWS is on.
- **BUILD:** 2026-09-29.5.
- **Tests:** 194 passed, 0 failed.

### Why AUTO never traded + live diagnosis
- **Root cause:** the AUTO TRADE "Min score %" (set to 50 by Eugene) was only an extra filter. The master gate still
  required the passing score (65), and the Analyser arrows AUTO trades only appear at the passing score. With a live
  score of 31–46%, no arrow and no entry was possible.
- **Gate:** for kind=auto, the required "score" condition passes when the current analyser score ≥ AUTO min_score
  (> 0). Manual entries keep the passing score.
- **AutoTrader._signals("analyser"):** computes the arrows with `min_score` = the AUTO min score, using a temporary
  params copy under the hub lock. The chart's own setting is untouched (tested).
  - Demo check: the last AUTO arrow moved from 165 bars ago (at 70%) to 20 bars ago (at ≥ 50%).
- **`AutoTrader.diagnose()`:** checks ARM, AUTO, MASTER, session, news, trades today, open trades, HTF bias,
  trigger, score vs threshold, arrow threshold and router, plus the last arrow per source and the last skip. It is
  cached 10 s, returned in `/api/auto` as `diag`, and rendered in the AUTO card.
- **Test stability:**
  - The forecast VP cache now lives on the hub instead of `id(hub)`, which could collide after garbage collection.
  - The replay-reproducibility test uses one pinned data set.
  - The cost test is split into commission/swap (same trades, fewer $) and slippage (every fill exactly 0.20 worse).
- **Full suite:** 197 passed, 0 failed.
- **BUILD:** 2026-09-29.6.

### STRATEGIES: Trend / Pullback / Low Volume Grid (Eugene, 2026-09-29 → 10-01)
- **`strategies.py`:** causal indicator series (EMA20/50, RSI, Stoch, W%R, CCI, MACD, ADX/DI, BB width, Supertrend,
  BOS, 5-bar tick volume).
  - Each strategy has named conditions (ON/OFF), required conditions, its own passing %, RR and SL.
  - Settings live in `strategy_settings.json`. Everything is OFF for AUTO by default.
- **The three strategies:**
  - **TREND:** required EMA alignment + ADX ≥ min. Entries only when the strategy TURNS ok, followed by a cooldown.
  - **PULLBACK:** a dip against the trend that turns back. Blocked when VOLATILE (ATR percentile ≥ 85), and the
    block can be switched off.
  - **LOW VOLUME GRID:** required ADX < max and volume percentile ≤ vol_pct, with a range ≤ max_range (the box when
    found, else the last N bars). It produces `levels` BUY STOPs / SELL STOPs, `step` $ apart, with a fixed SL and
    RR TP.
- **Chart:** `plugins/strategy_signals.py` draws TR / PB arrows with W/L, plus the GRID zone and the levels while the
  grid is alive.
- **AUTO:** sources `st_trend` / `st_pullback` / `st_conso` for the strategies switched to AUTO.
  - The gate waives trigger / score / setup-confidence for the strategy's own conditions. HTF is still required for
    TREND and PULLBACK; the grid is direction-free.
  - The grid is placed through `NewsTrader.place_box()` (levels / step, OCO, expiry, cancelled on DISARM, journaled
    as kind=strategy).
- **Forecast:** `st_*` setups. The grid is two BREAKOUT legs with touch fill; when one fills, the other gets
  `OCO_CANCELLED`.
- **Backtest:** `entry=st_*` uses the strategy's own SL / RR. The grid fills level 1 on touch within expiry_bars; a
  bar touching both sides is skipped.
- **UI:** a STRATEGIES card on the Trading page with live ✓/✗ per condition, the plan (entry / SL / TP or the grid
  prices), the AUTO toggle (with confirm) and settings.
- **Tests:** `tests/test_strategies.py` has 10 tests. The full suite is **207 passed, 0 failed**.
- **Synthetic demo backtest** (30 d M15; NOT evidence):

  | Entry | Trades | Win % | Total R |
  |---|---|---|---|
  | Analyser | 57 | 42% | +15R |
  | Trend | 33 | 33% | 0R |
  | Pullback | 70 | 30% | −17.5R |
  | Grid | 9 | 67% | +9R |
- **Fix:** a duplicate `id="stratCard"` (new card vs the existing strategies status card) is now `stratsCard`.
- **BUILD:** 2026-10-01.1.

### PULLBACK: uptrend + oversold + MFI + doji (Eugene's spec, 2026-10-01)
- **`strategies.series()`:** adds MFI 14 (typical price × tick volume) and candle body / range.
- **New pullback conditions:**
  - `mfi`: MFI ≤ mfi_os (20) in an uptrend / ≥ mfi_ob (80) in a downtrend, within the last 6 bars.
  - `doji`: body ≤ doji_body (0.15) × range and range > 0.25 ATR, on bar i or i−1.
- **Required for pullback:** rsi, mfi and doji (was: turn). The other conditions still count toward the score.
  The new settings `mfi_os`, `mfi_ob` and `doji_body` are in the UI.
- **Test:** an uptrend, a high-volume dip, then a doji gives a BUY; without the doji, no entry; a bearish bias gives
  no BUY. The existing no-look-ahead test covers the new series.
- **Demo (SYNTHETIC, 30 d):** this is a frequency check only, not evidence.

  | Strategy | M5 | M15 |
  |---|---|---|
  | PULLBACK | 95 trades, 18% win, −52.5R | 39 trades, −16.5R |
  | TREND | 72 trades, 47% win, +30R | 30 trades, +3R |
- **Full suite:** 208 passed, 0 failed.
- **BUILD:** 2026-10-01.2.

### PULLBACK + order block · CONSO quiet for ≥ 4 candles (Eugene, 2026-10-01)
- **PULLBACK `ob`:** the dip's low (or high, in a downtrend) within `ob_near` × ATR of an ACTIVE order block on the
  trend side, in the last 6 bars. It uses `exits.order_blocks` + `active_obs`: created at the break, inactive once
  broken, so it is causal. It is required together with rsi, mfi and doji.
- **CONSO `adx_low` and `vol_low`:** both must now hold on every one of the last `quiet_bars` (4) closed candles.
  `quiet_n` (quiet candles in a row) is shown in the UI.
- **Tests:** OB required (none → no entry; an OB at the dip → BUY; a broken OB does not count) and the 4-candle rule
  (dried up 2 candles ago → not yet).
- **Demo (SYNTHETIC, 30 d, M5):** frequency check only.
  - PULLBACK: 30 trades (was 95 before the OB rule).
  - LOW VOLUME GRID: 28 trades.
- **Full suite:** 210 passed, 0 failed.
- **BUILD:** 2026-10-01.3.

### v2.1.0: one indicator per strategy, side panel, BOS trend, 3-part consolidation (Eugene, 2026-10-01)
- **TREND:**
  - New required conditions `bos_m15` (≥ `bos_m15_min` = 2 M15 BOS in the direction within `bos_window` = 32 M15
    bars, and the latest M15 BOS in that direction) and `bos_h4` (latest H4 BOS within `h4_window` = 30 H4 bars).
  - The direction comes from the H4 BOS. The old trend conditions stay available but are OFF by default.
  - `strategies.series(bars, mtf)` builds the BOS state from the real M15 / H4 bars (hub, chart context, backtest
    data, forecast view), else by resampling. Prefix sums + bisect at the strategy bar's close: only higher-TF bars
    closed by then count.
- **CONSOLIDATION:** `vol_low` (1), `adx_low` (2) and `box` (3) are all ON and required; squeeze and atr_low are OFF.
  `count` / `count_of` are shown as x/3. No box → no pendings. OCO is unchanged (news_trader.place_box).
- **Settings migration:** `SCHEMA = 2`. An older file gets the new trend / conso conditions and required lists; the
  other values are kept.
- **Chart:** `plugins/strategy_signals.py` now has three indicators: Strategy · TREND (with B15 / H4 BOS marks),
  Strategy · PULLBACK and Strategy · CONSOLIDATION.
- **Market Overview:** a STRATEGIES side card (CONSOLIDATION x/3, TREND H4 + M15 BOS, PULLBACK 4 checks, NEWS next
  event + live countdown + NEWS TRADE state, AUTO badges). `/api/strategies` adds `news` and `auto`. Read-only.
- **Tests (+8):** BOS rule, no H4 BOS → no trade, real-vs-resampled M15 / H4, M5 chart uses M15 / H4, settings
  migration, conso x/3 and box required, one plugin per strategy, news row.
- **Demo (SYNTHETIC, 30 d):** signal counts only, not evidence.

  | TF | TREND | PULLBACK | CONSOLIDATION |
  |---|---|---|---|
  | M5 | 54 | 1 | 24 |
  | M15 | 54 | 0 | 1 |
- **Full suite:** 216 passed, 0 failed.
- **BUILD:** 2026-10-01.5 (v2.1.0).

### v2.1.1: page-scroll bug, LAYOUT chooser, "near hit" (Eugene, 2026-10-01)
- **Bug:** `scrollIntoView` (clicking a chart signal, "⚙ SETTINGS →") also scrolled the document. With
  `body { overflow: hidden }` the user could not scroll it back, so the sidebar stayed pushed up.
  - Fix: `html, body { overflow: clip }`, a `scrollInside()` helper that scrolls only the nearest scrollable panel,
    and a guard that resets any window scroll.
  - The sidebar scrolls on its own (`.side { overflow-y: auto }`).
- **LAYOUT:** show / hide and reorder the Market Overview cards (localStorage, per browser).
- **Near hit:**
  - `strategies._near()` gives progress %, the completing price level and the $ distance.
  - `_bos(levels=…)` exposes the swing high / low the next BOS needs a close beyond.
  - `/api/strategies` adds `price`, `near` per strategy, and `auto.armed / paper / trading`.
- **Tests:** +1 (near hit). Full suite: 217 passed, 0 failed.

### v2.2.0: 100% per strategy + NORMAL / AGGRESSIVE / SCALP (Eugene, 2026-10-01)
- **100% rule:** `min_pct` is forced to 100, so `ok` means every checked condition is met.
  - SCHEMA 3: old PULLBACK settings are reset to the 4 conditions; stoch, near_ema and turn are no longer defaults.
- **Profiles:**
  - `strategies.PROFILES` and `apply_profile()` set the conditions (exactly the required ones), the numbers,
    `htf_filter` and the AUTO TF.
  - The strategy AUTO flags are kept; nothing is switched on.
  - `POST /api/strategies/profile`.
  - A settings save that changes values marks the profile "custom".
- **TREND TFs:** `bos_ltf` / `bos_htf` (M5, M15, H1, H4). `series()` builds the BOS state for all four (real bars
  from the hub, the chart, the backtest data or the forecast; resampled otherwise).
- **HTF filter OFF:**
  - AUTO passes `htf:range` + `htf:own` for strategy signals.
  - The gate then skips the analyser HTF-bias requirement and the STRUCTURE_CONFLICT block, for `st_*` sources only.
  - Risk, execution, session, news, duplicates and max trades are unchanged.
- **Demo (SYNTHETIC, 30 d):** signal counts only, not evidence.

  | Profile | TREND | PULLBACK | CONSOLIDATION |
  |---|---|---|---|
  | NORMAL (M15) | 57 | 1 | 0 |
  | AGGRESSIVE (M15) | 61 | 17 | 3 |
  | SCALP (M5) | 170 | 0 | 29 |
- **Tests (+5):** 100% rule, profiles keep AUTO off, scalp uses M5 / H1 BOS, schema-2 migration, HTF filter on vs
  off through the real gate. Full suite: 222 passed, 0 failed.

### v2.3.0: strip above the chart, strategies-only right column, "why no trade" (Eugene, 2026-10-01)
- **Eugene's screenshot:** PULLBACK showed READY SELL 4/4 with AUTO TRADE ON + ARMED, but no trade. The strategy's
  badge was **MANUAL**: its AUTO flag was off, so AUTO never looks at it.
  - Second rule: AUTO trades a strategy only on the bar where it turns READY (fresh), so a READY that has been on for
    several bars is never entered late.
  - Both are now explained under the strategy.
  - `/api/strategies` adds `last_signal {t, side, fresh, bars_ago}`.
- **Strip:** reuses the ARM / AUTO switches' own handlers (same confirms) and the profile endpoint. The manual
  ticket (`#tradeCard`, `#groupsCard`) moved into a pop-over.
- **Layout:**
  - New default: only STRATEGIES on the right (`reygun_layout_v2`).
  - Hiding is a `<style>` rule keyed on `data-lay`; a class was removed by code that rewrites `className`.
- **Tests:** +1. Full suite: 223 passed, 0 failed.

### v2.3.1: ARM merged into the AUTO / NEWS / ticket confirms (Eugene, 2026-10-01)
- **Eugene:** "alisin mo na itong arm trading".
- **UI:** `ensureArmed()` arms right after the user confirms AUTO ON, NEWS TRADE ON or a BUY / SELL ticket. The ARM
  switch is hidden.
- **Server side unchanged:**
  - Orders still need `armed`, and it is false at start.
  - AUTO and NEWS turn off when disarmed (emergency stop, halt).
  - A HALT is never cleared silently: resuming asks first.
- **Diagnosis label:** "ARM" is now "Trading".
- **Tests:** 223 passed, 0 failed.

### v2.4.0: strategies only, all green = trade, CONSO on ADX < 20 (Eugene, 2026-10-01)
- **Score:** the confluence score KPI is hidden.
- **`strategies_only`:**
  - `autotrade.DEFAULTS.strategies_only = True`: `_plugins()` returns only `st_*`.
  - The diagnosis shows a "Strategies" line instead of HTF / trigger / score.
- **`AutoTrader._strategies()`:**
  - Runs on every new bar and every 15 s.
  - For each AUTO strategy it takes the last READY spell (`signals()`). If the last closed bar is still all green
    for the same side and the spell has not been handled, it trades with the CURRENT bar's entry / SL / TP or
    pending plan.
  - `_mark_existing()` no longer hides strategy spells, so a READY that was green when AUTO came on is taken.
  - The gate's duplicate check (`signal_id` from the spell time) keeps it to one trade per spell.
- **CONSO:**
  - SCHEMA 4: the conditions are `adx_low` only, `quiet_bars` 1, `range_bars` 12.
  - `max_range` 0 = no limit (the box detector gets 1e9).
  - Volume and box remain optional extra conditions.
- **Tests (+2, others moved to a `legacy_conso()` custom setting):** 225 passed, 0 failed.

### v2.4.1: PULLBACK order block must hold on M15 (Eugene, 2026-10-01)
- **`strategies.ob_hold(S, bars, i, d, cfg)`:**
  - Builds order blocks on `ob_tf` (M15; real bars, else resampled) and looks at the last ob_tf bar CLOSED by the
    strategy bar's close.
  - Needs an active OB on the trend side (bearish for SELL, bullish for BUY) that the last 6 bars tagged
    (± ob_near × ATR), with the close still inside / below (SELL) or above (BUY) it.
  - "Active" = no ob_tf close through it (`exits.order_blocks` broken index).
- **SL:** beyond the OB (allowed up to 6 × ATR, else the ATR SL).
- **Demo (SYNTHETIC, 30 d, pullback signals):** NORMAL 1, AGGRESSIVE 16, SCALP 0. Frequency only, not evidence.
- **Tests (+1):** holds → OK; M15 close above → not OK; too far → not OK; wrong side → not OK; SL beyond the OB.
  Full suite: 226 passed, 0 failed.

### v2.5.0: strategy panel (Eugene, 2026-10-01)
- **Editor:** ⚙ per strategy in the side card opens an editor.
  - Lists the ON conditions (live met) and the OFF ones (＋ add), plus the value inputs (same list as the Trading
    page).
  - It posts `{conditions, required = ON keys, values}` to `/api/strategies/settings`, so the profile becomes
    "custom". AUTO is not touched.
- **New conditions:** `rsi_side`, `ema200` (trend), `bb_touch`, `engulf` (pullback), `rsi_mid`, `inside_bb` (conso).
  The series adds `bb_up / bb_lo / ema200`.
- **SCHEMA 5:** resets conso `quiet_bars / adx_max / range_bars / max_range` from older files. Eugene's screen still
  showed ADX < 25 and "0/3 quiet candles" from the v2.2 AGGRESSIVE.
- **Tests (+1):** 227 passed, 0 failed.

### v2.5.1: trend kontra + card follows the setup (Eugene, 2026-10-01)
- **Kontra conditions:**
  - `not_extreme` (RSI < rsi_hi for BUY / > rsi_lo for SELL) and `no_ob_block` (`ob_block()`: no active opposite
    OB on ob_tf within ob_block_atr × ATR, or price inside it). An ob_tf close through the OB deactivates it.
  - Both are OFF by default: on the steady-trend test data RSI ≥ 70 at 87 of 95 BOS-ready bars, so ON by default
    would almost stop TREND entries ("importante mag-trade muna").
- **Side card:** the BOS chips and the BOS hint only show when those conditions are in the setup. Eugene had
  removed them and could not get rid of the chips.
- **Tests (+1):** 228 passed, 0 failed.

### v2.6.0: open-MT5 finder (Eugene, 2026-10-01)
- **Problem:** on the VPS (several MT5 installs) `mt5.initialize()` without a path attached to / started a different
  terminal than the open one, and adding followers by hand (terminal64.exe path) was unclear.
- **`mt5_detect.py`:**
  - Lists running terminal64.exe (PowerShell CIM, wmic fallback).
  - Probes each in its OWN spawned process (account, server, balance, Algo Trading, symbol + suggestions), so the
    app's master connection is untouched.
  - `pick_master()`: login > the only one with the symbol > the only one open.
- **Startup (live, no path):** auto-pick and save the path to accounts.json. Ambiguous → warning, choose on the
  Accounts page.
- **API / UI:** `GET /api/mt5/detect` and the Accounts table with MASTER / + FOLLOWER (fills the form; Save applies,
  and the master reconnects without a restart).
- **Tests (+2):** 230 passed, 0 failed. MT5 itself is Windows only, so the probe is not exercised here.

### v2.6.1: profit protection ON by default (Eugene, 2026-10-01: "dapat ON na agad, kailangang-kailangan")
- **New defaults:** `position_manager.DEFAULTS` is enabled with be_r 1.0, be_offset 0.20 and profit_lock [[1.25, 0.5]].
  Trailing and partial close stay off (0.01 lot cannot be split).
- **`load_settings()`:** a file without `version` that held only the old OFF defaults gets the new ones. A file the
  user set (any BE / lock, or saved by v2.6.1 with version 2) is kept.
- **UI:** 🛡 chip on the strip (polled every 15 s) → Command Center position manager card.
- **Tests (+1):** 231 passed, 0 failed.

### v2.6.2: profit protection per profile (Eugene, 2026-10-01)
- **Why:** the v2.6.1 default (BE 1R, lock at 1.25R) never fires for SCALP, whose TP is 1R.
- **Change:**
  - `PROFILES[*].protect` holds the profit protection for each profile.
  - `/api/strategies/profile` passes it to `positions_mgr.update_settings` (be_r, be_offset, profit_lock only).
  - The profile confirm shows it.
- **Test:** BE and locks sit below the profile's TP. Full suite: 232 passed, 0 failed.

### v2.6.3 / v2.6.4: straddle not blocked by structure, skipped spells retried, balances on the strip (Eugene, 2026-10-01)
- **Eugene's log:** "AUTO skip BUY/SELL STOP 4188.26 (st_conso, score 100%): [BLOCKED_STRUCTURE…". The gate checked the
  straddle as its BUY leg against a Bearish HTF. Pending straddles now pass `htf:own` (STRUCTURE_CONFLICT and HTF
  waived; risk, execution, session and news unchanged).
- **Retry:** `_strategies()` no longer marks a spell done before it trades.
  - A skipped spell is retried at most once a minute while all green.
  - `is_duplicate(traded_only=True)` makes only a spell that became a trade a duplicate.
  - The same skip reason is logged once.
- **UI:** `/api/strategies` adds `last_auto` per strategy (the skip reason on the card). The strip shows M / F
  accounts with a connected dot, balance and equity.
- **Tests (+3):** 234 passed, 0 failed.

### v2.6.5: keep-alive + crash log (Eugene, 2026-10-01: "na-di-disconnect, baka biglang may news, ang panalo biglang matalo")
- **`runstate.py`:**
  - `runtime_state.json` is written every 3 s (armed, AUTO, NEWS) and marked clean on a normal stop.
  - `--resume` (given only by the .bat restart loop) restores those after an unclean stop of less than 600 s.
  - A HALT is never cleared, and a normal double-click start never resumes.
- **Crash log:** `sys.excepthook`, `threading.excepthook` and faulthandler write to `crash_log.txt`.
- **.bat:** live and paper loop with a 5 s delay; restarts use `--no-window` (the open page reconnects).
- **Demo check:** armed + AUTO, kill -9, restart with `--resume` → "RESUMED after an unexpected stop: trading ON,
  AUTO".
- **Tests (+2):** 236 passed, 0 failed.
- **Not reproduced here:** why Eugene's server stopped. His screenshot still showed v2.6.2. `crash_log.txt` will
  hold the reason next time.

### v2.6.6: one-step AUTO start with reasons + optional AUTO on start (Eugene, 2026-10-01: "naka-ilang ON na ako, ayaw mag-AUTO")
- **Root cause not reproduced:** in demo the old flow (arm, then enable) worked. On Eugene's VPS the reason was
  silent: arm / enable failures were only shown in some paths.
- **`AutoTrader.start()`:** arms (unless HALTED), enables, and returns can_trade / warning.
  - The UI shows every failure.
  - It re-checks after 4 s and shows the last AUTO log line if AUTO went off again.
- **`auto_on_start`:** user setting, default False; applied after start-up (not with `--resume`, which has its own
  restore).
- **Load:** `/api/strategies` costs about 0.1 s on 2000 bars, so it is not the reason AUTO stays off.
- **Tests (+1):** 237 passed, 0 failed.

### v2.6.7: AUTO on start ON by default (Eugene, 2026-10-01: "on mo na as default, ang hirap i-ON at laging na-disconnect")
- **This changes the original "AUTO is OFF at start" rule, at the owner's explicit request.**
  - `autotrade.DEFAULTS.auto_on_start = True`.
  - `SETTINGS_VERSION` 2: older files get True once; a file saved by v2.6.7 with False keeps False.
- **Unchanged:**
  - Only strategies set to ● AUTO trade, and only when all their conditions are green.
  - Gates: risk, session, news, max trades, spread.
  - A HALT is never cleared; read-only mode never trades.
- **Tests (+1):** 238 passed, 0 failed.

### v2.7.0: order flow in the strategies + Buyers vs Sellers on the chart (Eugene, 2026-10-02)
- **`strategies._orderflow()` / `of_pressure()`:**
  - Per strategy bar (buy, sell) from `orderflow.CURRENT.bar_stats(tf)` (MT5 ticks, tick rule).
  - Without tick data (backtest) the conditions are not met.
- **New conditions:** `of_buyers` (trend), `of_turn` (pullback), `of_balanced` (conso). All OFF by default; settings
  of_bars / of_min.
- **Chart:** `plugins/flow_strength.py` (FlowStrength, default ON) marks side changes at ±15%. `/api/strategies` adds
  `orderflow.pressure`.
- **Server:** `_send()` swallows ConnectionAborted / Reset / BrokenPipe (WinError 10053 spam in Eugene's console when
  the page aborts a poll).
- **Demo:** synthetic ticks are balanced (±10%), so no markers appear there; covered by tests with injected flow.
- **Tests (+2):** 240 passed, 0 failed.

### v2.7.1: order flow ON per strategy (Eugene, 2026-10-02: "i-ON mo na lang … i-designate ang gamit ng order flow sa bawat strategy")
- **Requirement:** `OF_KEY` (trend `of_buyers`, pullback `of_turn`, conso `of_balanced`) is required by default and
  in all profiles.
- **SCHEMA 6:** appends the key to an older `required` list; the rest is kept. Conso conditions now reset only from
  schema < 4.
- **No tick data:** the condition passes ("not judged") and the card says so.
- **Status:** `/api/strategies` → `strategies[n].of` → one line per card.
- **Tests (+1, 7 updated):** 241 passed, 0 failed.

### v2.7.2: all ON at start + follower skip reasons (Eugene, 2026-10-02)
- **All ON:**
  - Strategy DEFAULTS auto=True; SCHEMA 7 sets auto once for older files.
  - `autotrade.news_on_start` (default True): after `auto_on_start`, `newstrader.set_enabled(True)`.
- **Follower:**
  - `TradeManager.follower_skip_reason()` replaces the silent filter in `open()` and in `news_trader._accounts()`.
  - Every reason is noted (event log) and stamped (journal lifecycle).
  - A paused follower (reconciliation) previously skipped every trade without a word; that is the most likely cause
    of "master traded, follower did not".
- **Strip:** chips show the follower state; PAUSED → `/api/reconciliation/resume`, LOCKED → `/api/trade/unlock`.
- **Tests (+2):** 243 passed, 0 failed.

### v2.7.3: copy manual master trades (Eugene, 2026-10-02)
- **Config:** `copy_manual` (default True) in `accounts.json`; kept by `load_config` / `update_config`; Accounts page
  checkbox.
- **Read:** when on, the master reads ALL positions; `master.positions` = magic `MAGIC`, `master.manual` = magic 0 on
  the master symbol. `Account.pos()` also searches `manual`, so sync() / reconciliation find the master leg.
- **Copier:** `TradeManager.copy_manual()` runs each loop before `sync()`. The first read marks open trades as seen
  (not copied). A new manual ticket becomes a `Group(kind="copy")`; follower legs are opened with
  `follower_skip_reason()`, comment `RGSC-<gid>`; journal kind `manual_mt5`.
- **Position manager:** skips `kind == "copy"` groups.
- **PaperBackend:** positions keep their own magic (a test can add a manual one); `magic_only` / symbol filters as
  in mt5_ops.
- **Tests (+6, `test_copy_manual.py`):** copy + close mirrored, SL move + partial mirrored, EA / other symbol /
  pre-existing ignored, switch off / SuperChart trade not double-copied, not armed = not copied.

### v2.7.4: manual copy without arming (Eugene, 2026-10-02)
- `copy_manual()` no longer calls `can_trade()` (armed / Algo Trading on the master are irrelevant: the trade is
  already open on the master). Only a LOCKED master stops it. `sync()` already ran without arming.
- **Tests:** `test_copied_even_when_not_armed`, `test_locked_master_not_copied` (replaces not-armed test).

### v2.7.5: Frankfurt session + why the strategies did not trade (Eugene, 2026-10-03)
- **Data:** XAUUSD M5, 2026-03-26 .. 2026-09-25, from the getdata-finance free sample on GitHub. Resampled to M15 / H4.
  Order flow is not judged offline (no ticks), so the counts below are an upper bound.
- **Strategy frequency (NORMAL, M15):**
  - TREND: 6.8 signals / week, 37% win at 1:2, +22R over 6 months.
  - CONSO: about 21 pending plans / week.
  - PULLBACK: **0 signals in 6 months.** RSI ≤ 40, MFI ≤ 20, doji and OB never line up together in an uptrend
    (all four together: 0 bars; RSI + MFI: 22 bars).
  - Looser pullback variants still lose. RSI + OB + order flow alone: 1.9 / week, 44% win, +5R. Not applied; it is
    the user's call.
- **Paper replay of Sep 15-25 through the real app** (patched to sim time, `news_unknown=allow`). TREND traded
  twice. It was blocked by:
  - session (London / NY only),
  - `required: Displacement` (regime BREAKOUT) → fixed,
  - max open 1.
- **Live risk:** with the default `news_unknown=block`, a calendar that has not loaded for 12 h blocks every AUTO
  trade (BLOCKED_NEWS / NEWS_UNKNOWN).
- **Gate:** `trade_gate`: for `st_*` sources every decision `missing_required` except `htf` is skipped.
- **Sessions:** "Frankfurt" [6, 14] UTC in market_hours, the chart plugin (frankfurt_start / _end), superchart
  defaults, analytics and the AUTO sessions checkboxes.
- **Tests (+1, 1 updated):** strategy trade not blocked by the analyser list (still blocked against the HTF bias);
  06 UTC = FRANKFURT.

### v2.7.6: CONSO manual, more sessions; RANGE strategy test (Eugene, 2026-10-03)
- **Migrations:**
  - strategies SCHEMA 8: `conso.auto=False` once (DEFAULTS too).
  - autotrade SETTINGS_VERSION 3: "Asia" + "Frankfurt" appended once to a saved non-empty sessions list.
  - Default sessions: Asia / Frankfurt / London / New York.
- **Session test of TREND (NORMAL, M15), by entry time:**

  | Hours (PH) | Trades | Win | R |
  |---|---|---|---|
  | 5 AM – 2 PM (Asia) | 64 | 39% | +11 |
  | 2 – 3 PM (Frankfurt only) | 10 | 50% | +5 |
  | 3 – 8 PM | 33 | 27% | −6 |
  | 8 – 10 PM | 21 | 33% | 0 |
  | 10 PM – 12 MN | 17 | 47% | +7 |
  | 12 MN – 5 AM | 34 | 38% | +5 |

  CONSO OCO lost in every bucket.
- **RANGE (2 equal tops + 2 equal bottoms, TP at the other side) prototype:**
  - Box from `conso.detect`, scaled to ATR (tol 0.3 ATR, size 1.5-6 ATR). Spread $0.25 per trade.
  - SL 10 pips ($1): M5 9% win, -$91; M15 2% win, -$98. A $1 stop is inside gold's noise (M5 ATR about $5,
    M15 about $9).
  - Best: M15 limit at the edges with SL 1 ATR: 105 trades, 31% win, +$61 per 0.01 lot. Also M15
    close-back-inside + ADX < 20 with SL 1 ATR: 37 trades, 51% win, +$58.
  - Not built yet: waiting for Eugene's choice of SL.
- **Tests (+1, 2 updated):** CONSO manual once / kept when switched back; sessions migration once.

### v2.7.7: TREND SL at the last swing (Eugene, 2026-10-03)
- **Rule:**
  - `strategies.last_swing(bars, i, d)` finds the latest confirmed pivot (3 bars each side) in the last 60 bars,
    below the close (BUY) or above it (SELL).
  - SL distance = |close − swing| + 0.1 ATR, clamped to 0.5–4 ATR.
  - Settings `sl_mode` ("swing" | "atr"), `swing_buf`, `swing_min`, `swing_max`.
- **Profiles and migration:** NORMAL uses swing; AGGRESSIVE / SCALP use atr. SCHEMA 9 sets swing once for a
  NORMAL file.
- **Test (179 signals, Mar–Sep 2026):**

  | Setup | Win | R |
  |---|---|---|
  | ATR SL, TP 2R | 37% | +22 |
  | ATR SL, TP 3R | 29% | +29 |
  | ATR SL, TP 4R | 24% | +36 |
  | Swing SL, TP 2R | 36% | +16 (+25.7 with NORMAL protection, DD 10R) |
  | Swing SL, TP 2.5R | 33% | +25 |
  | Swing SL, TP 3R | 25% | +2 |

  The average swing SL is about $29 against $14 for the ATR SL.
- Eugene chose swing SL + TP 2R. The NORMAL protection stays as it is.
- **Tests (+2):** swing finder and migration; SL inside 0.5–4 ATR.

### v2.7.8: refinement with a train / test split (Eugene, 2026-10-03)
- **Method:** XAUUSD M5, 2026-03-26 .. 09-25 (getdata-finance sample).
  - Strategy bars M15; exits walked on M5 (SL first inside one M5 bar). Spread $0.25 per trade.
  - TRAIN = before 2026-07-01, TEST = after. Settings are picked on TRAIN; only configs positive in BOTH halves
    are kept, ranked by the worse half's expectancy.
- **TREND** (grid: BOS min 1/2, H4 window 30/12, filters, SL swing/ATR, RR 1.5/2/3, market vs retest, protection).
  - Recurring winners: H4 window 12 and the `not_extreme` KONTRA.
  - Chosen (Eugene's swing SL, 2R, NORMAL protection): TRAIN 101 tr +25.2R, TEST 81 tr +28.2R, DD 10.6R.
  - Retest-and-hold entries had too few trades (58) and were weak on TRAIN.
- **PULLBACK** (2880 configs; 846 positive in both halves, which says the EMA-trend pullback has a general edge).
  - Chosen to keep Eugene's idea (RSI + OB): RR 2, own SL, NORMAL protection. TRAIN 26 tr +9.1R, TEST 24 tr +8.4R,
    DD 3.6R.
  - Alternative: turn + Bollinger touch, RR 2: TRAIN +14.1R, TEST +12.0R.
- **RANGE** (288 configs, 45 positive in both halves, so the edge is thin).
  - Chosen: M15 LIMIT, SL 1.5 ATR, TP at the middle of the range. TRAIN 62 tr +9.3R, TEST 59 tr +10.4R, 60% win.
  - No M5 config survived.
- **Verification:** the terminal's own `signals()` with the new NORMAL reproduce the lab numbers exactly
  (TREND 182 tr +53.4R, PULLBACK 50 tr +17.5R).
- **Migration:** SCHEMA 10 (NORMAL, once): trend `h4_window` 30 → 12, `not_extreme` required; pullback drops
  `mfi` / `doji`, rr 1.5 → 2.
- **Tests:** a `legacy()` helper keeps the rule tests written for the old defaults; +1 migration test.
- **Caveat:** order flow is not judged offline, and 6 months is a short sample.

### v2.7.9: Test signal - terminal to MT5 connectivity (Eugene, 2026-10-04)
- `mt5_ops.signal_test_send` / `signal_test_remove`: a never-filling BUY LIMIT (min lot, 80% of bid, magic 20260925,
  comment RGSC-TEST), checked with orders_get, then deleted and checked gone. Retcodes are explained (10018 market closed,
  10027 Algo Trading OFF, 10026 server disabled, ...).
- `TradeManager.signal_test(hold)`: all accounts in parallel; the hold happens between the two calls, outside the
  MT5 / worker locks. Refused while halted. Paper backend simulates it.
- API `/api/trade/signaltest`; UI button "📡 Test signal" with a confirm dialog.
- Tests: `tests/test_signal_test.py` (5).

### v2.8.0: MASTER in the terminal + CVD + delta profile (Eugene, 2026-10-04)
- `strategies.py`: TREND conditions `m5_struct` (latest M5 BOS = direction; not judged without M5 bars) and `cvd`
  (`cvd_change`: prefix sums over the M5 bars, bisect on bar close times - no look-ahead). Profile `master`
  (+ `autotrade` overrides), `master_settings()`, `migrate_to_master()` (NORMAL only, once via AUTO TRADE v4).
- `autotrade.py`: settings v4 (M15, max_open 3, max_per_day 0, no_opposite off, master_mode). `_consider`: own-trade
  rule for strategy sources; `_reversal`, `_early_exit`, `_trend_cfg` / `_last_trend_win` (re-entry after a win,
  from the journal `strategy` field). Orders carry `meta.strategy`.
- `trading.py`: `Group.source`; master daily $20 / $60 once (`master_preset`); partial-close guard (lot step / min lot).
- `news_trader.py`: CONSO fills tagged `st_conso`; `cancel_strategy_side`, `strategy_box_live`.
- `position_manager.py`: TREND secure partial; CONSO skipped when conso_protect is off.
- `plugins/delta_profile.py`, Profile split drawing in `web/app.js`; AUTO TRADE MASTER block in the UI; TREND editor fields.

### v2.8.1: leftover test order sweep (Eugene, 2026-10-05)
- `TradeManager._sweep_test_orders`: on every refresh, any `RGSC-TEST` pending order older than 30 s is cancelled
  (paused while `signal_test` runs). Test: `test_leftover_test_order_is_swept`.

### v2.8.1: copy every master trade + leftover test order sweep (Eugene, 2026-10-05)
- `trading.py`: `copy_ea` (default on) + `copy_magics` ranges (`parse_magics`, empty = all); `_copyable(magic)`
  decides which master positions the copier follows (manual 0 with copy_manual, EA magics with copy_ea, never ours).
- `TradeManager._sweep_test_orders`: an `RGSC-TEST` order pending > 30 s is cancelled (not during a running test).
- Tests: `test_copy_manual.py` (+4), `test_signal_test.test_leftover_test_order_is_swept`.

