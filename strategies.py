"""
STRATEGIES - three market-condition strategies, each with its OWN set of indicator conditions (editable).

  TREND        the market trends          -> MARKET entry WITH the trend (BUY in an uptrend / SELL in a downtrend)
  PULLBACK     a trend + an overbought / oversold dip AGAINST it that turns back -> MARKET entry WITH the trend
               (ON/OFF; skipped when the market is VOLATILE: an extreme can keep going)
  CONSOLIDATION a box (2 tops + 2 bottoms) + quiet market -> PENDING BUY STOP above / SELL STOP below (OCO)
  (news straddle = the NEWS TRADE card)

Every strategy:
  * conditions   named indicator checks, each ON/OFF; score = % of the ON conditions that are met
  * required     conditions that must be met whatever the score (the trigger)
  * min_pct      the strategy's own passing % (NOT the analyser score)
  * rr / sl      its own stop and target
Everything is computed from CLOSED bars only (bar i uses bars <= i). The same functions feed the chart arrows
(plugins/strategy_signals.py), AUTO TRADE, the forecast comparison and the backtest.
"""
import json
import math
import os

import conso
import exits

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "strategy_settings.json")
NAMES = ("trend", "pullback", "conso")
TITLE = {"trend": "TREND", "pullback": "PULLBACK", "conso": "CONSOLIDATION"}

# (key, label, default ON)
CATALOG = {
    "trend": [
        ("bos_m15", "BOS sa ENTRY TF (bos_ltf, hal. M15): ≥ bos_m15_min sa direksyon, sa huling bos_window kandila", True),
        ("bos_h4", "1 BOS sa TREND TF (bos_htf, hal. H4) sa direksyon = ang trend (sa huling h4_window kandila)", True),
        ("ema_align", "EMA20 at EMA50 nakaayos sa direksyon", False),
        ("price_ema50", "Presyo sa tamang panig ng EMA50", False),
        ("adx", "ADX ≥ adx_min (may lakas ang trend)", False),
        ("di", "+DI / −DI sa direksyon", False),
        ("macd", "MACD histogram sa direksyon", False),
        ("supertrend", "Supertrend sa direksyon", False),
        ("htf", "HTF bias ng analyser (H4/H1) kasabay", False),
        ("bos", "Bagong BOS sa direksyon sa AUTO TF (huling 3 bars)", False),
        ("rsi_side", "RSI sa tamang panig ng 50 (BUY > 50 / SELL < 50)", False),
        ("ema200", "Presyo sa tamang panig ng EMA200", False),
        ("of_buyers", "ORDER FLOW: mas malakas ang buyers (BUY) / sellers (SELL): delta ng huling of_bars ≥ of_min % ng volume", True),
        ("not_extreme", "KONTRA: hindi overbought (BUY: RSI < rsi_hi) / hindi oversold (SELL: RSI > rsi_lo)", True),
        ("no_ob_block", "KONTRA: walang order block (ob_tf) na nakaharang sa daan; kapag na-break ito, tuloy ang entry", False),
        ("m5_struct", "M5 STRUCTURE: ang huling M5 BOS ay nasa direksyon (walang BUY habang pababa pa ang M5)", False),
        ("mmi_cmi", "MMI / CMI = TREND: CMI ≥ cmi_trend (malayo ang narating vs range) O MMI bumababa (trend na nabubuo)", True),
        ("cvd", "CVD: tumataas ang CVD (BUY) / bumababa (SELL) - delta ng M5 bars sa huling cvd_bars kandila (cvd_mode turn2: huling 2)", False),
    ],
    "pullback": [
        ("rsi", "RSI umabot sa ≤ os (uptrend) / ≥ ob (downtrend) sa huling 6 bars", True),
        ("stoch", "Stochastic %K ≤ 20 / ≥ 80", False),
        ("cci", "CCI ≤ −100 / ≥ 100", False),
        ("wpr", "Williams %R ≤ −80 / ≥ −20", False),
        ("near_ema", "Presyo malapit sa EMA20 / EMA50 (≤ 0.6 ATR)", False),
        ("mfi", "MFI ≤ mfi_os (uptrend) / ≥ mfi_ob (downtrend) sa huling 6 bars", False),
        ("doji", "Doji sa dulo ng dip (body ≤ doji_body ng range, huling 2 bars)", False),
        ("ob", "Order block sa ob_tf (M15) na tinamaan ng pullback at HINDI na-break: SELL = umakyat sa bearish OB sa taas, BUY = bumaba sa bullish OB sa ilalim", False),
        ("ob_spike", "Order block O biglang spike: may OB (gaya sa itaas) O mabilis na galaw na ≥ spike_atr × ATR sa huling 6 bars papunta sa extreme", True),
        ("turn", "Bumabalik na: RSI tumawid pabalik sa os/ob o Stoch sa 20/80", False),
        ("of_turn", "ORDER FLOW: pumasok na ang buyers (BUY) / sellers (SELL) sa dulo ng pullback: delta ng huling 2 bars sa direksyon", True),
        ("bb_touch", "Tumama sa Bollinger band (BUY lower / SELL upper) sa huling 6 bars", False),
        ("engulf", "Engulfing candle sa direksyon ng trade (bar na ito o nauna)", False),
        ("mmi_cmi", "MMI / CMI: hindi runaway trend (MMI ≥ mmi_rev O CMI ≤ cmi_rev) = may babalikan ang presyo", True),
    ],
    "conso": [
        ("adx_low", "ADX < adx_max (20) → BUY STOP / SELL STOP agad (sa box kung meron, o sa huling range_bars)", True),
        ("vol_low", "Mahinang volume (pinakamababang vol_pct %), tuloy-tuloy nang quiet_bars kandila", False),
        ("box", "May consolidation box (2 tops + 2 bottoms)", False),
        ("squeeze", "Bollinger squeeze (BB width sa pinakamababang 30%)", False),
        ("atr_low", "ATR percentile ≤ 50 (tahimik)", False),
        ("of_balanced", "ORDER FLOW: balanse ang buyers at sellers (|delta| < of_min % ng volume) = totoong consolidation", True),
        ("rsi_mid", "RSI nasa gitna (40–60): walang direksyon", False),
        ("inside_bb", "Presyo nasa loob ng Bollinger bands (huling quiet_bars kandila)", False),
        ("mmi_cmi", "MMI / CMI = CHOP: CMI ≤ cmi_chop (paikot-ikot) at hindi bumababa ang MMI (walang trend na nabubuo)", True),
    ],
}
# TREND: the trend indicators together (score) + EMA order + ADX strength; BOS is a counted condition, not a must
# CONSO: low ADX + low volume -> grid of pending stops around the range (the box when found, else the last range_bars)
# PULLBACK (Eugene, 2026-10-01): uptrend + oversold + MFI + doji -> BUY (mirror for SELL)
# PULLBACK (Eugene, 2026-10-01): + order block. CONSO: ADX < 20 + low volume for at least 4 candles in a row
# TREND (Eugene, 2026-10-01): 2 BOS on M15 + 1 BOS on H4 in the same direction. CONSO: volume + ADX + box (3)
# v2.7.8 refine (Eugene, 2026-10-03; picked on Mar-Jun 2026, checked on Jul-Sep): TREND + KONTRA not_extreme,
# PULLBACK = RSI + order block (+ order flow) - MFI + doji together never fired
REQUIRED = {"trend": ["bos_m15", "bos_h4", "of_buyers", "not_extreme", "mmi_cmi"],     # v2.8.49: order flow back
            "pullback": ["rsi", "ob_spike", "of_turn", "mmi_cmi"], "conso": ["adx_low", "of_balanced", "mmi_cmi"]}
# ORDER FLOW per strategy (Eugene, 2026-10-02: "i-ON mo na"): what order flow must say for each strategy
OF_KEY = {"trend": "of_buyers", "pullback": "of_turn", "conso": "of_balanced"}
# CONSO (Eugene, 2026-10-01): "matagal bumaba ang ADX below 20" -> ADX < 20 alone places the pending orders
# 100% (Eugene, 2026-10-01): "per strategy na tayo": a strategy enters only when EVERY condition you checked is met
SCHEMA = 26      # saved settings older than this get the new conditions / required lists (other values are kept)
MTF_TFS = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400}     # timeframes the TREND break-of-structure rule can use

DEFAULTS = {
    "trend": dict(route=False, route_hi=55.0, route_lo=35.0, cmi_trend=45.0, mmi_lb=5, auto=True, max_risk_pct=0.0, max_sl=20.0, min_pct=100.0, rr=5.0, sl_atr=1.5, sl_mode="swing", swing_buf=0.1, swing_min=0.5, swing_max=4.0, adx_min=22.0, cooldown=8,
                  bos_ltf="M15", bos_htf="H4", bos_m15_min=2, bos_window=32, h4_window=12,
                  rsi_hi=70.0, rsi_lo=30.0, ob_tf="M15", ob_block_atr=1.5, of_bars=3, of_min=15.0,
                  m5_window=32, cvd_mode="slope8", cvd_bars=8),
    "pullback": dict(route=False, route_hi=55.0, route_lo=35.0, mmi_rev=66.0, cmi_rev=70.0, mmi_lb=5, auto=True, any_dir=True, spike_atr=3.0, max_risk_pct=0.0, max_sl=20.0, min_pct=100.0, rr=5.0, sl_atr=1.2, os=40.0, ob=60.0, block_volatile=False,
                     vol_pct=85.0, cooldown=6, mfi_os=20.0, mfi_ob=80.0, doji_body=0.15, ob_near=0.3, ob_tf="M15", of_min=10.0),
    "conso": dict(route=False, route_hi=55.0, route_lo=35.0, cmi_chop=40.0, mmi_lb=5, auto=False, no_expiry=True, max_risk_pct=0.0, min_pct=100.0, of_bars=5, of_min=15.0, rr=5.0, buffer=0.5, max_sl=20.0, expiry_bars=8, adx_max=20.0, quiet_bars=1,
                  vol_pct=35.0, range_bars=12, levels=1, step=1.5,
                  lookback=48, swing=2, min_touches=2, tol_pips=5.0, pip=0.10, min_bars=8, max_range=10.0,
                  min_range=0.5),
}


