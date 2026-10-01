# REYGUN SuperChart: changelog

## v2.6.5 (build 2026-10-01.20)
- **Keep-alive:**
  - `run_superchart_live.bat` / `run_superchart_paper.bat` restart SuperChart by themselves 5 s after it stops
    (crash, MT5 hiccup).
  - After an unexpected stop of less than 10 minutes, the restart switches back ON what was ON (trading, AUTO,
    NEWS TRADE), so open trades keep their profit protection.
  - A normal start still begins with AUTO OFF, and a HALT is never cleared.
- Every crash is written to `crash_log.txt` with the error.
- The page reconnects by itself while the server restarts.

## v2.6.4 (build 2026-10-01.19)
- **Fix: CONSOLIDATION READY but no pending orders.**
  - The BUY STOP leg was blocked as "structure against this side" (HTF Bearish): `[BLOCKED_STRUCTURE]` in the event
    log.
  - A straddle has no direction, so the HTF bias / structure conflict no longer applies to it.
- **Fix: a READY strategy that was skipped once (session, spread, max open…) was never tried again.**
  - It is now retried every minute while it stays all green, and traded once.
  - The strategy card shows why it did not enter ("⛔ lahat green pero HINDI pumasok: …").
- **Master and follower balances above the chart:** each has a green / red dot (connected or not), account #,
  balance and equity.

## v2.6.2 (build 2026-10-01.17)
- Each profile also sets the profit protection to fit its own TP:
  - NORMAL (1:2): BE 1R, lock 0.5R at 1.5R and 1R at 1.8R.
  - AGGRESSIVE (1:1.5): BE 1R, lock 0.5R at 1.25R.
  - SCALP (1:1): BE 0.6R, lock 0.3R at 0.8R (before the 1R TP).

## v2.6.1 (build 2026-10-01.16)
- **Profit protection ON from the start:**
  - Position manager defaults to break-even at +1R (SL → entry + $0.20) and lock +0.5R at +1.25R.
  - Older settings that were still the OFF defaults get it too; your own choices are kept.
- 🛡 chip above the chart shows what protects the trade; click it to change.

## v2.6.0 (build 2026-10-01.15)
- **Accounts → "🔍 Hanapin ang bukas na MT5":**
  - Lists every OPEN MT5 on the PC / VPS with its account, server, balance, Algo Trading state and whether it has
    the symbol.
  - One click: "Gamitin bilang MASTER" or "+ FOLLOWER", then Save accounts.
- **At start, with no master terminal chosen yet,** the app attaches to the OPEN MT5 with your login / symbol (and
  remembers it) instead of letting MetaTrader5 start another terminal.

## v2.5.1 (build 2026-10-01.14)
- **TREND "kontra" (optional, ＋ idagdag in the ⚙ strategy panel):**
  - Not overbought (BUY) / oversold (SELL).
  - No order block in the way. A BUY is blocked under a bearish M15 OB; once an M15 close breaks it, the BUY may
    go.
- Fix: removed conditions (e.g. H4 / M15 BOS) no longer show on the strategy card, and its hint follows the
  setup.

## v2.5.0 (build 2026-10-01.13)
- **STRATEGY PANEL (⚙ next to each strategy on the right):**
  - See the setup with live ✓ / ✗.
  - "✕ alisin" or "＋ idagdag" an indicator, edit the values, then Save. The profile becomes CUSTOM.
- **New indicators to add:**
  - TREND: RSI side of 50, EMA200.
  - PULLBACK: Bollinger band touch, engulfing candle.
  - CONSOLIDATION: RSI 40–60, inside the Bollinger bands.
- Fix: a CONSOLIDATION saved by the old AGGRESSIVE (ADX < 25, 3 quiet candles) is reset to ADX < 20 right away.

## v2.4.2 (build 2026-10-01.12)
- The version and build now sit at the top of the sidebar, under the logo.

