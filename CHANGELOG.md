# REYGUN SuperChart: changelog

## v2.8.49 (build 2026-10-09.49)
**Balik sa setup na kumikita hanggang Oct 7** (Eugene: "ok pa ang trade natin hanggang Oct 7, Oct 8 dun na" → "ibalik
sa dati").
- **Dahilan:** hanggang v2.8.43 may kasamang lumang `strategy_settings.json` ang zip, kaya ito ang talagang tumatakbo
  sa VPS: **HTF filter ON**, **ORDER FLOW required** sa TREND / PULLBACK / CONSO, PULLBACK RSI 40/60, CONSO box max $10.
  Noong Oct 8 nawala ang mga ito (v2.8.44: walang file + pinindot ang NORMAL; v2.8.45: order flow "info lang" + CMI
  router). Mas maraming trade ang nakapasok sa range market.
- **Ibinalik:** ang lumang TREND (H4 BOS + 2 M15 BOS + order flow + hindi overbought/oversold + MMI/CMI, SL sa swing),
  HTF filter ON, order flow REQUIRED, PULLBACK 40/60, CONSO max box $10, **walang CMI router**. Inalis ang BOS retest
  TREND (v2.8.46–47) at ang `max_range_atr` (v2.8.48; ang $10 max box ang dating proteksyon).
- **NORMAL = ang setup na ito na** (para hindi na mawala kapag pinindot ang NORMAL). Isang beses ina-apply sa
  kasalukuyang settings (schema 26); naiwan ang AUTO switches.
- **Naiwan** mula v2.8.48: CONSO failed-breakout exit at isang talo lang bawat box (may switch sa AUTO TRADE → MASTER).
- Forecast: naiwan ang `values` snapshot (read-only).

## v2.8.48 (build 2026-10-09.48)
CONSO sa RANGE (Eugene, 2026-10-08: range 4113–4133, SELL STOP napuno sa ilalim −$19.83, BUY STOP sa itaas −$20.09).
- **Malapad na box = range, hindi siksik** (`max_range_atr` 4.0; AGGRESSIVE 5.0): walang CONSO grid kapag ang box ay
  mas malapad sa 4 × ang karaniwang ATR (average ng huling 100 kandila). Panel: "malapad ang box: $20 > 4 × …".
- **Failed breakout exit** (`conso_fail_exit`, ON): kapag napuno ang BUY / SELL STOP pero ang susunod na M15 close ay
  BUMALIK sa loob ng box (≥ 0.2 × ATR), isasara agad = maliit na talo imbes na buong SL sa kabilang dulo ng box.
- **Isang talo lang bawat box** (`conso_one_loss`, ON): kapag natalo ang isang CONSO trade ng box, wala nang bagong
  grid sa parehong box hanggang may bagong box.
- Lahat ay may switch sa AUTO TRADE → MASTER at sa CONSO settings.

## v2.8.47 (build 2026-10-08.47)
Range filter sa TREND (Eugene: "may prob tayo sa ganitong market", M15 range 4110–4140: BUY sa tuktok ng range).
- **Tunay na break lang** (`min_ext_atr` 1.0): ang bagong high/low ay dapat lumampas sa na-break na level ng
  ≥ 1 × ATR bago bilangin ang retest. Sa range, ilang cents lang ang "break" kaya walang entry. (AGGRESSIVE 0.7)
- **HINDI RANGE** (`not_range`, required): ADX ≥ 20 (AGGRESSIVE 18). Kapag tahimik, CONSO ang hahawak.
- **HTF kasabay** (`bos_h4`, required): ang huling H4 BOS (SCALP: H1) ay nasa direksyon ng trade. Naka-OFF sa
  AGGRESSIVE.
- Isang beses idinagdag sa kasalukuyang settings (schema 24). Puwedeng i-OFF bawat isa sa panel.
- Panel: bagong ilaw "hindi range (ADX ≥ 20)" at "H4 BOS". Mas kaunti ang TREND trade, pero iiwas sa chop.

## v2.8.46 (build 2026-10-08.46)
- **Bagong TREND = BOS RETEST** (Eugene: "ito na lang gawin mo na trend natin, alisin mo ang lumang trend"). Galing sa
  "Valid BOS Pullback" (Pine), isinalin sa Python, closed bars lang (walang repaint):
  1. **BOS**: nag-close lampas sa pinakamataas na swing high / pinakamababang swing low (huling 48 kandila)
  2. **Bagong high/low**: confirmed kapag 3 kandila nang hindi nalalampasan
  3. **Malinis**: 5 kandila na hindi tumatama ang wick sa na-break na level (ang maagang tama = balik sa 0)
  4. **Retest = ENTRY**: tumama ang kandila sa na-break na level → MARKET entry kasabay ng bagong trend
  5. ✓ = nag-close lampas sa bagong high/low (tuloy ang trend; pang-chart lang)
  Patay ang setup kapag nag-close pabalik lampas sa level ng > 0.1 × ATR (sa Pine: 4 ticks, masyadong sikip sa gold),
  o lumampas sa 120 kandila. Ang BOS sa kabilang direksyon ay kinakansela ang lahat ng lumang setup.
- **SL** lampas sa dulo ng pullback (at sa level) + 0.2 × ATR, min 0.5 × ATR, max $20 (SCALP $15). TP 1:5 (AGGRESSIVE /
  SCALP 1:3), BE sa +0.5R at trailing gaya dati.
- **Inalis** ang lumang TREND conditions (2 M15 BOS + H4 BOS + RSI kontra + M5 structure + CVD + OB kontra).
  Optional (OFF): HTF BOS kasabay, MMI/CMI. **CMI router OFF sa TREND**: bumababa ang CMI habang nagre-retest, kaya
  halos lahat ng retest ay maibibigay sa PULLBACK kung naka-ON.
- AGGRESSIVE: 3 malinis na kandila lang. SCALP: sa M5 (48 bars = 4 oras, gaya ng Pine default).
- Chart: TR ▲/▼ sa retest, ✓ sa tuloy na BOS, BOS / EXT na linya ng setup na binabantayan. Panel: BOS → bagong
  high/low → malinis x/5 → retest, at kung anong presyo ang hinihintay.
- Isang beses na pinalitan ang TREND conditions sa kasalukuyang settings (schema 23); RR, max SL at AUTO ay naiwan.
- **Hindi pa na-test sa totoong history.** Ipadala ang M15 / H1 / H4 CSV para masukat bago pagkatiwalaan sa live.

## v2.8.45 (build 2026-10-05.45)
Audit fixes (Eugene: order flow = info, CMI router, auto-start stays ON, 0.5R ladder stays).
- **CMI ROUTER: isang strategy bawat kondisyon.** CMI ≥ 55 → TREND lang; CMI ≤ 35 → CONSO lang; nasa gitna →
  PULLBACK lang (`route`, `route_hi`, `route_lo` bawat strategy). Pareho sa live, chart markers, panel at forecast.
  Ang TL BREAK at NEWS ay hindi sakop. Kapag hinarangan: "CMI router: CMI 62 → TREND ang may hawak".
- **Order flow = info lang** (hindi na required): walang tick data sa history, kaya pareho na ang live at forecast
  test. Isang beses inalis sa required (schema 22); makikita pa rin sa panel.
- **Forecast:** bawat strategy forecast may `values` snapshot (MMI, CMI, ADX, RSI, MFI, ATR, EMA, BOS H4/M15/M5,
  CVD change at pinagmulan, router zone, met). Bagong stats: max win / loss streak, average minuto bago ma-trigger,
  average minuto papuntang TP at papuntang SL.

## v2.8.44 (build 2026-10-05.44)
- **BLOCKER FIX (audit 2026-10-08): wala nang settings file sa update zip.** Mula v2.8.30, may kasamang
  `strategy_settings.json` (galing sa tests: schema 16, RR 1:2, HTF filter ON) ang bawat zip, kaya pinapalitan nito
  ang strategy settings mo tuwing mag-e-extract.
  - `tests/harness.py`: LAHAT ng runtime file ng bawat module ay papunta na sa temp folder habang nagte-test.
  - Bagong `make_release.py`: gumagawa ng zip na walang `*.json` / `*.jsonl` / cache, at **humihinto ang build**
    kapag may runtime file na makakasama. Tests: `tests/test_release.py`.
- Pagkatapos i-install: pindutin ang **NORMAL** isang beses para maibalik ang tamang setup.

## v2.8.43 (build 2026-10-05.43)
- **MMI + CMI sa lahat ng strategy** (Eugene). MMI = Market Meanness Index (100 bars, ~75 random, bumababa =
  trend); CMI = Choppy Market Index (14 bars, mataas = trend, mababa = chop). Bagong required na kondisyon `mmi_cmi`:
  - TREND: CMI ≥ 45 O bumababa ang MMI (AGGRESSIVE CMI ≥ 35)
  - PULLBACK: MMI ≥ 66 O CMI ≤ 70 (hindi runaway trend) (AGGRESSIVE MMI ≥ 60 O CMI ≤ 75)
  - CONSO: CMI ≤ 40 AT hindi bumababa ang MMI (AGGRESSIVE CMI ≤ 45)
  Hindi humaharang habang kulang pa ang bars. Isang beses idinagdag sa kasalukuyang settings (schema 21).

## v2.8.42 (build 2026-10-05.42)
- **CONSO grid bawat mode** (Eugene): NORMAL = 1 BUY STOP + 1 SELL STOP; AGGRESSIVE = 2 BUY STOP + 2 SELL STOP;
  SCALP = 1 + 1. Inilipat isang beses sa NORMAL (schema 20, kung 2 pa ang naka-set).

## v2.8.41 (build 2026-10-05.41)
- **CONSO max SL = kapareho ng TREND / PULLBACK** (Eugene: "isabay sa SL nila ang conso"): NORMAL at AGGRESSIVE
  200 pips ($20), SCALP 150 pips ($15). Isang beses inilipat sa kasalukuyang settings (schema 19), kaya gumagana na
  agad kahit hindi pinipindot ang mode.

## v2.8.40 (build 2026-10-05.40)
- **SCALP CONSO max SL = 150 pips ($15)** din (Eugene: "sunod mo na rin sa conso"; dati $4).

## v2.8.39 (build 2026-10-05.39)
- **SCALP max SL = 150 pips ($15)** (Eugene) para sa TREND, PULLBACK at TL BREAK. Ang CONSO ng SCALP ay $4 pa rin.

## v2.8.38 (build 2026-10-05.38)
- **Inayos ang 3 mode** (Eugene): lahat ay sa SC logic (PULLBACK RSI + OB o spike, BUY at SELL; CONSO laging pares;
  BE +0.5R + trailing bawat 0.5R; reversal kapag may entry; bawat strategy sariling trade).
  - **NORMAL:** M15, TREND 2 BOS M15 + H4 + M5 + CVD, SL swing max $20, TP 1:5, lot x1 (x2 kapag naka-align).
  - **AGGRESSIVE:** M15, mas maluwag (1 BOS M15, RSI 35/65, spike 2.5 ATR, ADX<22, max 4 open), SL max $20,
    TP 1:3, **x2 lot** sa TREND / PULLBACK / TL BREAK (hindi dodoble ulit kapag naka-align).
  - **SCALP:** M5 (2 BOS M5 + H1), SL max $8 (CONSO $4, 1 level, TL BREAK $8), TP 1:3, lot x1.
  - Bagong AUTO setting `mode_mult`, `tl_max_sl`, `tl_rr` (nakatakda ng mode).

## v2.8.37 (build 2026-10-05.37)
- **Modes: SCALP / NORMAL / AGGRESSIVE lang** (Eugene). Wala na ang MASTER button (galing iyon sa RG Master EA na
  tinanggal na). **NORMAL = ang SC setup** na binuo sa MASTER: TREND (H4 + 2 M15 BOS + M5 + CVD, swing SL max $20),
  PULLBACK RSI 30/70 + (OB o spike) BUY/SELL kahit kontra HTF, CONSO ADX<20 pares, TP 1:5, BE +0.5R + trailing,
  reversal kapag may entry. Ang naka-save na "master" ay nagiging "normal" (pareho ang settings).

## v2.8.36 (build 2026-10-05.36)
- **Walang 10-min exit sa news trades** (Eugene: "alisin mo na ung 10 mins, naka-trail naman"): `time_exit_min` = 0
  (isang beses inilipat, news settings v3). Ang NEWS CONSO / NEWS TREND ay sarado sa trailing (0.5R ladder), sa TP
  1:5, o sa SL. Maibabalik sa ⚙ ng News Trade.

## v2.8.35 (build 2026-10-05.35)
- **NEWS CONSO at NEWS TREND: TP 1:5** (Eugene: "news conso at news trend 1:5 din ... basta naka-trail pa rin").
  `tp_rr` = 5, isang beses inilipat (news settings v2). Naka-trail pa rin (0.5R ladder, BE sa +0.5R) at sarado pa
  rin pagkalipas ng `time_exit_min` (10 min) kung hindi pa tapos.

## v2.8.34 (build 2026-10-05.34)
- **TP 1:5** (Eugene: "pwede tayo mag 1:5RR, may trailing naman tayo, tapos may pullback na mag-autoclose"):
  TREND, PULLBACK, CONSO at TL BREAK. Ang labas ay sa 0.5R trailing ladder (BE +0.5R, bawat +0.5R isang hakbang sa
  likod) at sa reversal; ang TP ay para sa malalaking galaw. Isang beses inilipat (settings schema 17) at nasa
  MASTER profile na rin (kasama ang 0.5R ladder at CONSO protection, para hindi bumalik sa luma kapag na-click ang
  MASTER). TL BREAK: `tl_rr` (default 5).

## v2.8.33 (build 2026-10-05.33)
- **Break-even sa +0.5R, tapos hagdan bawat 0.5R** (Eugene: "0.5R = BE, 1R : 0.5R and so on"): +0.5R → BE
  (+$0.20), +1R → lock 0.5R, +1.5R → lock 1R, +2R → TP (1:2). Para sa lahat ng trade (SuperChart, manual, EA).
  Isang beses inilipat (position settings v4). Ang TP/2 scaling ng v2.8.32 ay naka-OFF na (ang 0.5R BE ay sakto na
  rin sa 1:1 trades: kalahati ng daan papunta sa TP).

## v2.8.32 (build 2026-10-05.32)
- **Protection para sa 1:1 trades** (Eugene: "1:1 lng kc ung trade"): ang BE / lock / trailing ay para sa 1:2 (1R =
  kalahati ng daan papunta sa TP). Ang trade na mas malapit ang TP kaysa 2R (hal. 1:1) ay gumagamit ng TP/2 bilang
  1R, kaya nagsisimula ang protection sa kalahati ng daan papunta sa TP. Walang pagbabago sa 1:2 at mas malawak.
  Setting `tp_scale` (default ON). Hal. SELL 4117.75, TP 4105.03: BE sa 4111.39, at sa 4108 ang SL ay +$6.36.

## v2.8.31 (build 2026-10-05.31)
- **Trailing / profit protection pati sa MANUAL at EA trades** (Eugene: "kahit anong trade, manual, EA man o
  SuperChart"): ang trade na binuksan mo sa MT5 (PC / phone) o ng RG M EA sa master ay binibigyan na rin ng
  break-even, lock at trailing; sumusunod ang followers. Ang 1R ay ang UNANG SL ng trade (kung idinagdag mo ang SL
  pagkatapos, iyon ang 1R). Kailangang may SL ang trade. Setting `manage_manual` (default ON).

## v2.8.30 (build 2026-10-05.30)
- **PROP FIRM follower** (Accounts → follower → "PROP FIRM account"). Ang master at ibang followers ay tuloy; ang
  PROP account lang ang sumusunod sa rules:
  - **News:** walang NEWS trades (news conso / news TREND), walang bagong trade ±N min (default 5) sa high-impact
    news, at isinasara ang open trades niya bago ang window (`prop_close_news`).
  - **Weekend:** walang open trade sa weekend: isinasara sa Biyernes cut-off (default 20:00 UTC = Sab 4:00 AM PH),
    walang bagong trade hanggang Linggo 23:00 UTC.
  - **Walang x2** (fixed lot kahit naka-align).
  - Binubura rin ang pending CONSO orders ng PROP account sa mga oras na iyon. Hindi ito binibilang na COPY CHECK.

## v2.8.29 (build 2026-10-05.29)
- **SuperChart na ang nagla-login ng follower MT5** (Eugene): sa Accounts, bawat follower may Login #, Server at
  Password. Pagbukas / Reconnect, ini-initialize ang MT5 terminal ng follower gamit ang mga iyon, kaya hindi na
  kailangang mag-login nang manual sa MT5. Kailangan pa rin ng sariling MT5 terminal (folder) bawat account.
- Ang password ay naka-save LANG sa accounts.json sa VPS; hindi ito ipinapadala pabalik sa browser (makikita lang
  "● naka-save"); blangkong field = hindi babaguhin. Kapag mali: "(mali ba ang login / password / server?)".

## v2.8.28 (build 2026-10-05.28)
- **TRAILING ON** (Eugene: CONSO BUY 4170 → 4180+, bumalik sa SL): step trailing bawat 0.5R mula +1R (sa +1R ang SL
  ay +0.5R, sa +1.5R → +1R, sa +2R ang TP). Tighten-only; hindi ginagalaw ang TP. Isang beses inilipat
  (position settings v3); maibabalik sa 🛡 chip.
- **CONSO kasama na sa profit protection** (break-even / lock / trailing): `conso_protect` ON, isang beses
  (auto settings v5). Dati: ang CONSO ay naiiwan sa sariling SL / TP gaya ng Master EA.
- Hal. CONSO BUY 4170, SL $8: sa 4178 (+1R) SL → 4174; sa 4182 (+1.5R) SL → 4178 (+$8) sa halip na -$8.

## v2.8.27 (build 2026-10-05.27)
- **Bagong AUTO strategy: TL BREAK** (Eugene, trendline breakout gaya ng "simplified entry"):
  - **TL break:** papasok sa CLOSE ng kandilang bumasag sa trend line (Trend Line Breakout plugin, ≥2 touches).
  - **TL retest:** RE-ENTRY kapag na-retest ang nabasag na line at humawak (hiwalay na trade).
  - SL sa likod ng swing, max $20 (200 pips); TP 1:2. Puwedeng kontra sa HTF (madalas ganoon ang TL break).
  - Kasama sa: x2 kapag naka-align (H4·H1·M15), reversal kapag may entry (isinasara ang mga kontrang trade, at
    isinasara rin ang TL trades ng ibang reversal), copy sa followers, daily DD / lock. Walang % risk block.
  - Button na **● TL BREAK** sa itaas ng chart (default ON; may confirm kapag ginawang MANUAL). Setting `tl_auto`.
- FIX: ang master daily DD migration (v2.8.25/26) ay pumapalit lang sa preset ($20 / 200 pips), hindi sa sariling
  limit mo.

## v2.8.26 (build 2026-10-05.26)
- **Master daily DD = 400 pips** (Eugene: "400 ang dd limit ntin ndi 200"). Pinapalitan din ang 200 pips na inilagay
  ng v2.8.25 (isang beses; ang sariling value mo ay hindi ginagalaw). 0.01 lot = -$40.

## v2.8.25 (build 2026-10-05.25)
- **Master daily DD = 200 pips** (Eugene): inilipat isang beses mula $20 (master lang; ang daily TP ay hindi
  ginalaw). Sa 0.01 lot = -$20; papalitan sa Accounts kung gusto.

## v2.8.24 (build 2026-10-05.24)
- **Daily DD / TP sa PIPS** (Eugene): bagong pagpipilian na "pips" sa Accounts (bukod sa % at $). 1 gold pip =
  $0.10 na galaw; binibilang sa base lot ng bawat account (follower: sariling lot; master: AUTO lot). Hal. DD
  400 pips: master 0.01 → -$40, follower 0.02 → -$80. Ang x2 (naka-align) na trade ay doble ang bawas sa DD
  dahil doble ang lot. Ipinapakita sa Accounts: "DD at -$40 (400 pips)".

## v2.8.23 (build 2026-10-05.23)
- **SL max $20 (200 gold pips) para sa TREND at PULLBACK** (Eugene: "max tayo sa SL na $20 or 200 pips"). Wala nang
  % risk block (RISK_PER_TRADE) sa dalawa: ang SL ay ang sariling SL ng strategy (TREND: last swing), pero hindi
  lalampas sa $20 na layo; ang TP ay sumusunod (1:2 → $40). `max_sl` = 20, `max_risk_pct` = 0 (off), isang beses
  inilipat (settings schema 16). Pareho ang layo kahit x2 ang lot (naka-align): 0.01 lot = $20, 0.02 lot = $40 kapag
  tinamaan ang SL. Event log: "TREND SL: $28.60 → $20.00 (max $20)".

## v2.8.22 (build 2026-10-05.22)
- **x2 lot kapag naka-align ang H4 + H1 + M15** (Eugene): TREND / PULLBACK, master AT followers (hal. master 0.01 →
  0.02, follower 0.02 → 0.04) kapag pareho ang direksyon ng trade at ng H4, H1 at M15 structure. Kung hindi
  (hal. H1 / M15 lang), base lot. `align_mult` (default 2; 1 = off). Event log: "ALIGN H4·H1·M15 x2". Ang
  reconciliation ay umaasa sa doble na volume (walang maling "volume drift" repair).
- **FIX: TREND 5% risk sa pagpapadala ng order.** Ang gate ay pumapayag hanggang 5%, pero ang huling check bago
  ipadala ang order ay gumagamit pa rin ng 2% ng risk settings, kaya puwedeng "FAILED: RISK_PER_TRADE". Iisang
  limit na (5%; 10% kapag x2).

## v2.8.21 (build 2026-10-05.21)
- **PULLBACK: Order block O biglang spike** (Eugene: "khit walang OB, pero kung may OB mas ok"). Bagong kondisyon
  `ob_spike`: pasado kapag may order block (gaya dati) O may biglang galaw na ≥ 3 × ATR (`spike_atr`) sa huling
  6 bars papunta sa extreme (bagsak bago ang BUY, akyat bago ang SELL). Kasama pa rin ang RSI 30/70. Pinalitan ang
  "ob" ng "ob_spike" isang beses (settings schema 15) at sa MASTER profile. Kapag may OB, doon pa rin ang SL.

## v2.8.20 (build 2026-10-05.20)
- **REVERSAL kapag may ENTRY lang** (Eugene: "pag may entry lng sna ang pag close"): ang pagsara ng open trades na
  kontra ay nangyayari lang kapag talagang PUMASOK ang bagong TREND / PULLBACK trade. Kung hinarangan ang signal,
  walang isinasara. Kung puno ang max open dahil sa mga kontrang trade: isasara muna ang mga iyon, saka papasok.
- **CONSO: bagong box ang papalit sa lumang grid** (Eugene: "mapapalitan ang lumang conso box"): kapag may bagong
  CONSO box habang naka-pending pa ang luma (at walang bukas na CONSO trade), buburahin ang lumang pares at ilalagay
  ang bago. Hindi na babalik ang lumang box. Kung walang bagong box, hinahayaan ang pending (pares, OCO).

## v2.8.19 (build 2026-10-05.19)
- **Hindi na binubura ng reversal ang isang side ng CONSO grid** (Eugene: na-cancel ang BUY STOP kahit walang na-hit).
  Dati (v2.8.0): bagong TREND / PULLBACK SELL → binubura ang CONSO BUY STOP (at baligtad), kaya SELL STOP lang ang
  naiiwan. Ngayon laging pares ang grid hanggang ma-hit (OCO). Ang lumang galaw: `rev_cancel_conso` (default OFF).
  Ang open trades na kontra ay isinasara pa rin ng reversal.

## v2.8.18 (build 2026-10-05.18)
- **PULLBACK sa dalawang direksyon, kahit kontra sa HTF** (Eugene: para sa biglang spike, sell at buy man):
  RSI oversold → BUY, overbought → SELL, kahit Bearish / Bullish ang HTF (`pullback.any_dir`, default ON). Hindi na
  hinaharang ng HTF bias / structure conflict sa gate. Ang mga kondisyon (MASTER: RSI 30/70 + order block) ay
  kailangan pa rin.
- **PULLBACK: walang VOLATILE block** (`block_volatile` OFF): mataas talaga ang ATR pagkatapos ng spike.
  Isang beses inilipat (settings schema 14); maibabalik sa ⚙ ng PULLBACK.

## v2.8.17 (build 2026-10-05.17)
- **TREND SL hanggang 5% ng equity** (Eugene: "gawin mo nlng n lging 5% ang sl"). Ang SL ay nasa last swing pa rin,
  pero kapag lampas ito sa 5% ng master equity (sa AUTO lot), ilalapit ito sa 5% at susunod ang TP (parehong 1:2).
  Hal. equity $153, 0.01 lot: max SL ≈ $7.65, TP ≈ $15.30. Event log: "TREND SL: swing $… → $…".

## v2.8.16 (build 2026-10-05.16)
- **TREND: sariling risk limit 5% bawat trade** (Eugene; dati 2% ng risk settings). `trend.max_risk_pct` = 5.
  Paalala: sa master na ~$153 at swing SL ~$28 (0.01 lot) = ~18.7% → haharangin pa rin; papasok kapag mas maliit
  ang SL (≤ ~$7.6) o mas malaki ang master equity.

## v2.8.15 (build 2026-10-05.15)
- **PULLBACK reversal** (Eugene: "pag biglang may pullback, auto close ang naka-open, trend o conso man"): ang bagong
  PULLBACK signal ay nagsasara na rin ng open TREND / PULLBACK / CONSO trades na KONTRA sa direksyon nito, at
  binubura ang CONSO STOP sa side na iyon (dati TREND lang ang gumagawa nito). `pullback_reversal` (default ON).

## v2.8.14 (build 2026-10-05.14)
- **CONSO: walang risk-per-trade limit** (Eugene; hinarangan ulit: 5.66% > 5%). `conso.max_risk_pct` = 0 (off),
  inilipat isang beses (settings schema 13). Para sa ordinary CONSO at NEWS CONSO. Ang TREND / PULLBACK ay nasa
  risk settings pa rin. Ang naiwang safety: ARM / STOP, daily DD / TP lock, spread, news window, pares o wala.

## v2.8.13 (build 2026-10-05.13)
- **Walang kalahating CONSO grid** (Eugene: 2 BUY STOP, walang SELL STOP). Nangyari: lumabas na ang presyo sa ilalim
  ng box bago nailagay ang grid, kaya ni-reject ng broker ang SELL STOP (dapat nasa ilalim ng presyo) at BUY STOP
  lang ang naiwan, walang OCO partner. Ngayon:
  - bago maglagay: kung nabasag na ang box (bid ≤ SELL STOP o ask ≥ BUY STOP) → hindi maglalagay, may dahilan sa
    ⛔ line ("nabasag na pababa / pataas ang box");
  - pagkalagay: kung isang side lang ang tinanggap sa master → buburahin lahat (walang kalahating grid).

## v2.8.12 (build 2026-10-05.12)
- **CONSO: sariling risk 5% bawat trade** (Eugene: "5% risk pra sa conso"; dati hinarangan ng RISK_PER_TRADE 4.86% >
  2%). Ordinary CONSO at NEWS CONSO: max 5% ng equity bawat order (`conso.max_risk_pct`, default 5). Ang TREND /
  PULLBACK ay sumusunod pa rin sa risk settings (2%).

## v2.8.11 (build 2026-10-05.11)
- **CONSO hindi na pinipigilan ng session** (Eugene: "basta may conso wag nlng pipigilan"): ang CONSO grid ay
  ilalagay kahit labas sa Asia / Frankfurt / London / New York. Ang TREND / PULLBACK ay sumusunod pa rin sa sessions.
  Ang safety na naiwan para sa CONSO: ARM / STOP, daily DD / TP lock, spread, news window, isang grid lang sabay.

## v2.8.10 (build 2026-10-05.10)
- **Kahit anong entry sa master, papasok din sa followers** (Eugene: 2 entry sa master, walang follower):
  - CONSO grid / NEWS grid: dati, umaasa ang follower sa SARILI nitong pending order (iba ang presyo ng broker niya,
    kaya puwedeng hindi ma-hit, o hindi na-place). Ngayon: pag na-fill ang master, ang follower na hindi pa na-fill
    ay papasok agad sa MARKET (lot ng follower, parehong SL / TP $), at buburahin ang pending niya (walang doble).
  - **COPY CHECK:** bawat master trade na 20 s na pero kulang ng follower: pulang mensahe sa event log + alert,
    kasama ang dahilan (PAUSED, LOCKED, Algo Trading OFF, hindi konektado, order failed…). Isang beses bawat trade.
- Tests: 2 bago.

## v2.8.9 (build 2026-10-05.9)
- **NEWS line sa itaas ng chart:** susunod na high-impact news, countdown, forecast / previous. Asul = malayo pa,
  dilaw = < 1 oras, pula + kumikislap = NEWS WINDOW (naka-pause ang ordinary strategies). I-click → NEWS page.
- **News results (NEWS page):** bawat high-impact news habang bukas ang app: forecast vs actual (TUGMA ±5% /
  MAS MATAAS / MAS MABABA), galaw ng gold +5 / +10 / +30 min, range ng unang 10 min, at resulta ng NEWS trades
  (CONSO / TREND, WIN / LOSS, P/L). Summary: forecast accuracy %, average na galaw, win rate. `news_results.py`,
  saved sa news_results.json.

## v2.8.8 (build 2026-10-05.8)
- **Para sa VPS (walang magki-click):** NEWS → TREND naka-ON na by default (`follow_trend`).
- **Hiwalay ang NEWS sa ordinary strategies:**
  - 3 min bago ang news: isasara ang open ordinary trades (dati na) AT buburahin ang ordinary CONSO pending grid
    (bago; wala na itong expiry kaya puwedeng ma-hit ng news spike).
  - 30 min bago hanggang 10 min pagkatapos ng news: naka-pause ang ordinary TREND / PULLBACK / CONSO (news window ng
    AUTO TRADE); kusang babalik pagkatapos, at maglalagay ulit ng CONSO grid kung READY pa ang box.
  - News CONSO + News TREND: hiwalay na trades (source news / news_trend), sarado pagkalipas ng 10 min.

## v2.8.7 (build 2026-10-05.7)
- **CONSO walang expiration** (Eugene: "walang expiration ang conso"): ang BUY STOP / SELL STOP grid ay nananatili
  hanggang ma-hit (OCO: buburahin ang kabila) o ma-cancel (reversal, STOP NEW TRADES, DISARM). Bagong setting
  `conso.no_expiry` (default ON).
- **NEWS: isara pagkalipas ng 10 min** (`time_exit_min`, default 10, 0 = off): ang na-trigger na news trade ay
  isasara 10 minuto pagkabukas (master + followers).
- **NEWS → TREND** (`follow_trend`, default OFF, nasa News Trade ⚙): kapag na-hit ang news stop sa master, may
  kasunod na market trade sa parehong direksyon (parehong SL / TP $, master lot + lot ng followers), isang beses
  bawat news. Isasara rin pagkalipas ng `time_exit_min`.
- Tests: 3 bagong news / conso tests.

## v2.8.6 (build 2026-10-05.6)
- **CONSO: bagong pending grid habang READY pa ang box** (Eugene: "ok lng mag open prin ng pending order ang conso,
  basta pag na hit ang isa close ang opposite"). Dati: isang grid lang bawat READY (kaya "signal … already
  processed"). Ngayon: kapag tapos na ang dating grid (expired / cancelled / na-hit at sarado na) at READY pa rin,
  maglalagay ulit ng BUY STOP + SELL STOP. OCO pa rin: kapag na-hit ang isa, buburahin ang kabila (bawat account).
  Isang grid lang sabay; hindi habang may bukas pang CONSO trade. Habang naghihintay ang grid: ▶ berde sa strip.

## v2.8.5 (build 2026-10-05.5)
- **Bakit READY pero walang order:** ang dahilan (na nasa nakatagong STRATEGIES panel lang dati) ay nasa ilalim na ng
  AUTO buttons sa itaas ng chart: ⛔ PULA = READY pero hindi papasok (MANUAL, AUTO TRADE OFF, hindi ARM, o ang gate
  na humarang: session / risk / news / may pending CONSO grid…), ▶ BERDE = papasok / pumasok na.
- **Pag-MANUAL ng strategy ay may confirm na:** dati, isang click lang sa umiilaw na button ay nag-MANUAL na
  (ganito naging ○ TREND).
- Tooltip ng MASTER: ano ang laman ng profile.

## v2.8.4 (build 2026-10-05.4)
- **Mas malaking chart:** tinago ang "NAKA-ON … score · … chart" indicator chips sa ilalim ng chart (isang beses;
  maibabalik sa ⚙ LAYOUT → "Naka-ON na indicators"). Mas mataas na ang chart area (halos buong screen).

## v2.8.3 (build 2026-10-05.3)
- **STRATEGIES panel puwedeng itago** (default nakatago): mas malaki ang chart. ◧ PANEL sa itaas ng chart para
  ipakita ulit. Ang AUTO buttons (TREND / PULLBACK / CONSO) sa itaas ng chart ay umiilaw na:
  BERDE + kumikislap = READY, DILAW = MALAPIT, may ▲ / ▼ at % (tooltip = detalye).
- **Balance card:** malaki na ang BALANCE (may account #), sa ilalim ang Equity at open P/L.
- **CONSO laging AUTO:** ina-AUTO isang beses (settings schema 12); puwede pa ring gawing MANUAL sa ○ CONSO.

## v2.8.2 (build 2026-10-05.2)
- **Isang chart na lang:** tinanggal ang ⧉ 2 SCREEN at ↗ (pop-out) sa chart toolbar.
- **Icon-only na menu:** ang sidebar at chart toolbar ay icon na lang (pangalan = tooltip kapag itinapat ang mouse).
  ☰ sa itaas ng sidebar para ipakita / itago ulit ang mga pangalan (naaalala).
- **Copier fix (RG Master EA CONSO / TREND hindi nakopya sa followers):**
  - Kapag LOCKED ang master (daily DD / TP, hal. ang $20 / $60 preset ng v2.8.0), tuloy pa rin ang EA sa master
    pero dati HINDI na kinokopya sa followers. Ngayon kinokopya pa rin (bagong checkbox sa Accounts,
    "I-COPY pa rin kahit LOCKED ang master", default ON). Ang sariling daily lock ng follower ay masusunod pa rin.
  - Bawat trade sa master na HINDI kinopya ay may paliwanag na sa event log (ibang symbol, magic wala sa listahan,
    naka-off ang copy), isang beses bawat ticket.
  - Trades na bukas na bago nag-start ang app: may warning na sa event log (dati tahimik lang).
- Tests: 3 bagong copier tests.

## v2.8.1 (build 2026-10-05.1)
- **Copy EVERY trade on the master** (Eugene: "copy na lahat, kahit anong trade man - master EA, manual o terminal"):
  trades of ANY EA on the master symbol (e.g. the REYGUN Master EA) are now copied to the followers like manual
  trades: follower lot, same SL / TP $ distance, SL moves, partials and the close mirrored. The EA's trade on the
  master is never modified. Accounts: "I-COPY din ang trades ng EA sa master" (on) + optional magic-number list
  (empty = every EA). Journal kind `ea_mt5` with `source_magic`. Terminal trades are not copied twice.
- **Leftover test order cleanup** (an `RGSC-TEST` pending order stayed in MT5 after a 📡 Test signal): an
  `RGSC-TEST` order still pending after 30 s is deleted and logged. Nothing else is touched.
- Paper: the test BUY LIMIT no longer triggers like a stop order.

## v2.8.1 (build 2026-10-05.1)
- **Leftover test order cleanup** (Eugene: an `RGSC-TEST` pending order was still in MT5 after a 📡 Test signal):
  every account's orders are checked on each refresh; an `RGSC-TEST` order (our magic) still pending after 30 s is
  deleted and logged ("naiwang test order … binura"). Nothing else is touched; not while a test is running.
- Paper: the test BUY LIMIT no longer triggers like a stop order.

## v2.8.0 (build 2026-10-04.2)
- **MASTER** (Eugene: "ilagay mo na ang master sa terminal", "ilagay mo rin ang CVD"): the MT5 Master EA v1.90 (CVD preset)
  in the terminal.
  - New STRATEGIES profile MASTER, applied once when the strategies were NORMAL:
    - TREND: H4 BOS + 2 M15 BOS + not extreme + new **M5 structure** + new **CVD** conditions.
      CVD = sum of the M5 bar deltas (tick volume × (close − open) / (high − low)) over the last 8 strategy bars
      (or the last 2 with cvd_mode turn2). Only M5 bars closed by the strategy bar's close are used.
    - Swing SL, 1:2. PULLBACK RSI 30/70 + order block. CONSO ADX < 20 grid. Every strategy on AUTO.
  - Sanity check on 6 months of M15 (2R, no protection): base +47R; + M5 structure +54R; + CVD +67R.
  - AUTO TRADE MASTER settings (v4 migration, once): M15, max 3 open, unlimited per day, "no opposite" off.
    - Each strategy its own trade (`Group.source`); only its own open trade or live CONSO grid blocks it.
    - Reversal close: a fresh TREND signal closes older TREND / PULLBACK / CONSO trades against it and cancels the
      CONSO stops on that side (once per signal).
    - Early exit (m15_2bos) for TREND; re-entry after a TREND win (H4 window 30); secure 50% at 1R (TREND).
    - CONSO trades are skipped by the position manager (EA: CSProtect off).
  - Master account daily loss $20 / profit target $60 ($), once, only when no limit was set.
- **Partial close safety:** a partial close is skipped on a leg whose lot cannot be split (0.5 × 0.01). Before, the
  broker could round it up to the whole position.
- **Delta Profile (buy vs sell)** plugin (LAYERS → VOLUME, on): split buy / sell profile, delta per row, ΔPOC, and who
  is more aggressive. `Profile` gained `buy`, `sell_color` and `title`.
- Tests: `tests/test_master.py` (16).

## v2.7.9 (build 2026-10-04.1)
- **📡 Test signal** (Accounts page; Eugene: "test signal ... para malaman kung may connectivity nga ang dalawa"):
  - A REAL order to the master and every follower, through the same order_send route as every signal:
    BUY LIMIT, minimum lot, 20% below the price, so it can never fill. Comment `RGSC-TEST`.
  - It stays 5 s so you can see it in each MT5 Trade tab, then it is deleted and checked gone.
  - Shows per account: sent / visible in MT5 / deleted, plus the send time in ms.
  - Market closed (weekend): reported as "SARADO ANG MARKET" (retcode 10018) - the broker was reached.
  - No ARM needed. Refused while HALTED; it never clears a halt. The 5 s wait is outside the MT5 lock (the chart keeps
    updating). Only the test ticket is touched.

## v2.7.8 (build 2026-10-03.4)
- **Refined strategies** (Eugene: "yung iba bagsak na, kailangan i-refine"). Settings were picked on Mar-Jun 2026
  and checked on Jul-Sep 2026 (months not used to pick them). Exits are walked on M5 bars, with $0.25 spread and the
  NORMAL protection.
  - **TREND:**
    - The H4 BOS must be fresh: within the last 12 H4 bars (was 30).
    - New KONTRA, required: no BUY when overbought (RSI ≥ 70), no SELL when oversold (RSI ≤ 30). This stops the
      late entries.
    - SL at the last swing, TP 1:2. Mar-Jun +25R, Jul-Sep +28R, max DD 10.6R. Before: +24R over all 6 months.
  - **PULLBACK:**
    - RSI dip + an unbroken M15 order block + order flow.
    - MFI and doji are OFF: with them, it never once fired in 6 months.
    - TP 1:2 (was 1.5). Mar-Jun +9R, Jul-Sep +8R, about 2 trades a week.
  - **CONSO:** unchanged. It passed Eugene's MT5 Strategy Tester.
  - Older NORMAL settings get this once. AGGRESSIVE / SCALP are untouched.
- **MT5 EAs v1.20:**
  - TREND and PULLBACK use the same rules, plus the NORMAL BE / lock.
  - RANGE: M15 only, SL 1.5 ATR, TP at the middle of the range. Mar-Jun +9R, Jul-Sep +10R, 60% win.

## v2.7.7 (build 2026-10-03.3)
- **TREND SL at the last swing** (Eugene):
  - The SL goes beyond the latest confirmed swing low (BUY) / high (SELL), plus 0.1 ATR.
  - It stays between 0.5 and 4 ATR. If no swing is found, it falls back to 1.5 ATR. TP stays at 1:2.
  - 6-month test (M15, NORMAL): 36% win, +16R with no protection, +26R with the NORMAL protection (BE at 1R,
    lock 1.5R→0.5R, 1.8R→1R), max drawdown 10R. The old setup (1.5 ATR) made +22R.
- Older settings switch once (NORMAL profile only; AGGRESSIVE / SCALP keep the ATR SL).
- ⚙ TREND editor: SL mode (swing / atr), swing buffer, swing max.
- MT5 EA TREND v1.10: same swing SL, plus the NORMAL profit protection.

## v2.7.6 (build 2026-10-03.2)
- **CONSO is MANUAL** (Eugene: "oo"). In a 6-month test of real gold prices the OCO breakout lost in every session
  (15-28% win at 1:2, about -130R). It still shows on the chart and the card; switch it back to ● AUTO any time.
- **AUTO TRADE sessions:** Asia and Frankfurt are added to London + New York (once; your later choice stays). In
  the same test TREND did best in Asia (+11R).

## v2.7.5 (build 2026-10-03.1)
- **Frankfurt session** (Eugene): 06:00-14:00 UTC = 2:00-10:00 PM Manila. It has its own box on the chart and its
  own chip in the header. The hour before London (2-3 PM Manila) now reads "Frankfurt session" (code FRANKFURT).
  You can tick it in AUTO TRADE → Sessions.
- **Strategy trades are no longer blocked by the analyser's required list:**
  - In a replay of real gold prices, TREND (100% green) was blocked with "required: Displacement". That is the
    analyser's BREAKOUT regime rule, not a TREND condition.
  - Strategy signals now skip the analyser's required list (score, trigger, regime extras).
  - Still checked: the HTF bias (NORMAL), session, news, risk, execution, max open and the structure conflict.

## v2.7.4 (build 2026-10-02.6)
- **Manual copy without touching the terminal** (Eugene: "khit ndi ako mag pindot s terminal ... un nrin gawin s
  followers"): a manual trade on the master is copied even when trading is NOT armed. As long as the app is open
  and connected, whatever you do on the master (open, SL / TP move, partial, close) the followers do too.
  - Still not copied while the master is LOCKED (daily DD / TP lock); the event log says so.
  - A pending order you place on the master is copied when it fills (it becomes a position).

## v2.7.3 (build 2026-10-02.5)
- **Manual trades are copied to the followers** (Eugene: "isama mo na rin ang manual na trade sa pag-copy"):
  - A BUY / SELL you open by hand on the master MT5 (PC or phone; magic 0, master symbol) is copied to every follower
    with the follower's fixed lot and the same SL / TP $ distance.
  - From then on it is a normal copied trade: SL / TP moves, partial closes and the close on the master are mirrored.
  - Not copied: other EAs' trades (magic ≠ 0), other symbols, trades already open when the app started.
  - The position manager (BE / profit lock / trail) leaves the SL of your manual trade alone.
  - ON by default; "I-COPY din ang MANUAL trades" checkbox on the Accounts page.

## v2.7.2 (build 2026-10-02.4)
- **Everything ON at start** (Eugene: "i-ON mo lahat pati ang news"):
  - Trading + AUTO + NEWS TRADE switch on when the app opens.
  - TREND, PULLBACK and CONSOLIDATION are all ● AUTO; older settings get it once. A strategy you set to ○ MANUAL
    later stays MANUAL.
  - "NEWS TRADE ON pagbukas" can be unticked in AUTO settings.
- **Follower copy:** every follower that is left out of a trade now says why in the event log and journal, e.g.
  "FOLLOWER LiteFinance: hindi kinopya (PAUSED / Algo Trading OFF / LOCKED / hindi konektado / walang lot)".
  Pending orders (CONSO / NEWS) do the same.
- **Account chips above the chart:** OFFLINE / PAUSED / LOCKED / ALGO OFF in red. Click a PAUSED or LOCKED chip to
  resume / unlock.

## v2.7.1 (build 2026-10-02.2)
- **Order flow is ON in every strategy** (and in every profile). Older settings get it added; your other
  conditions stay. Each strategy uses it for its own job:
  - TREND: the stronger side must follow the trend (buyers ≥ +15% for BUY, sellers for SELL; last 3 candles).
  - PULLBACK: buyers / sellers must be back at the end of the pullback (≥ 10%, last 2 candles).
  - CONSOLIDATION: buyers and sellers balanced (|delta| < 15%, last 5 candles).
- **Strategy cards (Market Overview)** show a line per strategy: "✓ / ✗ OF buyers +32% · kailangan buyers ≥15%".
- No tick data yet (e.g. Backtest): order flow is not judged, so it does not block.

## v2.7.0 (build 2026-10-02.1)
- **Order flow in the strategies** (＋ idagdag in the ⚙ strategy panel, OFF by default):
  - TREND "ORDER FLOW: mas malakas ang buyers / sellers": delta of the last 3 candles ≥ 15% of volume in the
    trade direction.
  - PULLBACK "ORDER FLOW: pumasok na ang buyers / sellers" at the end of the pullback (last 2 candles).
  - CONSOLIDATION "ORDER FLOW balanse": |delta| < 15%.
- **"Buyers vs Sellers" on the chart** (ON by default): BUYERS ▲ / SELLERS ▼ where the stronger side changes, plus
  an ORDER FLOW bar on the STRATEGIES card.
- **Fix:** the black window filled with "ConnectionAbortedError [WinError 10053]". It was harmless (the page cancelled
  a request) and is no longer printed.

## v2.6.7 (build 2026-10-01.22)
- **AUTO ON pagbukas ng app is now the default** (Eugene: "i-ON mo na as default").
  - Live: trading + AUTO switch on by themselves at every start and after every auto restart. Only the strategies
    marked ● AUTO trade.
  - Untick "AUTO ON pagbukas ng app" (Trading → AUTO TRADE settings, or in the AUTO confirm) to start in MANUAL.
  - A HALT (daily loss / emergency stop) is still never cleared by itself.

## v2.6.6 (build 2026-10-01.21)
- **⚡ AUTO → confirm** now turns on trading + AUTO in one server step (`/api/auto/start`) and always says what
  happened:
  - "Hindi na-ON ang AUTO: …" or "AUTO ON, pero hindi pa makakapag-order: …".
  - "Nag-OFF ulit ang AUTO: …" when it goes off again within a few seconds, with the reason.
- **"Laging AUTO pagbukas ng app"** (checkbox in the AUTO confirm and in AUTO settings, OFF by default): when ON,
  trading + AUTO switch on by themselves every time SuperChart starts. A HALT is never cleared.

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