_P4 = ["rsi", "mfi", "doji", "ob", "of_turn"]
# PROFILES (Eugene, 2026-10-01): NORMAL / AGGRESSIVE / SCALP presets for all three strategies + the AUTO TF.
# Applying one never switches anything ON (the AUTO flags of the strategies and AUTO TRADE stay as they are).
PROFILES = {
    # MASTER (Eugene, 2026-10-04: "ilagay mo na ang master sa terminal"): the same rules as the MT5 Master EA v1.90
    # (preset CVD) - TREND: H4 BOS + 2 M15 BOS + not overbought/oversold + M5 structure + CVD, swing SL, 1:2;
    # PULLBACK RSI 30/70 + OB; CONSO ADX < 20 grid. Each strategy its own trade, reversal close, early exit,
    # re-entry after a win and secure 50% at 1R are the AUTO TRADE "MASTER" settings (autotrade.py).
    "master": dict(
        title="MASTER", auto_tf="M15", htf_filter=False,
        autotrade=dict(master_mode=True, max_open=3, max_per_day=0, no_opposite=False, reversal=True,
                       trend_early_exit="m15_2bos", trend_reentry_win=30, trend_secure_r=1.0, trend_secure_pct=50.0,
                       conso_protect=True, mode_mult=1.0, tl_max_sl=20.0, tl_rr=5.0),
        # v2.8.34: TP 1:5; exits by the 0.5R ladder (BE +0.5R, then every +0.5R one step behind) and the reversal
        protect=dict(be_r=0.5, be_offset=0.20, profit_lock=[], trail_mode="step", step_r=0.5, trail_start_r=0.5,
                     tp_scale=False),
        desc="Pareho ng MT5 Master EA v1.90 (CVD): TREND H4 + 2 M15 BOS + M5 structure + CVD, SL sa swing, TP 1:5 + trailing; "
             "PULLBACK RSI 30/70 + OB; CONSO ADX<20 grid. Bawat strategy may sariling trade + reversal close.",
        trend=dict(bos_ltf="M15", bos_htf="H4", bos_m15_min=2, bos_window=32, h4_window=12, rr=5.0, sl_atr=1.5, sl_mode="swing",
                   cooldown=8, rsi_hi=70.0, rsi_lo=30.0, m5_window=32, cvd_mode="slope8", cvd_bars=8, max_sl=20.0,
                   conditions=["bos_m15", "bos_h4", "not_extreme", "m5_struct", "cvd", "mmi_cmi"]),
        pullback=dict(os=30.0, ob=70.0, mfi_os=20.0, mfi_ob=80.0, rr=5.0, sl_atr=1.2, cooldown=6, vol_pct=85.0,
                      ob_near=0.3, max_sl=20.0, spike_atr=3.0, conditions=["rsi", "ob_spike", "mmi_cmi"]),
        conso=dict(quiet_bars=1, adx_max=20.0, vol_pct=35.0, rr=5.0, levels=1, step=1.5, buffer=0.5, expiry_bars=8,
                   max_sl=20.0, range_bars=12, max_range=0.0, conditions=["adx_low", "mmi_cmi"])),
    # v2.8.38 (Eugene: "ayusin mo ang 3 mode ... sa scalp at aggressive ikaw na bahala, mas malaking lot pag aggressive"):
    # the same SC logic as NORMAL (PULLBACK OB or spike both sides, CONSO pairs, 0.5R ladder, reversal on entry), tuned.
    "aggressive": dict(
        title="AGGRESSIVE", auto_tf="M15", htf_filter=False,
        autotrade=dict(master_mode=True, max_open=4, max_per_day=0, no_opposite=False, reversal=True,
                       trend_early_exit="m15_2bos", trend_reentry_win=30, trend_secure_r=1.0, trend_secure_pct=50.0,
                       conso_protect=True, mode_mult=2.0, tl_max_sl=20.0, tl_rr=3.0),
        protect=dict(be_r=0.5, be_offset=0.20, profit_lock=[], trail_mode="step", step_r=0.5, trail_start_r=0.5,
                     tp_scale=False),
        desc="Mas maluwag at x2 lot (TREND / PULLBACK / TL BREAK): 1 BOS sa M15 + H4 BOS, PULLBACK RSI 35/65 + (OB o "
             "spike) BUY at SELL, CONSO ADX<22. TP 1:3, BE sa +0.5R at trailing bawat 0.5R. Mas maraming trade.",
        trend=dict(bos_ltf="M15", bos_htf="H4", bos_m15_min=1, bos_window=32, h4_window=30, rr=3.0, sl_atr=1.2,
                   sl_mode="swing", cooldown=4, max_sl=20.0, cmi_trend=35.0, conditions=["bos_m15", "bos_h4", "not_extreme", "mmi_cmi"]),
        pullback=dict(os=35.0, ob=65.0, mfi_os=25.0, mfi_ob=75.0, rr=3.0, sl_atr=1.2, cooldown=4, vol_pct=85.0,
                      ob_near=0.3, max_sl=20.0, spike_atr=2.5, mmi_rev=60.0, cmi_rev=75.0, conditions=["rsi", "ob_spike", "mmi_cmi"]),
        conso=dict(quiet_bars=1, adx_max=22.0, cmi_chop=45.0, vol_pct=35.0, rr=3.0, levels=2, step=1.0, buffer=0.5, expiry_bars=8,
                   max_sl=20.0, range_bars=12, max_range=0.0, conditions=["adx_low", "mmi_cmi"])),
    "scalp": dict(
        title="SCALP", auto_tf="M5", htf_filter=False,
        autotrade=dict(master_mode=True, max_open=3, max_per_day=0, no_opposite=False, reversal=True,
                       trend_early_exit="m15_2bos", trend_reentry_win=30, trend_secure_r=1.0, trend_secure_pct=50.0,
                       conso_protect=True, mode_mult=1.0, tl_max_sl=15.0, tl_rr=3.0),
        protect=dict(be_r=0.5, be_offset=0.10, profit_lock=[], trail_mode="step", step_r=0.5, trail_start_r=0.5,
                     tp_scale=False),
        desc="Mabilis: M5 entry (2 BOS sa M5 + BOS sa H1), SL max $15 (150 pips), pati CONSO, PULLBACK RSI 30/70 + (OB o "
             "spike) BUY at SELL. TP 1:3, BE sa +0.5R at trailing bawat 0.5R. Maikli ang trade.",
        trend=dict(bos_ltf="M5", bos_htf="H1", bos_m15_min=2, bos_window=36, h4_window=24, rr=3.0, sl_atr=1.0,
                   sl_mode="swing", cooldown=4, max_sl=15.0, conditions=["bos_m15", "bos_h4", "not_extreme", "mmi_cmi"]),
        pullback=dict(os=30.0, ob=70.0, mfi_os=20.0, mfi_ob=80.0, rr=3.0, sl_atr=1.0, cooldown=3, vol_pct=85.0,
                      ob_near=0.3, max_sl=15.0, spike_atr=3.0, conditions=["rsi", "ob_spike", "mmi_cmi"]),
        conso=dict(quiet_bars=1, adx_max=20.0, vol_pct=35.0, rr=3.0, levels=1, step=1.0, buffer=0.3, expiry_bars=6,
                   max_sl=15.0, range_bars=12, max_range=0.0, conditions=["adx_low", "mmi_cmi"])),
}


# v2.8.37 (Eugene: "dapat ang mode natin ay SCALP, NORMAL at AGGRESSIVE lang"; "normal setup"): NORMAL = the SC setup
# that was built on the MASTER profile (TREND / PULLBACK any side, OB or spike / CONSO, 1:5, 0.5R ladder, CONSO protected,
# each strategy its own trade + reversal). "master" stays only as an alias of NORMAL (old saved settings / links).
PROFILES["normal"] = json.loads(json.dumps(PROFILES["master"]))
PROFILES["normal"].update(
    title="NORMAL",
    desc="SC setup: M15 entry. TREND H4 + 2 M15 BOS + M5 structure + CVD, SL sa swing (max $20); PULLBACK RSI 30/70 + "
         "(OB o spike) BUY at SELL kahit kontra HTF; CONSO ADX<20 BUY/SELL STOP pares. TP 1:5, BE sa +0.5R at trailing "
         "bawat 0.5R; bawat strategy may sariling trade + reversal kapag may entry.")
# v2.8.49 (Eugene: "ok pa ang trade natin hanggang Oct 7, Oct 8 dun na" -> "ibalik sa dati"): what REALLY ran until Oct 7.
# The zips up to v2.8.43 carried an old strategy_settings.json, so the VPS ran THESE values (HTF filter ON, order flow
# REQUIRED, PULLBACK RSI 40/60, CONSO box max $10), not the NORMAL profile. On Oct 8 (v2.8.44 without that file +
# NORMAL pressed, v2.8.45 order flow info-only + CMI router) they were lost. NORMAL = this setup again.
OCT7 = dict(
    htf_filter=True,
    trend=dict(rr=5.0, max_sl=20.0, sl_mode="swing", sl_atr=1.5, cooldown=8, bos_ltf="M15", bos_htf="H4", bos_m15_min=2,
               bos_window=32, h4_window=12, rsi_hi=70.0, rsi_lo=30.0, of_bars=3, of_min=15.0, route=False,
               conditions=["bos_m15", "bos_h4", "of_buyers", "not_extreme", "mmi_cmi"]),
    pullback=dict(rr=5.0, max_sl=20.0, os=40.0, ob=60.0, any_dir=True, block_volatile=False, spike_atr=3.0, cooldown=6,
                  of_min=10.0, route=False, conditions=["rsi", "ob_spike", "of_turn", "mmi_cmi"]),
    conso=dict(rr=5.0, max_sl=20.0, levels=1, step=1.5, max_range=10.0, adx_max=20.0, quiet_bars=1, of_bars=5, of_min=15.0,
               route=False, conditions=["adx_low", "of_balanced", "mmi_cmi"]))
for _n in NAMES:
    PROFILES["normal"][_n].update(json.loads(json.dumps(OCT7[_n])))
PROFILES["normal"]["htf_filter"] = True
PROFILES["normal"]["desc"] = ("Ang setup na tumakbo hanggang Oct 7: HTF filter ON, ORDER FLOW required sa lahat. TREND H4 + 2 M15 BOS "
                              "+ order flow + hindi overbought/oversold; PULLBACK RSI 40/60 + (OB o spike) + order flow; CONSO "
                              "ADX<20 + order flow balanse, box max $10. TP 1:5, BE +0.5R, trailing bawat 0.5R.")
PROFILES["master"] = json.loads(json.dumps(PROFILES["normal"]))
for _p in ("aggressive", "scalp"):
    for _n in NAMES:
        PROFILES[_p][_n]["route"] = False                     # v2.8.49: no CMI router (it was not there until Oct 7)
PROFILE_BUTTONS = ("scalp", "normal", "aggressive")       # what the UI shows (MASTER hidden: it is NORMAL now)