## v2.4.1 (build 2026-10-01.11)
- **PULLBACK order block on M15:**
  - SELL: in a downtrend the rally goes up into a bearish order block ABOVE (overbought), and that OB is not broken
    on M15 (no M15 close above it).
  - BUY: the mirror, a dip into a bullish OB below that holds.
  - The SL now goes just beyond the order block.
- The OB timeframe is a setting (`ob_tf`, default M15).

## v2.4.0 (build 2026-10-01.10)
- **Per strategy only:**
  - The confluence score card is gone.
  - AUTO now trades only the strategies set to AUTO (`strategies_only`, ON by default). The analyser / breakout
    sources come back only when you turn it OFF in AUTO settings.
- **All green → trade:**
  - When every checked condition of an AUTO strategy is green on the last closed bar, AUTO enters within ~15 s, at
    current prices.
  - This includes a READY that was already green when AUTO was switched on.
  - One trade per READY spell.
- **CONSOLIDATION = ADX < 20 alone:** the BUY STOP / SELL STOP go in right away, around the box when there is one,
  else around the last 12 bars. Volume and box can still be checked as extra conditions.
- **Choosing which strategy AUTO trades:**
  - "AUTO: ○ TREND ○ PULLBACK ○ CONSO" above the chart.
  - The same ○ MANUAL / ● AUTO switch next to each strategy on the right.

## v2.3.1 (build 2026-10-01.9)
- **No separate ARM step:**
  - The confirm of ⚡ AUTO, NEWS TRADE or a BUY / SELL ticket also turns trading on. The ARM switch is gone.
  - Still unchanged: AUTO is OFF when the app opens, every order has its confirm, and a HALT (daily loss / target /
    emergency stop) is never resumed without asking.
- The sidebar shows TRADING ON / OFF / HALTED, and the strip shows LIVE or PAPER.

## v2.3.0 (build 2026-10-01.8)
- **Strip above the chart:**
  - **✋ MANUAL / ⚡ AUTO** and **ARMED / DISARMED** (same confirms as before).
  - **SCALP / NORMAL / AGGRESSIVE**.
  - **BUY / SELL ▾**, which opens the manual ticket and the open SuperChart trades.
  - A line showing what AUTO trades now.
- **Right side shows only the 4 strategies** by default, with no scrolling. Other cards come back via ⚙ LAYOUT.
- **Click MANUAL / AUTO on a strategy** to switch its AUTO flag (turning it on asks first).
- **Why a READY did not trade**, shown under the strategy:
  - "READY pero MANUAL", "READY pero OFF ang AUTO TRADE" or "DISARMED".
  - "nag-READY N bar na ang nakalipas": AUTO enters only on the bar where it turns READY, never a late entry.
- Fix: the card show / hide rule can no longer be undone by other code.

## v2.2.0 (build 2026-10-01.7)
- **100% per strategy:** a strategy enters only when EVERY condition you checked is met (no passing % any more).
  The PULLBACK default is just the 4: oversold, MFI, doji and order block.
- **Profiles** (Trading → STRATEGIES): **NORMAL**, **AGGRESSIVE**, **SCALP**. One click sets the three strategies
  and the AUTO TF. AUTO stays OFF; there is a confirm. "CUSTOM" shows when you change the numbers yourself.
  - NORMAL: M15 entry, 2 BOS M15 + 1 BOS H4, all 4 pullback conditions, box quiet 4 candles, 1:2.
    The analyser HTF bias must agree.
  - AGGRESSIVE: 1 BOS M15 + 1 BOS H4, pullback without the doji, box quiet 3 candles, ADX < 25, 1:1.5.
    Follows the strategy's own trend.
  - SCALP: AUTO TF M5, 2 BOS M5 + 1 BOS H1, SL 1×ATR, 1:1, one grid level, shorter expiry. Follows the strategy's
    own trend.
- TREND: the entry TF / trend TF of the BOS rule can be chosen (M5, M15, H1, H4).
- New "HTF bias of the analyser" switch: OFF = the strategy's own trend (e.g. the H4 BOS) decides the direction.
- The side panel shows the profile and the strategy's own conditions (x/N).