def apply_profile(cur, name):
    """cur settings + the profile -> new settings (AUTO flags kept). The profile's conditions are exactly the
    checked (and required) ones."""
    name = "normal" if name == "master" else name
    p = PROFILES[name]
    out = json.loads(json.dumps(cur))
    for n in NAMES:
        ov = dict(p[n])
        conds = ov.pop("conditions")
        out[n].update(ov)
        out[n]["conditions"] = {k: k in conds for k, _, _ in CATALOG[n]}
        out[n]["required"] = list(conds)
        out[n]["min_pct"] = 100.0
    out["profile"], out["htf_filter"], out["schema"] = name, bool(p["htf_filter"]), SCHEMA
    return clean_settings(out)


def default_settings():
    out = {}
    for n in NAMES:
        d = dict(DEFAULTS[n])
        d["conditions"] = {k: on for k, _, on in CATALOG[n]}
        d["required"] = list(REQUIRED[n])
        out[n] = d
    return out


def clean_settings(raw):
    s = default_settings()
    old_schema = int((raw or {}).get("schema", 1) or 1) if raw else SCHEMA
    for n in NAMES:
        new = dict((raw or {}).get(n) or {})
        if n == "conso" and old_schema < 5:
            for k in ("quiet_bars", "adx_max", "range_bars", "max_range"):   # v2.4: ADX < 20 right away
                new.pop(k, None)
        if old_schema < SCHEMA and (n == "trend" and old_schema < 2 or n == "pullback" and old_schema < 3
                                    or n == "conso" and old_schema < 4):
            new.pop("conditions", None)                          # the rules changed: start from the new set
            new.pop("required", None)
        cur = s[n]
        for k, dv in DEFAULTS[n].items():
            if k not in new:
                continue
            try:
                cur[k] = bool(new[k]) if isinstance(dv, bool) else type(dv)(float(new[k])) if not isinstance(dv, str) \
                    else str(new[k])
            except (TypeError, ValueError):
                pass
        if isinstance(new.get("conditions"), dict):
            for k in cur["conditions"]:
                if k in new["conditions"]:
                    cur["conditions"][k] = bool(new["conditions"][k])
        if isinstance(new.get("required"), list):
            cur["required"] = [k for k in new["required"] if k in cur["conditions"]]
        if old_schema < 7:
            cur["auto"] = True                                   # v2.7.2: every strategy on AUTO (Eugene: "i-ON mo lahat")
        if old_schema < 10 and (raw or {}).get("profile", "normal") == "normal":
            # v2.7.8 refine (once, NORMAL): TREND fresh H4 trend + not overbought/oversold; PULLBACK RSI + OB, 1:2
            if n == "trend":
                if cur["h4_window"] == 30:
                    cur["h4_window"] = 12
                if "not_extreme" not in cur["required"]:
                    cur["required"].append("not_extreme")
            if n == "pullback":
                cur["required"] = [k for k in cur["required"] if k not in ("mfi", "doji")]
                cur["conditions"]["mfi"] = cur["conditions"]["doji"] = False
                if abs(cur["rr"] - 1.5) < 1e-9:
                    cur["rr"] = 2.0
        if old_schema < 9 and n == "trend" and (raw or {}).get("profile", "normal") == "normal":
            cur["sl_mode"] = "swing"                             # v2.7.7: TREND SL at the last swing (NORMAL, once)
        if n == "trend" and cur["sl_mode"] not in ("swing", "atr"):
            cur["sl_mode"] = "swing"
        if n == "trend" and cur["cvd_mode"] not in ("slope8", "turn2"):
            cur["cvd_mode"] = "slope8"
        if n == "trend":
            cur["cvd_bars"] = max(2, min(50, int(cur["cvd_bars"])))
        if old_schema < 8 and n == "conso":
            cur["auto"] = False                                  # v2.7.6: CONSO to MANUAL (6-month test: -130R)
        if old_schema < 12 and n == "conso":
            cur["auto"] = True                                   # v2.8.3 (Eugene): CONSO laging AUTO
        if old_schema < 13 and n == "conso":
            cur["max_risk_pct"] = 0.0                            # v2.8.14 (Eugene): walang risk-per-trade sa CONSO
        if old_schema < 21:
            # v2.8.43 (Eugene: "isama mo sa lahat ng strategy"): MMI / CMI regime check, ON + required, once
            cur.setdefault("conditions", {})["mmi_cmi"] = True
            req = list(cur.get("required") or [])
            if "mmi_cmi" not in req:
                cur["required"] = req + ["mmi_cmi"]
        if old_schema < 20 and n == "conso" and (raw or {}).get("profile", "normal") in ("normal", "master") \
                and int(new.get("levels", 2) or 2) == 2:
            # v2.8.42 (Eugene): NORMAL = 1 BUY STOP + 1 SELL STOP; AGGRESSIVE = 2 + 2
            cur["levels"] = 1
        if old_schema < 19 and n == "conso":
            # v2.8.41 (Eugene: "isabay sa SL nila ang conso"): the CONSO max SL = the TREND / PULLBACK max SL
            cur["max_sl"] = float((((raw or {}).get("trend") or {}).get("max_sl")) or 20.0)
        if old_schema < 17:
            # v2.8.34 (Eugene: "pwede tayo mag 1:5RR, may trailing naman tayo, tapos may pullback na mag-autoclose"):
            # TP at 5R for TREND / PULLBACK / CONSO; the 0.5R trailing ladder and the reversal take the exits
            cur["rr"] = 5.0
        if old_schema < 16 and n in ("trend", "pullback"):
            # v2.8.23 (Eugene: "max tayo sa SL na $20 or 200 pips"): no % risk block for TREND / PULLBACK; the stop is
            # capped at a $20 price distance (200 gold pips) instead
            cur["max_risk_pct"], cur["max_sl"] = 0.0, 20.0
        if old_schema < 15 and n == "pullback":
            # v2.8.21 (Eugene: "khit walang OB, pero kung may OB mas ok"): OB OR a sudden spike
            if cur.get("conditions", {}).get("ob"):
                cur["conditions"]["ob"], cur["conditions"]["ob_spike"] = False, True
            if "ob" in cur.get("required", []):
                cur["required"] = ["ob_spike" if k == "ob" else k for k in cur["required"]]
        if old_schema < 14 and n == "pullback":
            # v2.8.18 (Eugene: "alisin ang htf sa pullback... para sa biglang spike, sell at buy man"): both
            # directions (oversold -> BUY, overbought -> SELL), and no VOLATILE block (a spike is volatile)
            cur["any_dir"], cur["block_volatile"] = True, False
        if old_schema < 6 and OF_KEY[n] not in cur["required"]:
            cur["required"].append(OF_KEY[n])                    # v2.7.1: order flow ON in every strategy
        if old_schema < 22:                                      # after every older step
            # v2.8.45 (Eugene, audit): order flow is info only (no tick history -> live and history judged the same)
            ofk = OF_KEY[n]
            cur.setdefault("conditions", {})[ofk] = False
            cur["required"] = [k for k in (cur.get("required") or []) if k != ofk]
        if old_schema < 26:                                      # after every older step
            prof = (raw or {}).get("profile", "normal")
            if prof in ("normal", "master"):
                # v2.8.49: back to what ran until Oct 7 (OCT7), once. AUTO flags stay.
                o = OCT7[n]
                for k, v in o.items():
                    if k != "conditions":
                        cur[k] = v
                cur["conditions"] = {k: k in o["conditions"] for k, _, _ in CATALOG[n]}
                cur["required"] = list(o["conditions"])
            else:
                cur["route"] = False
                ofk = OF_KEY[n]
                cur["conditions"][ofk] = True
                if ofk not in cur["required"]:
                    cur["required"].append(ofk)
        known = {k for k, _, _ in CATALOG[n]}
        cur["required"] = [k for k in cur["required"] if k in known]
        for k in cur["required"]:
            cur["conditions"][k] = True                          # a required condition is always ON
        cur["min_pct"] = 100.0                                    # every checked condition must be met
        cur["rr"] = max(0.3, min(10.0, cur["rr"]))
        if n in ("pullback", "trend") and cur["ob_tf"] not in MTF_TFS:
            cur["ob_tf"] = "M15"
        if n == "trend":
            if cur["bos_ltf"] not in MTF_TFS:
                cur["bos_ltf"] = "M15"
            if cur["bos_htf"] not in MTF_TFS or MTF_TFS[cur["bos_htf"]] <= MTF_TFS[cur["bos_ltf"]]:
                higher = [t for t, sec in MTF_TFS.items() if sec > MTF_TFS[cur["bos_ltf"]]]
                cur["bos_htf"] = "H4" if "H4" in higher else (higher[-1] if higher else "H4")
                if cur["bos_ltf"] == "H4":
                    cur["bos_ltf"] = "M15"
            cur["bos_m15_min"] = max(1, min(5, int(cur["bos_m15_min"])))
    raw = raw or {}
    s["profile"] = raw.get("profile") if raw.get("profile") in list(PROFILES) + ["custom"] else "normal"
    if s["profile"] == "master":
        s["profile"] = "normal"                    # v2.8.37: MASTER is called NORMAL now (same settings)
    s["htf_filter"] = bool(raw.get("htf_filter", True))
    if raw and int(raw.get("schema", 1) or 1) < 26 and s["profile"] == "normal":
        s["htf_filter"] = True                     # v2.8.49: the Oct 7 setup had the HTF filter ON
    s["schema"] = SCHEMA
    return s


def master_settings(cur=None):
    """The MASTER setup (the MT5 Master EA rules) on top of cur, with EVERY strategy on AUTO (CONSO too)."""
    m = apply_profile(cur if cur is not None else clean_settings(None), "master")
    for n in NAMES:
        m[n]["auto"] = True
    return m


def migrate_to_master(path=None):
    """v2.8.0 (Eugene, 2026-10-04: "ilagay mo na ang master sa terminal"), ONCE (called by AUTO TRADE's v4 settings
    migration): a NORMAL setup becomes MASTER. AGGRESSIVE / SCALP / CUSTOM are left as they are. -> True if changed."""
    cur = load_settings(path)
    if cur.get("profile", "normal") != "normal":
        return False
    save_settings(master_settings(cur), path)
    return True


def load_settings(path=None):
    try:
        with open(path or SETTINGS_FILE) as f:
            return clean_settings(json.load(f))
    except (OSError, ValueError):
        return clean_settings(None)


def save_settings(s, path=None):
    s = clean_settings(s)
    try:
        with open(path or SETTINGS_FILE, "w") as f:
            json.dump(s, f, indent=2)
    except OSError:
        pass
    return s


# ============================================================================ indicator series (causal)
def _bos(bars, k=3, levels=None):
    """+1 / -1 on the bar whose CLOSE breaks the last confirmed swing high / low (pivot k bars each side,
    known k bars later). Causal: bos[i] uses bars <= i. levels: optional list that receives (swing high, swing low)
    as known after each bar (the prices the NEXT up / down BOS needs a close beyond)."""
    n = len(bars)
    sh = sl_ = None
    out = [0] * n
    for i in range(n):

        j = i - k
        if j >= k:
            if all(bars[j].h >= bars[x].h for x in range(j - k, j + k + 1) if x != j):
                sh = bars[j].h
            if all(bars[j].l <= bars[x].l for x in range(j - k, j + k + 1) if x != j):
                sl_ = bars[j].l
        if i and sh is not None and bars[i].c > sh >= bars[i - 1].c:
            out[i] = 1
        elif i and sl_ is not None and bars[i].c < sl_ <= bars[i - 1].c:
            out[i] = -1
        if levels is not None:
            levels.append((sh, sl_))
    return out


def _tf_sec(bars):
    if len(bars) < 3:
        return 300
    return min(b2.t - b1.t for b1, b2 in zip(bars[-60:], bars[-59:])) or 300


def _resample(bars, sec):
    """Bars grouped into `sec` buckets (open of the first, high / low, close of the last, summed volume)."""
    from engine import Bar
    out, cur, key = [], None, None
    for b in bars:
        k = b.t // sec
        if k != key:
            if cur:
                out.append(Bar(*cur))
            key, cur = k, [k * sec, b.o, b.h, b.l, b.c, float(getattr(b, "v", 0) or 0)]
        else:
            cur[2], cur[3], cur[4] = max(cur[2], b.h), min(cur[3], b.l), b.c
            cur[5] += float(getattr(b, "v", 0) or 0)
    if cur:
        out.append(Bar(*cur))
    return out


MTF = tuple(MTF_TFS.items())


def _mtf(bars, mtf=None):
    """Break-of-structure state of M15 and H4 for every bar of `bars`: the real M15 / H4 bars when given (live,
    chart, backtest), else resampled from `bars`. Prefix sums -> O(log n) lookups; only higher-TF bars CLOSED by
    the strategy bar's close are used (no look-ahead)."""
    sec = _tf_sec(bars)
    out = {}
    for tf, tsec in MTF:
        src = (mtf or {}).get(tf)
        if not src and tsec >= sec:
            src = _resample(bars, tsec)
        if not src or len(src) < 10:
            out[tf] = None
            continue
        lv = []
        bos = _bos(src, levels=lv)
        up, dn, last, last_at = [0], [0], [0], [-1]
        for k, v in enumerate(bos):
            up.append(up[-1] + (v == 1))
            dn.append(dn[-1] + (v == -1))
            last.append(v if v else last[-1])
            last_at.append(k if v else last_at[-1])
        out[tf] = dict(closes=[b.t + tsec for b in src], up=up, dn=dn, last=last, last_at=last_at,
                       levels=[(None, None)] + lv, src=src)
    return out, sec


def mtf_state(S, i, tf, window):
    """BOS on `tf` as of the close of strategy bar i: ups / downs in the last `window` closed bars, the direction
    of the latest BOS and how many closed `tf` bars ago it was."""
    import bisect
    m = (S.get("mtf") or {}).get(tf)
    if not m:
        return None
    k = bisect.bisect_right(m["closes"], S["t_close"][i])
    lo = max(0, k - int(window))
    last = m["last"][k]
    sh, sl_ = m["levels"][k]
    return dict(up=m["up"][k] - m["up"][lo], dn=m["dn"][k] - m["dn"][lo], last=last,
                age=(k - 1 - m["last_at"][k]) if last else None, bars=k, swing_high=sh, swing_low=sl_)
def _ema(vals, n):
    out, a, k = [None] * len(vals), None, 2 / (n + 1)
    for i, v in enumerate(vals):
        a = v if a is None else a + k * (v - a)
        out[i] = a if i >= n - 1 else None
    return out


def _rma(vals, n):
    out, a = [None] * len(vals), None
    for i, v in enumerate(vals):
        if i < n - 1:
            continue
        a = sum(vals[i - n + 1:i + 1]) / n if a is None else (a * (n - 1) + v) / n
        out[i] = a
    return out


def _rank(vals, i, look=100):
    """Percentile (0-100) of vals[i] among the previous `look` values (causal)."""
    w = [v for v in vals[max(0, i - look + 1):i + 1] if v is not None]
    if not w or vals[i] is None:
        return None
    return 100.0 * sum(1 for v in w if v <= vals[i]) / len(w)