## v2.1.1 (build 2026-10-01.6)
- **Bug fix:** the whole page (sidebar included) could slide up after clicking a signal on the chart, with no way
  back. The page itself can no longer scroll; only the sidebar, the main area and the panels do. The sidebar now
  scrolls on small screens (e.g. 1366×768).
- **⚙ LAYOUT** (top bar): choose which Market Overview cards are shown and their order on the right; "STRATEGIES +
  Trade lang" for a clean view. Saved in the browser.
- **STRATEGIES card** is now first on the right:
  - AUTO TRADE ON/OFF, ARMED and LIVE/PAPER at the top, and an AUTO / MANUAL badge per strategy.
  - A progress bar per strategy, MALAPIT (≥ 60%) / READY, and the price that completes it with how far it is
    (next M15 BOS level, the H4 BOS level, or the nearest BUY / SELL STOP of the box).
  - Refreshes every 5 s.

## v2.1.0 (build 2026-10-01.5)
- One chart indicator per strategy in LAYERS: **Strategy · TREND**, **Strategy · PULLBACK**,
  **Strategy · CONSOLIDATION**.
- **STRATEGIES side card** on the Market Overview: CONSOLIDATION (x/3), TREND (H4 + M15 BOS), PULLBACK (4 checks),
  NEWS (next event + countdown, NEWS TRADE on/off), with AUTO badges.
- **TREND** = 2 BOS on M15 + 1 BOS on H4 in the same direction (real M15 / H4 bars, no look-ahead).
- **CONSOLIDATION** = low volume (1) + ADX < 20 (2) + box (3), all required. The BUY STOP / SELL STOP sit around the
  box before it breaks; one fill cancels the other (OCO).
- Older strategy settings get the new TREND / CONSOLIDATION rules automatically; your other values are kept.

## v2.0.0 (build 2026-10-01.4)
**Trading**
- Trading menu (⚡): AUTO TRADE, NEWS TRADE, EXIT GUARD and STRATEGIES on one page, with the settings below.
- STRATEGIES, each with its own indicator set and AUTO switch:
  - TREND: market entry with the trend, 1:2.
  - PULLBACK: uptrend + oversold + MFI + doji + order block, giving a BUY (mirrored for SELL).
  - LOW VOLUME GRID: ADX < 20 + low volume for at least 4 candles, then BUY STOP / SELL STOP (OCO).
- NEWS TRADE: closes open SuperChart trades before the news, then places BUY STOP / SELL STOP (box, or price ± $),
  with OCO and expiry.
- Regime router: a trend regime takes trend signals at 1:2; a range regime takes reversal arrows at 1:1.
- Reversal arrows (R✓ / R?) as an AUTO source.
- The AUTO "Min score %" is the real threshold, for both the gate and the arrows.
- "Hinihintay:" checklist in the AUTO card (why there is no trade yet).

**Analysis**
- Score explanation ("BAKIT X% ANG SCORE?"), plus the neutral / per-indicator options.
- Forecast comparison: Structure, Structure + POC, VP, reversal arrows and router.
- Daily volume profile (previous day POC / VAH / VAL, max 5 days).
- AI Analyst (Claude Fable): read-only, on a button press only.

**Screens**
- MANUAL / AUTO card and the connected accounts on the Market Overview.
- Strip of the indicators that are switched ON, under the chart.
- 2 SCREEN (second chart with its own TF) + ⚡ AUTO TF from the chart + pop-out window.
- Companion view for the phone (`/m`).
- Clearer signals list (WIN +2R / LOSS −1R).
- Red banner when the running server is older than the page.

**Safety**
- Unchanged: ARM, MASTER, the 15-gate master gate, the risk engine, AUTO is always OFF at start, and other EA trades
  are never touched.
- Errors in the log now show file:line.

## v1.0.0 (2026-09-25)
- First terminal:
  - chart + MTF analyser and score;
  - plugins (CHART / SCORE per indicator);
  - multi-account copier;
  - journal, news reader, backtest;
  - paper / live with ARM + confirm.