def series(bars, mtf=None):
    """Every indicator the strategies use, one value per bar (None while warming up).
    mtf: optional {"M5" / "M15" / "H1" / "H4": bars} for the trend strategy's break-of-structure rules."""
    n = len(bars)
    c = [b.c for b in bars]
    h = [b.h for b in bars]
    lo = [b.l for b in bars]
    S = dict(n=n, c=c, ema20=_ema(c, 20), ema50=_ema(c, 50), atr=exits.atr(bars, 14))
    v = [float(getattr(b, "v", 0) or 0) for b in bars]
    S["vol"] = [None if i < 4 else sum(v[i - 4:i + 1]) / 5 for i in range(n)]       # 5-bar average tick volume
    # RSI 14
    up, dn = [0.0], [0.0]
    for i in range(1, n):
        d = c[i] - c[i - 1]
        up.append(max(d, 0.0))
        dn.append(max(-d, 0.0))
    au, ad = _rma(up, 14), _rma(dn, 14)
    S["rsi"] = [None if a is None or b is None else (100.0 if b == 0 else 100 - 100 / (1 + a / b)) for a, b in zip(au, ad)]
    # Stochastic %K (14, 3)
    raw = [None] * n
    for i in range(13, n):
        hh, ll = max(h[i - 13:i + 1]), min(lo[i - 13:i + 1])
        raw[i] = 50.0 if hh == ll else 100 * (c[i] - ll) / (hh - ll)
    S["stoch"] = [None if i < 15 or None in raw[i - 2:i + 1] else sum(raw[i - 2:i + 1]) / 3 for i in range(n)]
    # Williams %R 14
    S["wpr"] = [None if raw[i] is None else raw[i] - 100 for i in range(n)]
    # CCI 20
    tp = [(h[i] + lo[i] + c[i]) / 3 for i in range(n)]
    cci = [None] * n
    for i in range(19, n):
        w = tp[i - 19:i + 1]
        m = sum(w) / 20
        md = sum(abs(x - m) for x in w) / 20
        cci[i] = 0.0 if md == 0 else (tp[i] - m) / (0.015 * md)
    S["cci"] = cci
    # MACD 12 26 9 histogram
    e12, e26 = _ema(c, 12), _ema(c, 26)
    line = [None if a is None or b is None else a - b for a, b in zip(e12, e26)]
    first = next((i for i, v in enumerate(line) if v is not None), n)
    sig = [None] * n
    if first < n:
        sub = _ema(line[first:], 9)
        sig[first:] = sub
    S["macd"] = [None if a is None or b is None else a - b for a, b in zip(line, sig)]
    # ADX / DI 14
    tr, pdm, mdm = [0.0], [0.0], [0.0]
    for i in range(1, n):
        tr.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
        u, d = h[i] - h[i - 1], lo[i - 1] - lo[i]
        pdm.append(u if u > d and u > 0 else 0.0)
        mdm.append(d if d > u and d > 0 else 0.0)
    atr_w, pw, mw = _rma(tr, 14), _rma(pdm, 14), _rma(mdm, 14)
    pdi = [None if a in (None, 0) or p is None else 100 * p / a for p, a in zip(pw, atr_w)]
    mdi = [None if a in (None, 0) or m is None else 100 * m / a for m, a in zip(mw, atr_w)]
    dx = [None if p is None or m is None else (0.0 if p + m == 0 else 100 * abs(p - m) / (p + m)) for p, m in zip(pdi, mdi)]
    fdx = next((i for i, v in enumerate(dx) if v is not None), n)
    adx = [None] * n
    if fdx < n:
        adx[fdx:] = _rma([v or 0.0 for v in dx[fdx:]], 14)
    S.update(pdi=pdi, mdi=mdi, adx=adx)
    # Bollinger width (20, 2) + ATR series for percentiles
    bbw = [None] * n
    for i in range(19, n):
        w = c[i - 19:i + 1]
        m = sum(w) / 20
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / 20)
        bbw[i] = 4 * sd / m if m else None
    S["bbw"] = bbw
    bbu, bbl = [None] * n, [None] * n
    for i in range(19, n):
        w = c[i - 19:i + 1]
        m = sum(w) / 20
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / 20)
        bbu[i], bbl[i] = m + 2 * sd, m - 2 * sd
    S["bb_up"], S["bb_lo"] = bbu, bbl
    S["ema200"] = _ema(c, 200)
    # v2.8.43 MMI (Market Meanness Index, 100 bars, lightly smoothed) and CMI (Choppy Market Index, 14 bars)
    mp, raw_mmi = 100, [None] * n
    for i in range(mp, n):
        w = c[i - mp:i + 1]
        med = sorted(w)[len(w) // 2]
        # Lotter: a value above the median followed by a lower one (or below / higher) = "mean" (reverting) move
        hit = sum(1 for k in range(1, len(w)) if (w[k - 1] > med and w[k] < w[k - 1]) or (w[k - 1] < med and w[k] > w[k - 1]))
        raw_mmi[i] = 100.0 * hit / (len(w) - 1)
    S["mmi"] = [None if i < mp + 4 or None in raw_mmi[i - 4:i + 1] else sum(raw_mmi[i - 4:i + 1]) / 5 for i in range(n)]
    cm = [None] * n
    for i in range(14, n):
        hh, ll = max(h[i - 14:i + 1]), min(lo[i - 14:i + 1])
        cm[i] = 0.0 if hh == ll else 100.0 * abs(c[i] - c[i - 14]) / (hh - ll)
    S["cmi"] = cm
    # Supertrend (10, 3)
    atr10 = _rma(tr, 10)
    st_dir, fu, fl = [0] * n, None, None
    for i in range(n):
        a = atr10[i]
        if a is None:
            continue
        mid = (h[i] + lo[i]) / 2
        bu, bl = mid + 3 * a, mid - 3 * a
        fu = bu if fu is None or bu < fu or c[i - 1] > fu else fu
        fl = bl if fl is None or bl > fl or c[i - 1] < fl else fl
        prev = st_dir[i - 1] if i else 0
        st_dir[i] = 1 if c[i] > fu else -1 if c[i] < fl else (prev or 1)
    S["st"] = st_dir
    # structure: last confirmed swing high / low (3 bars each side, confirmed 3 bars later) -> BOS bars
    k = 3
    sh = sl_ = None
    bos = [0] * n
    for i in range(n):
        j = i - k                                         # pivot at j is known at i
        if j >= k:
            if all(h[j] >= h[x] for x in range(j - k, j + k + 1) if x != j):
                sh = h[j]
            if all(lo[j] <= lo[x] for x in range(j - k, j + k + 1) if x != j):
                sl_ = lo[j]
        if i and sh is not None and c[i] > sh >= c[i - 1]:
            bos[i] = 1
        elif i and sl_ is not None and c[i] < sl_ <= c[i - 1]:
            bos[i] = -1
    S["bos"] = bos
    # MFI 14 (money flow: typical price x tick volume)
    mfi = [None] * n
    pos = [0.0] * n
    neg = [0.0] * n
    for i in range(1, n):
        f = tp[i] * v[i]
        if tp[i] > tp[i - 1]:
            pos[i] = f
        elif tp[i] < tp[i - 1]:
            neg[i] = f
    for i in range(14, n):
        ps, ns = sum(pos[i - 13:i + 1]), sum(neg[i - 13:i + 1])
        mfi[i] = 100.0 if ns == 0 and ps > 0 else 50.0 if ns == 0 else 100 - 100 / (1 + ps / ns)
    S["mfi"] = mfi
    # candle body / range (for doji)
    S["body"] = [abs(b.c - b.o) for b in bars]
    S["range"] = [b.h - b.l for b in bars]
    # order blocks (exits.order_blocks: created at the break, active until broken - read with active_obs(i))
    S["obs"] = exits.order_blocks(bars)
    S["mtf"], sec = _mtf(bars, mtf)
    S["sec"] = sec
    S["of"] = _orderflow(bars, sec)
    S["t_close"] = [b.t + sec for b in bars]
    S["cvd"] = _cvd_prefix(bars, S)
    return S


def _delta(b):
    """Estimated volume delta of one bar (same formula as the MT5 Master EA / REYGUN_CVD):
    tick volume x (close - open) / (high - low)."""
    rg = b.h - b.l
    return float(getattr(b, "v", 0) or 0) * (b.c - b.o) / rg if rg > 0 else 0.0


def _cvd_prefix(bars, S):
    """CVD source for the TREND 'cvd' condition: the M5 bars (finer, like the EA) when there are real M5 bars,
    else the strategy bars themselves. -> dict(closes, pre) with pre[k] = sum of the deltas of the first k bars."""
    m5 = (S.get("mtf") or {}).get("M5")
    src = m5["src"] if m5 and S.get("sec", 300) > 300 else bars
    tsec = 300 if src is not bars else S.get("sec", 300)
    pre = [0.0]
    for b in src:
        pre.append(pre[-1] + _delta(b))
    return dict(closes=[b.t + tsec for b in src], pre=pre, m5=src is not bars)


def cvd_change(S, i, n):
    """CVD change over the last n strategy bars ending at bar i (only bars CLOSED by bar i's close). None = no data."""
    import bisect
    c = S.get("cvd")
    if not c or i - n < 0:
        return None
    k1 = bisect.bisect_right(c["closes"], S["t_close"][i])
    k0 = bisect.bisect_right(c["closes"], S["t_close"][i - n])
    if k1 <= k0:
        return None
    return c["pre"][k1] - c["pre"][k0]


# ============================================================================ evaluation
def trend_dir(S, i, bias=0, strict=True):
    """Direction of the trend at bar i: the analyser's HTF bias when there is one, else the EMA structure.
    strict=False (pullback): EMA20 vs EMA50 + a rising / falling EMA50 is enough (price may dip below EMA50)."""
    if bias:
        return bias
    e20, e50, c = S["ema20"][i], S["ema50"][i], S["c"][i]
    if e20 is None or e50 is None:
        return 0
    if not strict:
        e50p = S["ema50"][i - 5] if i >= 5 else None
        if e50p is None:
            return 0
        return 1 if e20 > e50 and e50 > e50p else -1 if e20 < e50 and e50 < e50p else 0
    return 1 if e20 > e50 and c > e50 else -1 if e20 < e50 and c < e50 else 0


def mmi_cmi(S, i, kind, cfg):
    """v2.8.43 MMI / CMI regime check. Missing data (warm-up) passes, so it never blocks a young chart.
    trend: CMI >= cmi_trend OR MMI falling over mmi_lb bars.   rev (pullback): MMI >= mmi_rev OR CMI <= cmi_rev.
    chop (conso): CMI <= cmi_chop AND MMI not falling."""
    mm, cm = S.get("mmi"), S.get("cmi")
    if not mm or not cm or i >= len(mm) or mm[i] is None or cm[i] is None:
        return True
    lb = int(cfg.get("mmi_lb", 5))
    prev = mm[i - lb] if i - lb >= 0 else None
    falling = prev is not None and mm[i] < prev - 0.5
    if kind == "trend":
        return cm[i] >= cfg.get("cmi_trend", 45.0) or falling
    if kind == "rev":
        return mm[i] >= cfg.get("mmi_rev", 66.0) or cm[i] <= cfg.get("cmi_rev", 70.0)
    return cm[i] <= cfg.get("cmi_chop", 40.0) and not falling


def _checks(name, S, i, d, cfg, bars, bias):
    c = S["c"][i]
    a = S["atr"][i] or 1.0
    g = lambda key: S[key][i]                                       # noqa: E731
    out = {}
    if name == "trend":
        e20, e50 = g("ema20"), g("ema50")
        out["ema_align"] = e20 is not None and e50 is not None and (e20 - e50) * d > 0
        out["price_ema50"] = e50 is not None and (c - e50) * d > 0
        out["adx"] = (g("adx") or 0) >= cfg["adx_min"]
        p, m = g("pdi"), g("mdi")
        out["di"] = p is not None and m is not None and (p - m) * d > 0
        out["macd"] = (g("macd") or 0) * d > 0
        out["supertrend"] = S["st"][i] == d
        out["htf"] = bool(bias) and bias == d
        recent = S["bos"][max(0, i - 2):i + 1]                     # a BOS in the direction in the last 3 bars
        out["bos"] = d in recent
        r_ = g("rsi")
        out["rsi_side"] = r_ is not None and (r_ - 50) * d > 0
        e200 = S["ema200"][i]
        out["ema200"] = e200 is not None and (c - e200) * d > 0
        pr, _k = of_pressure(S, i, int(cfg.get("of_bars", 3)))
        out["of_buyers"] = pr is None or pr * d >= cfg.get("of_min", 15.0)       # no tick data: not judged
        out["mmi_cmi"] = mmi_cmi(S, i, "trend", cfg)
        out["not_extreme"] = r_ is not None and (r_ < cfg.get("rsi_hi", 70) if d == 1 else r_ > cfg.get("rsi_lo", 30))
        out["no_ob_block"] = ob_block(S, bars, i, d, cfg) is None
        m15 = mtf_state(S, i, cfg.get("bos_ltf", "M15"), cfg.get("bos_window", 32))
        h4 = mtf_state(S, i, cfg.get("bos_htf", "H4"), cfg.get("h4_window", 30))
        n15 = (m15["up"] if d == 1 else m15["dn"]) if m15 else 0
        out["bos_m15"] = bool(m15) and n15 >= int(cfg.get("bos_m15_min", 2)) and m15["last"] == d
        out["bos_h4"] = bool(h4) and h4["last"] == d and h4["age"] is not None and h4["age"] < cfg.get("h4_window", 30)
        m5 = mtf_state(S, i, "M5", cfg.get("m5_window", 32)) if S.get("sec", 300) > 300 else None
        out["m5_struct"] = m5 is None or m5["last"] == d                 # no M5 bars (backtest): not judged
        cv = cvd_change(S, i, 2 if cfg.get("cvd_mode") == "turn2" else int(cfg.get("cvd_bars", 8)))
        out["cvd"] = cv is None or cv * d > 0
    elif name == "pullback":
        r, rp = g("rsi"), S["rsi"][i - 1] if i else None
        os_, ob = cfg["os"], cfg["ob"]
        lo_w = [x for x in S["rsi"][max(0, i - 5):i + 1] if x is not None]
        # the dip AGAINST the trend: in an uptrend (d=1) RSI was oversold in the last 6 bars
        out["rsi"] = bool(lo_w) and (min(lo_w) <= os_ if d == 1 else max(lo_w) >= ob)
        st_w = [x for x in S["stoch"][max(0, i - 5):i + 1] if x is not None]
        out["stoch"] = bool(st_w) and (min(st_w) <= 20 if d == 1 else max(st_w) >= 80)
        cc = [x for x in S["cci"][max(0, i - 5):i + 1] if x is not None]
        out["cci"] = bool(cc) and (min(cc) <= -100 if d == 1 else max(cc) >= 100)
        ww = [x for x in S["wpr"][max(0, i - 5):i + 1] if x is not None]
        out["wpr"] = bool(ww) and (min(ww) <= -80 if d == 1 else max(ww) >= -20)
        near = [abs(bars[k].l - S["ema20"][k]) if d == 1 else abs(bars[k].h - S["ema20"][k])
                for k in range(max(0, i - 5), i + 1) if S["ema20"][k] is not None]
        near += [abs(bars[k].l - S["ema50"][k]) if d == 1 else abs(bars[k].h - S["ema50"][k])
                 for k in range(max(0, i - 5), i + 1) if S["ema50"][k] is not None]
        out["near_ema"] = bool(near) and min(near) <= 0.6 * a
        mf = [x for x in S["mfi"][max(0, i - 5):i + 1] if x is not None] if "mfi" in S else []
        out["mfi"] = bool(mf) and (min(mf) <= cfg.get("mfi_os", 20.0) if d == 1 else max(mf) >= cfg.get("mfi_ob", 80.0))
        # doji at the end of the dip: tiny body vs its range (and not a dead flat bar), on this bar or the one before
        dj = False
        for k in (i, i - 1):
            if k < 0 or "body" not in S:
                continue
            rg = S["range"][k]
            if rg > 0.25 * a and S["body"][k] <= cfg.get("doji_body", 0.15) * rg:
                dj = True
        out["doji"] = dj
        out["ob"] = ob_hold(S, bars, i, d, cfg) is not None
        # v2.8.21: a SUDDEN SPIKE into the extreme (no OB needed): the last 6 bars moved >= spike_atr x ATR, the
        # high came first and the low last for a BUY (a drop), the low first and the high last for a SELL
        w = range(max(0, i - 5), i + 1)
        hi_k = max(w, key=lambda k: bars[k].h)
        lo_k = min(w, key=lambda k: bars[k].l)
        mv = bars[hi_k].h - bars[lo_k].l
        out["spike"] = mv >= cfg.get("spike_atr", 3.0) * a and ((d == 1 and hi_k < lo_k) or (d == -1 and lo_k < hi_k))
        out["ob_spike"] = out["ob"] or out["spike"]
        # the turn on THIS bar: RSI crosses back through os / ob, or Stochastic %K through 20 / 80
        kk, kp = S["stoch"][i], S["stoch"][i - 1] if i else None
        rsi_turn = r is not None and rp is not None and ((d == 1 and rp <= os_ < r) or (d == -1 and rp >= ob > r))
        st_turn = kk is not None and kp is not None and ((d == 1 and kp <= 20 < kk) or (d == -1 and kp >= 80 > kk))
        out["turn"] = rsi_turn or st_turn
        pr, _k = of_pressure(S, i, 2)
        out["of_turn"] = pr is None or pr * d >= cfg.get("of_min", 10.0)
        out["bb_touch"] = any(
            (S["bb_lo"][k] is not None and bars[k].l <= S["bb_lo"][k]) if d == 1 else
            (S["bb_up"][k] is not None and bars[k].h >= S["bb_up"][k]) for k in range(max(0, i - 5), i + 1))

        def _eng(k):
            if k < 1:
                return False
            b, pb = bars[k], bars[k - 1]
            if d == 1:
                return b.c > b.o and pb.c < pb.o and b.c >= pb.o and b.o <= pb.c
            return b.c < b.o and pb.c > pb.o and b.c <= pb.o and b.o >= pb.c
        out["engulf"] = _eng(i) or _eng(i - 1)
        out["mmi_cmi"] = mmi_cmi(S, i, "rev", cfg)
    return out


def _score(name, cfg, met):
    on = [k for k, v in cfg["conditions"].items() if v]
    if not on:
        return 0.0, []
    return 100.0 * sum(1 for k in on if met.get(k)) / len(on), on


def route_zone(S, i, cfg):
    """v2.8.45 CMI ROUTER (Eugene): ONE strategy per market condition. CMI >= route_hi -> TREND, CMI <= route_lo ->
    CONSO, in between -> PULLBACK. None while CMI is warming up (then every strategy may trade, as before)."""
    cm = S.get("cmi")
    if not cm or i >= len(cm) or cm[i] is None:
        return None
    if cm[i] >= cfg.get("route_hi", 55.0):
        return "trend"
    if cm[i] <= cfg.get("route_lo", 35.0):
        return "conso"
    return "pullback"


def evaluate(name, bars, S, i, cfg, bias=0, box=None):
    e = _evaluate(name, bars, S, i, cfg, bias, box)
    if e.get("ok") and cfg.get("route", True):
        z = route_zone(S, i, cfg)
        if z is not None and z != name:
            cm = S["cmi"][i]
            e = dict(e, ok=False, missing=list(e.get("missing") or []) + ["route"], routed_to=z,
                     why=f"CMI router: CMI {cm:.0f} → {TITLE[z]} ang may hawak (TREND ≥ {cfg.get('route_hi', 55.0):g}, "
                         f"CONSO ≤ {cfg.get('route_lo', 35.0):g}, PULLBACK sa gitna)")
    return e


def _evaluate(name, bars, S, i, cfg, bias=0, box=None):
    """One strategy on CLOSED bar i -> dict(ok, side, score, met{}, on[], missing_required[], why, plan...)."""
    if i < 60 or i >= len(bars):
        return dict(ok=False, side=0, score=0.0, met={}, on=[], missing=["data"], why="hindi pa sapat ang bars")
    if name == "conso":
        return _eval_conso(bars, S, i, cfg, box)
    extra = {}
    if name == "trend" and cfg["conditions"].get("bos_h4"):
        # the trend IS the latest H4 break of structure (when that rule is on)
        h4 = mtf_state(S, i, cfg.get("bos_htf", "H4"), cfg.get("h4_window", 30))
        m15 = mtf_state(S, i, cfg.get("bos_ltf", "M15"), cfg.get("bos_window", 32))
        d = h4["last"] if h4 and h4["last"] and h4["age"] is not None and h4["age"] < cfg.get("h4_window", 30) else 0
        extra = dict(ltf=cfg.get("bos_ltf", "M15"), htf=cfg.get("bos_htf", "H4"), h4_bos=h4["last"] if h4 else 0, h4_age=h4["age"] if h4 else None,
                     m15_up=m15["up"] if m15 else 0, m15_dn=m15["dn"] if m15 else 0,
                     m15_last=m15["last"] if m15 else 0)
        if not d:
            return dict(ok=False, side=0, score=0.0, met={}, on=[], missing=["bos_h4"],
                        why=f"walang {cfg.get('bos_htf', 'H4')} BOS sa huling {cfg.get('h4_window', 30)} kandila (walang trend)",
                        **extra)
    elif name == "pullback" and cfg.get("any_dir", False):
        # v2.8.18: no trend / HTF needed: the side whose extreme is there (RSI oversold -> BUY, overbought -> SELL);
        # when neither, the trend side (for the panel / progress only)
        d = 0
        for dd in (1, -1):
            if _checks(name, S, i, dd, cfg, bars, bias).get("rsi"):
                d = dd
                break
        d = d or trend_dir(S, i, bias, strict=False) or 1
    else:
        d = trend_dir(S, i, bias, strict=name != "pullback")
    if not d:
        return dict(ok=False, side=0, score=0.0, met={}, on=[], missing=["trend"], why="walang trend (HTF bias / EMA)")
    met = _checks(name, S, i, d, cfg, bars, bias)
    score, on = _score(name, cfg, met)
    extra.update(count=sum(1 for k in on if met.get(k)), count_of=len(on))
    missing = [k for k in cfg["required"] if not met.get(k)]
    why = ""
    if name == "pullback" and cfg.get("block_volatile", True):
        pct = _rank(S["atr"], i)
        if pct is not None and pct >= cfg["vol_pct"]:
            missing.append("volatile")
            why = f"VOLATILE: ATR percentile {pct:.0f} ≥ {cfg['vol_pct']:.0f} (baka magtuloy-tuloy ang galaw)"
    ok = not missing and score >= cfg["min_pct"]
    a = S["atr"][i] or 1.0
    entry = bars[i].c
    if name == "trend":
        sl = entry - d * cfg["sl_atr"] * a
        if cfg.get("sl_mode", "atr") == "swing":
            # SL at the LAST SWING (Eugene, 2026-10-03): beyond the latest confirmed swing low (BUY) / high (SELL)
            # + swing_buf x ATR, at least swing_min x ATR, capped at swing_max x ATR. No swing found: ATR SL.
            sw = last_swing(bars, i, d)
            if sw is not None:
                dist = abs(entry - sw) + cfg.get("swing_buf", 0.1) * a
                dist = max(dist, cfg.get("swing_min", 0.5) * a)
                if cfg.get("swing_max", 4.0) > 0:
                    dist = min(dist, cfg.get("swing_max", 4.0) * a)
                sl = entry - d * dist
                extra.update(sl_from="swing", swing=round(sw, 2))
    else:                                                        # pullback: beyond the dip extreme (or ATR)
        ext = min(b.l for b in bars[max(0, i - 5):i + 1]) if d == 1 else max(b.h for b in bars[max(0, i - 5):i + 1])
        o = ob_hold(S, bars, i, d, cfg) if met.get("ob") else None
        if o is not None:                                        # the OB is the invalidation: SL just beyond it
            ext = min(ext, o["lo"]) if d == 1 else max(ext, o["hi"])
            extra.update(ob_lo=round(o["lo"], 2), ob_hi=round(o["hi"], 2))
        sl = ext - d * 0.1 * a
        if (entry - sl) * d < 0.5 * a or (entry - sl) * d > (6 if o is not None else 3) * a:
            sl = entry - d * cfg["sl_atr"] * a
    risk = abs(entry - sl)
    tp = entry + d * risk * cfg["rr"]
    return dict(ok=ok, side=d, score=round(score, 1), met=met, on=on, missing=missing, why=why,
                entry=round(entry, 2), sl=round(sl, 2), tp=round(tp, 2), rr=cfg["rr"], **extra)


def last_swing(bars, i, d, k=3, look=60):
    """The most recent CONFIRMED swing (k bars each side, known k bars later, so no look-ahead) on the strategy
    bars before bar i: a swing LOW below the close for a BUY (d=1), a swing HIGH above it for a SELL. None if
    there is none in the last `look` bars."""
    c = bars[i].c
    for j in range(i - k, max(k, i - look) - 1, -1):
        if d == 1 and bars[j].l < c and all(bars[j].l <= bars[x].l for x in range(j - k, j + k + 1) if x != j):
            return bars[j].l
        if d == -1 and bars[j].h > c and all(bars[j].h >= bars[x].h for x in range(j - k, j + k + 1) if x != j):
            return bars[j].h
    return None


def _orderflow(bars, sec):
    """Per strategy bar: (buy, sell) tick volume from the live order flow (orderflow.CURRENT, MT5 ticks, tick rule).
    None for bars without tick data (backtest, demo history before start): the ORDER FLOW conditions are then
    simply not met. Only the bar's own closed data is used (no look-ahead)."""
    try:
        import orderflow
        of = orderflow.CURRENT
        tf = next((k for k, v in TF_SEC_NAMES.items() if v == sec), None)
        if of is None or tf not in orderflow.TFS:
            return None
        st = {x[0]: (x[1], x[2]) for x in of.bar_stats(tf)}
        return [st.get(b.t) for b in bars]
    except Exception:
        return None


TF_SEC_NAMES = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}


def of_pressure(S, i, n):
    """(delta % of volume over the last n bars, bars with data). + = buyers stronger, - = sellers stronger."""
    of = S.get("of")
    if not of:
        return None, 0
    b = s_ = 0.0
    k = 0
    for j in range(max(0, i - n + 1), i + 1):
        x = of[j]
        if x:
            b, s_, k = b + x[0], s_ + x[1], k + 1
    if not k or b + s_ <= 0:
        return None, 0
    return 100.0 * (b - s_) / (b + s_), k


def obs_at(S, i, tf):
    """(order blocks on `tf`, index of the last `tf` bar CLOSED by strategy bar i). The strategy TF itself, or the
    real / resampled higher-TF bars (no look-ahead)."""
    m = (S.get("mtf") or {}).get(tf)
    if S.get("sec") == MTF_TFS.get(tf) or not m:
        return S.get("obs") or [], i
    import bisect
    cache = S.setdefault("_ob_cache", {})
    if tf not in cache:
        cache[tf] = exits.order_blocks(m["src"])
    return cache[tf], bisect.bisect_right(m["closes"], S["t_close"][i]) - 1


def ob_block(S, bars, i, d, cfg):
    """TREND kontra (Eugene, 2026-10-01): a BUY with a bearish order block just ABOVE (price inside it, or below it
    within ob_block_atr x ATR) is blocked - "wag mag-BUY sa ilalim ng OB". Once an ob_tf candle CLOSES through it the OB
    is broken (inactive) and the BUY may go. SELL = mirror (bullish OB just below). Returns the blocking OB or None."""
    obs, idx = obs_at(S, i, cfg.get("ob_tf", "M15"))
    if idx < 0:
        return None
    a = S["atr"][i] or 1.0
    room = cfg.get("ob_block_atr", 1.5) * a
    c = S["c"][i]
    best = None
    for o in exits.active_obs(obs, idx):
        if o["side"] != -d:
            continue
        if d == 1 and o["hi"] >= c and o["lo"] - c <= room:           # supply above (or we are inside it)
            best = o if best is None or o["lo"] < best["lo"] else best
        if d == -1 and o["lo"] <= c and c - o["hi"] <= room:          # demand below (or we are inside it)
            best = o if best is None or o["hi"] > best["hi"] else best
    return best


def ob_hold(S, bars, i, d, cfg):
    """PULLBACK order block (Eugene, 2026-10-01): SELL in a downtrend = the rally went UP into a bearish order block
    ABOVE (high within ob_near x ATR of it, last 6 bars) and that OB is NOT broken on `ob_tf` (M15: no M15 close above
    it); BUY = mirror (dip into a bullish OB below, no M15 close below it). Uses only higher-TF bars closed by the
    strategy bar's close (no look-ahead). Returns the OB or None."""
    a = S["atr"][i] or 1.0
    near = cfg.get("ob_near", 0.3) * a
    seg = bars[max(0, i - 5):i + 1]
    ext = min(b.l for b in seg) if d == 1 else max(b.h for b in seg)
    tf = cfg.get("ob_tf", "M15")
    m = (S.get("mtf") or {}).get(tf)
    if S.get("sec") == MTF_TFS.get(tf) or not m:
        obs, idx = S.get("obs") or [], i                         # the strategy TF is the OB TF (or no M15 data)
    else:
        import bisect
        cache = S.setdefault("_ob_cache", {})
        if tf not in cache:
            cache[tf] = exits.order_blocks(m["src"])
        obs = cache[tf]
        idx = bisect.bisect_right(m["closes"], S["t_close"][i]) - 1   # the last CLOSED ob_tf bar
        if idx < 0:
            return None
    c = bars[i].c
    best = None
    for o in exits.active_obs(obs, idx):                        # active = created and never closed through
        if o["side"] != d:
            continue
        if d == -1 and ext >= o["lo"] - near and c <= o["hi"]:   # tagged the supply above, still below its top
            best = o if best is None or o["lo"] < best["lo"] else best
        if d == 1 and ext <= o["hi"] + near and c >= o["lo"]:    # tagged the demand below, still above its bottom
            best = o if best is None or o["hi"] > best["hi"] else best
    return best


def _eval_conso(bars, S, i, cfg, box=None):
    """Low ADX + low volume (+ optional squeeze / quiet ATR / a real box) -> a GRID of pending stops around the
    range: the consolidation box when one is found, else the high / low of the last `range_bars` closed bars.
    levels per side, `step` $ apart, every order SL = sl_dist, TP = sl_dist x RR."""
    if box is None:
        box = conso.detect(bars, end=i + 1, **{k: (cfg[k] if k != "max_range" or cfg[k] > 0 else 1e9) for k in (
            "lookback", "swing", "min_touches", "tol_pips", "pip", "min_bars", "max_range", "min_range")})
    met = {"box": bool(box and box.get("ok"))}
    bp = _rank(S["bbw"], i)
    met["squeeze"] = bp is not None and bp <= 30
    # ADX < adx_max AND low volume on EVERY one of the last quiet_bars closed candles (not just the latest)
    qb = max(1, int(cfg.get("quiet_bars", 4)))
    win = range(max(0, i - qb + 1), i + 1)
    met["adx_low"] = i - qb + 1 >= 0 and all((S["adx"][k] if S["adx"][k] is not None else 99) < cfg["adx_max"] for k in win)
    ap = _rank(S["atr"], i)
    met["atr_low"] = ap is not None and ap <= 50
    pr, _k = of_pressure(S, i, int(cfg.get("of_bars", 5)))
    met["of_balanced"] = pr is None or abs(pr) < cfg.get("of_min", 15.0)
    rr_ = S["rsi"][i]
    met["rsi_mid"] = rr_ is not None and 40 <= rr_ <= 60
    met["mmi_cmi"] = mmi_cmi(S, i, "chop", cfg)
    met["inside_bb"] = all(S["bb_up"][k] is not None and S["bb_lo"][k] <= bars[k].c <= S["bb_up"][k]
                           for k in range(max(0, i - max(1, int(cfg.get("quiet_bars", 1))) + 1), i + 1))
    vp = _rank(S["vol"], i)
    vps = [_rank(S["vol"], k) for k in win]
    met["vol_low"] = i - qb + 1 >= 0 and all(v is not None and v <= cfg["vol_pct"] for v in vps)
    quiet_n = 0
    for k in range(i, -1, -1):                                       # how many candles in a row are quiet now
        rk = _rank(S["vol"], k)
        if (S["adx"][k] if S["adx"][k] is not None else 99) < cfg["adx_max"] and rk is not None and rk <= cfg["vol_pct"]:
            quiet_n += 1
            if quiet_n >= 50:
                break
        else:
            break
    score, on = _score("conso", cfg, met)
    missing = [k for k in cfg["required"] if not met.get(k)]
    count, count_of = sum(1 for k in on if met.get(k)), len(on)
    # the range the grid goes around
    if met["box"]:
        hi, lo, src, t_from = box["high"], box["low"], "box", box.get("t_from")
    elif cfg["conditions"].get("box"):
        missing = list(dict.fromkeys(missing + ["box"]))         # pendings go around the BOX: no box, no orders
        k0 = max(0, i - int(cfg["range_bars"]) + 1)
        hi, lo, src, t_from = max(b.h for b in bars[k0:i + 1]), min(b.l for b in bars[k0:i + 1]), "range", bars[k0].t
    else:
        k0 = max(0, i - int(cfg["range_bars"]) + 1)
        hi, lo, src, t_from = max(b.h for b in bars[k0:i + 1]), min(b.l for b in bars[k0:i + 1]), "range", bars[k0].t
    rng = hi - lo
    why = ""
    if cfg["max_range"] > 0 and rng > cfg["max_range"]:
        missing.append("range")
        why = f"range ${rng:.2f} > max ${cfg['max_range']:.2f} (hindi siksik)"
    ok = not missing and score >= cfg["min_pct"]
    out = dict(ok=ok, side=0, score=round(score, 1), met=met, on=on, missing=missing, why=why, pending=True,
               range_src=src, vol_pct=None if vp is None else round(vp), adx=None if S["adx"][i] is None else round(S["adx"][i], 1),
               quiet_n=quiet_n, quiet_bars=qb, count=count, count_of=count_of)
    buf, n_lv, step = cfg["buffer"], max(1, int(cfg["levels"])), float(cfg["step"])
    bs, ss = hi + buf, lo - buf
    sl_d = min(bs - (lo - buf), cfg["max_sl"]) if cfg["max_sl"] > 0 else bs - (lo - buf)
    grid = [dict(side=1, level=k, entry=round(bs + k * step, 2)) for k in range(n_lv)] + \
           [dict(side=-1, level=k, entry=round(ss - k * step, 2)) for k in range(n_lv)]
    for g in grid:
        g["sl"] = round(g["entry"] - g["side"] * sl_d, 2)
        g["tp"] = round(g["entry"] + g["side"] * sl_d * cfg["rr"], 2)
    out.update(box_high=round(hi, 2), box_low=round(lo, 2), box_from=t_from, levels=n_lv, step=step, grid=grid,
               buy_stop=round(bs, 2), sell_stop=round(ss, 2), sl_dist=round(sl_d, 2),
               tp_dist=round(sl_d * cfg["rr"], 2), rr=cfg["rr"],
               buy_sl=round(bs - sl_d, 2), buy_tp=round(bs + sl_d * cfg["rr"], 2),
               sell_sl=round(ss + sl_d, 2), sell_tp=round(ss - sl_d * cfg["rr"], 2))
    return out


def signals(name, bars, cfg, bias_fn=None, lookback=300, S=None, mtf=None):
    """Every signal of one strategy over the last `lookback` CLOSED bars (the last bar is still forming).
    Trend / pullback: one per fresh OK (then `cooldown` bars quiet). Conso: one pending plan per box."""
    n = len(bars)
    if n < 80:
        return []
    S = S or series(bars, mtf)
    out, last_i, last_box, prev_ok = [], -10 ** 9, None, 0
    for i in range(max(60, n - 1 - lookback), n - 1):
        bias = bias_fn(i) if bias_fn else 0
        if name == "conso":
            e = _eval_conso(bars, S, i, cfg)
            if not e["ok"]:
                last_box = None                                    # the quiet spell ended
                continue
            if last_box is not None and i - last_box < int(cfg.get("expiry_bars", 8)):
                continue                                           # one grid per quiet spell (until it expires)
            last_box = i
            out.append(dict(t=bars[i].t, i=i, side=0, pending=True, strategy=name, kind="CONSO GRID",
                            score=e["score"], cautions=[], entry=e["buy_stop"], sl=e["buy_sl"], tp=e["buy_tp"],
                            plan=e, why_for=[k for k in e["on"] if e["met"].get(k)],
                            why_against=[k for k in e["on"] if not e["met"].get(k)]))
            continue
        e = evaluate(name, bars, S, i, cfg, bias)
        was = prev_ok
        prev_ok = e["ok"] and e["side"]
        if not e["ok"] or was == e["side"] or i - last_i < int(cfg.get("cooldown", 6)):
            continue                                       # only when it TURNS ok (fresh), then a quiet period
        last_i = i
        out.append(dict(t=bars[i].t, i=i, side=e["side"], strategy=name, kind=TITLE[name], score=e["score"],
                        cautions=[], entry=e["entry"], sl=e["sl"], tp=e["tp"], rr=e["rr"],
                        why_for=[k for k in e["on"] if e["met"].get(k)],
                        why_against=[k for k in e["on"] if not e["met"].get(k)]))
    return out


def label(name, key):
    return next((lab for k, lab, _ in CATALOG[name] if k == key), key)


def hub_mtf(hub):
    """The real M15 / H4 bars of the hub (for the trend strategy's BOS rules); None -> resampled."""
    try:
        return {tf: hub.src.bars(tf) for tf, _ in MTF}
    except Exception:
        return None


# ============================================================================ live status (Trading page)
def status(hub, tf=None, cfg=None):
    """Every strategy evaluated on the last CLOSED bar of the AUTO timeframe: conditions met / missing, score vs
    the strategy's passing %, and the plan (entry / SL / TP or the box stops). Read-only."""
    cfg = cfg or load_settings()
    at = getattr(hub, "autotrader", None)
    tf = tf or ((at.settings.get("tf") if at is not None else None) or "M15")
    bars = hub.src.bars(tf)[:-1]                                   # closed bars only
    out = dict(tf=tf, settings=cfg, catalog={n: [dict(key=k, label=lab) for k, lab, _ in CATALOG[n]] for n in NAMES},
               titles=TITLE, strategies={},
               profiles={k: dict(title=v["title"], desc=v["desc"], auto_tf=v["auto_tf"], htf_filter=v["htf_filter"],
                                 protect=v["protect"])
                         for k, v in PROFILES.items() if k in PROFILE_BUTTONS})
    if len(bars) < 80:
        out["error"] = "hindi pa sapat ang bars"
        return out
    S = series(bars, hub_mtf(hub))
    i = len(bars) - 1
    r = getattr(hub, "read", None)
    bias = getattr(r, "bias", 0) or 0
    for n in NAMES:
        e = evaluate(n, bars, S, i, cfg[n], bias)
        e = dict(e)
        e["labels"] = {k: label(n, k) for k in cfg[n]["conditions"]}
        e["t"] = bars[i].t
        if n == "pullback" and cfg[n].get("block_volatile", True):
            e["atr_pct"] = _rank(S["atr"], i)
        out["strategies"][n] = e
    try:
        price = float(hub.src.bars(tf)[-1].c)
    except Exception:
        price = bars[-1].c
    out["price"] = round(price, 2)
    for n in NAMES:
        out["strategies"][n]["near"] = _near(n, out["strategies"][n], S, i, cfg[n], price)
    # the last SIGNAL of each strategy (AUTO takes a signal only on the bar where it turns READY - a READY that has
    # been on for several bars was already signalled, it is not a new entry)
    try:
        full = hub.src.bars(tf)
        Sf = series(full, hub_mtf(hub))
        for n in NAMES:
            sg = signals(n, full, cfg[n], (lambda _i: bias) if n != "conso" else None, 60, S=Sf)
            last = sg[-1] if sg else None
            out["strategies"][n]["last_signal"] = None if last is None else dict(
                t=last["t"], side=last["side"], fresh=last["t"] == bars[-1].t, bars_ago=len(bars) - 1 - last["i"])
    except Exception as e:
        out["signal_error"] = f"{e.__class__.__name__}: {e}"
    if at is not None:                                              # why a READY strategy did not trade (yet)
        import time as _t
        for n in NAMES:
            ev = next((x for x in list(at.log) if f"st_{n}" in x.get("text", "") and
                       x.get("decision") in ("skip", "trade", "error") and _t.time() - x.get("t", 0) < 900), None)
            if ev:
                out["strategies"][n]["last_auto"] = dict(t=int(ev["t"]), decision=ev.get("decision"),
                                                         text=ev.get("text", "")[:220])
    pr, k = of_pressure(S, i, 3)
    out["orderflow"] = None if pr is None else dict(pressure=round(pr, 1), bars=k)
    for n in NAMES:                                       # what order flow means for THIS strategy, right now
        c = cfg[n]
        nb = 2 if n == "pullback" else int(c.get("of_bars", 3 if n == "trend" else 5))
        p_, _k = of_pressure(S, i, nb)
        e = out["strategies"][n]
        d = e.get("side") or 0
        need = c.get("of_min", 10.0 if n == "pullback" else 15.0)
        role = {"trend": "kasabay ng trend ang mas malakas", "pullback": "bumalik na ang buyers / sellers sa dulo ng pullback",
                "conso": "balanse ang buyers at sellers"}[n]
        e["of"] = dict(on=bool(c["conditions"].get(OF_KEY[n])), key=OF_KEY[n], pct=None if p_ is None else round(p_, 1),
                       bars=nb, need=need, side=d, role=role,
                       ok=(e.get("met") or {}).get(OF_KEY[n]), nodata=p_ is None)
    out["news"] = news_status(hub)
    at_on = bool(at is not None and getattr(at, "enabled", False))
    tm = getattr(hub, "trader", None)
    out["auto"] = dict(enabled=at_on, tf=tf, strategies={n: bool(cfg[n].get("auto")) for n in NAMES},
                       trading=tm is not None, armed=bool(tm is not None and getattr(tm, "armed", False)),
                       paper=bool(tm is not None and getattr(tm, "paper", False)),
                       sources=list((at.settings.get("sources") or []) if at is not None else []))
    return out


def _near(name, e, S, i, cfg, price):
    """How close a strategy is to firing, for the side panel: progress 0-100 (checked conditions met: all of them
    are needed), the price that would complete it (when there is one) and the $ distance from the current price."""
    req = e.get("on") or [k for k, v in cfg.get("conditions", {}).items() if v] or cfg.get("required") or []
    met = e.get("met") or {}
    have = sum(1 for k in req if met.get(k))
    out = dict(progress=round(100.0 * have / len(req)) if req else 0, have=have, need=len(req), level=None,
               dist=None, text="")
    if e.get("ok"):
        out["progress"] = 100
    if name == "trend" and not (cfg.get("conditions", {}).get("bos_m15") or cfg.get("conditions", {}).get("bos_h4")):
        mt = e.get("met") or {}
        if e.get("side") and mt.get("no_ob_block") is False:
            o = ob_block(S, None, i, e["side"], cfg)
            if o is not None:
                d_ = e["side"]
                lvl = o["hi"] if d_ == 1 else o["lo"]
                out.update(level=round(lvl, 2), dist=round((lvl - price) * d_, 2),
                           text=f"KONTRA: OB {o['lo']:.2f}–{o['hi']:.2f} sa daan · {cfg.get('ob_tf', 'M15')} close "
                                f"{'above' if d_ == 1 else 'below'} {lvl:.2f} = tuloy")
    elif name == "trend":
        d = e.get("h4_bos") or 0
        ltf, htf = cfg.get("bos_ltf", "M15"), cfg.get("bos_htf", "H4")
        m15 = mtf_state(S, i, ltf, cfg.get("bos_window", 32))
        h4 = mtf_state(S, i, htf, cfg.get("h4_window", 30))
        nb = int(cfg.get("bos_m15_min", 2))
        if d and m15:
            got = m15["up"] if d == 1 else m15["dn"]
            lvl = m15["swing_high"] if d == 1 else m15["swing_low"]
            # the M15 BOS count is part of the progress: 1 of 2 BOS = half of that condition
            part = (1 + min(got, nb) / nb) / 2 if not e.get("ok") else 1
            out["progress"] = round(100 * part)
            if lvl is not None and not e.get("ok"):
                out.update(level=round(lvl, 2), dist=round((lvl - price) * d, 2),
                           text=f"{ltf} BOS {'pataas' if d == 1 else 'pababa'} ({min(got, nb)}/{nb}): "
                                f"close {'above' if d == 1 else 'below'} {lvl:.2f}")
        met_ = e.get("met") or {}
        if d and met_.get("bos_m15") and met_.get("bos_h4") and not e.get("ok"):
            o = ob_block(S, None, i, d, cfg) if met_.get("no_ob_block") is False else None
            if o is not None:
                lvl = o["hi"] if d == 1 else o["lo"]
                out.update(level=round(lvl, 2), dist=round((lvl - price) * d, 2),
                           text=f"KONTRA: {'bearish' if d == 1 else 'bullish'} OB {o['lo']:.2f}–{o['hi']:.2f} sa daan · "
                                f"{cfg.get('ob_tf', 'M15')} close {'above' if d == 1 else 'below'} {lvl:.2f} = tuloy")
            elif met_.get("not_extreme") is False:
                out["text"] = f"KONTRA: {'overbought' if d == 1 else 'oversold'} (RSI) · hintayin lumamig"
        if not (d and m15) and h4:
            hi, lo = h4["swing_high"], h4["swing_low"]
            cands = [(abs(x - price), x, s_) for x, s_ in ((hi, 1), (lo, -1)) if x is not None]
            if cands:
                _, lvl, s_ = min(cands)
                out.update(level=round(lvl, 2), dist=round(abs(lvl - price), 2),
                           text=f"{htf} BOS: {htf} close {'above' if s_ == 1 else 'below'} {lvl:.2f}")
    elif name == "conso":
        out["progress"] = round(100 * (e.get("count") or 0) / max(1, e.get("count_of") or 3))
        if e.get("ok"):
            bs, ss = e.get("buy_stop"), e.get("sell_stop")
            if bs is not None and ss is not None:
                db, ds = round(bs - price, 2), round(price - ss, 2)
                lvl, dist, sd = (bs, db, "BUY STOP") if db <= ds else (ss, ds, "SELL STOP")
                out.update(level=lvl, dist=dist, text=f"{sd} {lvl:.2f}")
        else:
            out["text"] = f"tahimik {e.get('quiet_n', 0)}/{e.get('quiet_bars', 4)} kandila"
    elif name == "pullback":
        r = S["rsi"][i]
        mf = S.get("mfi", [None])[i] if S.get("mfi") else None
        d = e.get("side") or 0
        if r is not None and d:
            tgt = cfg.get("os", 30) if d == 1 else cfg.get("ob", 70)
            out["text"] = f"RSI {r:.0f} (kailangan {'≤' if d == 1 else '≥'} {tgt:g})" + \
                          (f" · MFI {mf:.0f}" if mf is not None else "")
    return out


def news_status(hub):
    """NEWS row of the side panel: the next high-impact event of your news filter (UTC epoch, the page counts
    down) and whether NEWS TRADE is on, with its timings. Read-only."""
    import time as _t
    nf, nt = getattr(hub, "news", None), getattr(hub, "newstrader", None)
    out = dict(next=None, enabled=bool(nt is not None and getattr(nt, "enabled", False)), available=nt is not None)
    if nt is not None:
        st = nt.settings
        out.update(place_before_sec=st.get("place_before_sec"), close_before_sec=st.get("close_before_sec"),
                   close_before_news=bool(st.get("close_before_news")), plan=getattr(nt, "plan", None))
    if nf is None:
        out["error"] = "news reader off"
        return out
    try:
        s = nf.settings
        cur, imp = set(s.get("currencies") or []), set(s.get("warn_impacts") or [])
        now = _t.time()
        with nf.lock:
            for e in nf.calendar:
                if e["t"] > now - 300 and (not cur or e["currency"] in cur) and (not imp or e["impact"] in imp):
                    out["next"] = dict(title=e["title"], currency=e["currency"], impact=e["impact"], t=e["t"])
                    break
    except Exception as e:
        out["error"] = f"{e.__class__.__name__}"
    out["now"] = int(_t.time())
    return out
