# REYGUN TERM + REYGUN SUPERCHART

Dalawang app ang laman ng folder na ito. Pareho ang data (MT5), analyser, at plugins nila:

| App | Itsura | Launcher |
|---|---|---|
| **REYGUN SUPERCHART** | Dashboard window na may TradingView-style chart, cards, at panels | `run_superchart_live.bat` / `run_superchart_demo.bat` |
| **REYGUN TERM** | Blue retro terminal | `run_live.bat` / `run_demo.bat` |

## SUPERCHART
- Bubukas ito bilang sariling window (gamit ang Microsoft Edge app mode). Huwag isara ang itim na console window, dahil 'yon ang server. Kapag isinara mo 'yon, hihinto ang app.
- Chart: mouse wheel para mag-zoom, drag para mag-scroll, M1 hanggang D1 buttons (o keys `1` hanggang `7`), at **LAYERS** para i-on/off ang indicator plugins.
- Mga page sa sidebar: Market Overview · MTF Analyser · Signal Center · Plugins · Accounts · **Journal** · **News** · Tape & Recording
- Kapag hindi kasya ang page sa screen (hal. VPS na 1366x768), gamitin ang mouse wheel o ang **▲ ▼** sa sidebar (double-click = pinakataas/pinakababa).
- Hindi na nagre-record ng ticks by default. Kung kailangan mo pa rin (para sa replay), pindutin ang **REC** sa itaas.
- Local lang ito (127.0.0.1). Walang makaka-access mula sa labas ng PC/VPS mo.
- Kapag "port busy" ang lumabas, bukas pa ang isang SuperChart. Isara muna 'yon o gamitin ang `--port 8766`.

## Manual trading + multi-account (SuperChart)

**Walang automatic na trade.** (Naka-patay muna ang News Trade auto; box at signals na lang ang ipinapakita.) Magpapadala lang ng order kapag:
1. Na-**ARM** mo ang trading (switch sa TRADE card). Laging **naka-OFF** ito tuwing bubuksan ang app.
2. Nag-click ka ng **BUY** o **SELL**.
3. Pinindot mo ang **Confirm** sa dialog na naglilista ng bawat account at lot.

Alert lang pa rin ang strategies.

| Launcher | Data | Orders |
|---|---|---|
| `run_superchart_paper.bat` | live MT5 | **simulated (paper)**, dito ka muna mag-practice |
| `run_superchart_live.bat` | live MT5 | **totoong order** (kailangan ng ARM + confirm) |
| `run_superchart_readonly.bat` | live MT5 | walang trade panel |
| `run_superchart_demo.bat` | synthetic | paper |

**Trade ticket:** Lot (master), SL distance $, TP distance $. Pareho ang $ layo ng SL/TP sa bawat account, mula sa sariling fill price nito, kaya pareho ang risk kahit magkaiba nang kaunti ang presyo ng mga broker.

**SuperChart trades card:** Set SL/TP (para sa lahat ng account), Break-even, Close 50%, Close, at Close ALL.

**Sync:** Kapag inilipat mo ang SL/TP, nag-partial close, o nagsara sa **master**, susunod ang lahat ng follower, kahit sa MT5 mo ito ginawa. Ang mga trade na binuksan mula sa SuperChart lang ang sinusundan (magic `20260925`, comment `RGSC-xxxxxx`). Hindi ginagalaw ang trades ng mga EA mo.

**Accounts page:**
- **Master**: ang MT5 na nagpapakain sa chart. Itakda ang **path ng terminal64.exe**, login # (check), at symbol. Kapag nag-Save, lilipat agad ang chart sa MT5 na iyon, pero hindi ito papayagan habang may bukas na SuperChart trade. Kapag mali ang login #, naka-block ang trading hanggang maayos.
- **Follower**: MT5 terminal path, login # (optional na check para siguradong tamang account), symbol (hal. `XAUUSD` sa Switch Markets), **fixed lot**, at copy on/off.
- **Daily DD / Daily TP** bawat account ($ o % ng balance sa simula ng araw). Kapag naabot: isasara ang trades (lahat, o SuperChart lang, depende sa setting) at **naka-lock ang account hanggang sa susunod na araw ng server**. May **Unlock** button kung kailangan.

**Symbol ng bawat broker (prefix/suffix):** iba-iba ang pangalan ng gold, halimbawa `XAUUSD_ph` (LiteFinance), `XAUUSD.r`, `XAUUSDm` (Exness), `#XAUUSD` o `GOLD`.
- **🔍 sa tabi ng Symbol:** hinahanap sa MT5 mismo ng account na iyon ang lahat ng symbol na tugma sa base ng master (hal. XAUUSD, pati ang alias na GOLD). Una ang pinakamagandang tugma. Minamarkahan ang **in use** at **NOT tradable** (close only o disabled). I-click para piliin, tapos **Save accounts**.
- **🔍 Auto-detect symbols:** sabay-sabay na pinupunan ang symbol ng bawat konektadong follower.
- Kapag mali ang symbol, sinasabi rin ng Test connections ang mungkahing symbol, halimbawa "did you mean: XAUUSDm".
- Kailangang naka-Save at konektado ang follower bago ito mahanapan ng symbol.

**🔌 Test connections** (Accounts page): sinusuri ang master at bawat follower **nang walang order na ipinapadala**:
- account #, server at balance
- Algo Trading ON ba sa terminal, at pinapayagan ba ng broker ang EA/API trading sa account
- nakikita ba ang symbol (hal. XAUUSD_ph kontra XAUUSD), may presyo at spread ba, at full trading ba ang symbol
- tama ba ang lot sa volume step ng broker
- **Order check ng MT5**: sinusubukan ng MT5 mismo ang isang BUY ng lot mo (margin, volume, permissions) **nang hindi ito ipinapadala**
- kapag nilagyan mo ng login # ang follower, tinitingnan kung iyon talaga ang naka-login
Kapag **✓ LAHAT HANDA**, handa na ang master at lahat ng followers para sa manual trade. Kapag may ✗, nakasulat kung ano ang aayusin.

**Setup ng follower (isang beses lang):**
1. Mag-install ng **hiwalay na MT5** para sa bawat follower sa sariling folder, hal. `C:\MT5_Switch1\`. Isang terminal = isang account.
2. Buksan ito, mag-login, i-tick ang **Save password**, at i-ON ang **Algo Trading**.
3. Sa SuperChart → Accounts → **+ Add follower**. Ilagay ang path papunta sa `terminal64.exe`, ang lot, at ang DD/TP. I-click ang **Save accounts**.
4. Hayaang bukas ang lahat ng MT5 terminal habang tumatakbo ang SuperChart. Hindi naka-save ang password dito.

**Safety:** Kapag hindi mabasa ang master (MT5 error), walang gagawing pagsasara ang app. Kailangang dalawang magkasunod na check na wala ang master trade bago isara ang mga follower. May 5 segundong palugit din ang mga bagong trade bago galawin ng sync.

## Trade Journal (awtomatiko)
Bawat BUY/SELL mula sa SuperChart ay naka-record sa `journal.jsonl` (live) o `journal_paper.jsonl` (paper):
- **Sa entry:** oras, side, lot, entry, SL/TP, mga account, at ang **SCORE sa mismong sandaling iyon** (overall %, pumasa ba sa passing score, trigger, verdict, at % bawat grupo). Naka-record din kung may **high-impact news** malapit.
- **Sa pagsara:** exit price, **paano nagsara** (TP · SL · Manual (SuperChart) · Manual (sa MT5 mismo) · Risk lock (DD/TP) · Stop out), P/L bawat account, **R multiple**, at tagal.
- Mga partial close at paglipat ng SL ay nakalista rin.
- **Journal page:** win rate, P/L, avg R, profit factor, **pumasa vs hindi pumasa** sa passing score, **Manual vs News trade**, at **entry sa news window**. May NEWS tag ang mga trade galing sa News Trade. I-click ang row para makita ang breakdown ng score. May **Export CSV** para sa Excel.
- Dito mo makikita kung talagang gumagana ang scoring mo: dapat mas maganda ang resulta ng mga pumasa.

## News reader
- **Economic calendar** mula sa ForexFactory (ngayong linggo): oras sa Manila, currency, impact (pula = High, orange = Medium, dilaw = Low), forecast · previous, at countdown. Nire-refresh kada 15 minuto.
- **Headlines** mula sa FXStreet RSS (kada 3 minuto). Naka-highlight ang mga salitang gumagalaw sa gold (gold, XAU, Fed, CPI, NFP, Powell, ...). May filter na "Gold-moving only".
- **Pill sa itaas:** ang susunod na high-impact USD news at countdown. Pula at kumukurap kapag nasa loob ng warning window (default 30 min bago hanggang 10 min pagkatapos).
- **Babala sa TRADE card at sa Confirm dialog** kapag nasa warning window. Babala lang ito; hindi nito bina-block ang trade.
- **🔊 VOICE:** binabasa nang malakas: "Heads up, USD CPI in 30 minutes", tapos 5 min, 1 min, at paglabas ng news. Kapag naka-on ang **READ HEADLINES**, binabasa rin ang mga bagong gold-related headline. Gamit ang sariling text-to-speech ng Edge. Pagkabukas ng app, mag-click muna kahit saan nang isang beses para payagan ng browser ang boses.
- **News settings** (sa ibaba ng News page): currencies (hal. `USD, EUR`), ilang minuto bago/pagkatapos, aling impact ang may babala, keywords, at dagdag na RSS feeds (`name | url`).
- Kailangan ng internet. Kapag walang internet, ipinapakita ang huling na-save (`news_cache.json`). Para patayin ang news: `--no-news`.

## Auto trade (ON/OFF), mula sa signals sa chart
Card na **AUTO TRADE** sa kanan (sa ilalim ng TRADE card). **Laging OFF pagbukas ng app.** Kailangang **naka-ARM**, at may confirm bago mag-ON. Kusa itong mag-o-OFF kapag nag-DISARM.
- Kinukuha ang **parehong signals na nakikita mo sa chart**: **Analyser Signals** (default), at kung gusto mo, pati **Conso Breakout Signals**. Kailangang naka-ON sa LAYERS ang plugin.
- Magte-trade lang sa **bar na kasasara lang**. Hindi nito kukunin ang mga lumang arrow, at hindi ito uulit sa parehong signal.
- **SL/TP = SL at TP ng signal** (itakda sa ⚙ ng Analyser Signals: atr/swing/vwap/ob at rr/vwap/atr…). Ipinapadala bilang $ distance mula sa fill ng bawat account, kaya sabay sa master at sa lahat ng followers, gaya ng manual entry.
- **Filters (⚙ SETTINGS):** timeframe, master lot, direksyon (both/buy/sell), **max ⚠ cautions** (default 0 = malinis lang), min score, **sessions** (default London + New York), **walang entry sa news window**, **max auto trades bawat araw** (default 3), max open trades (default 1), at max spread.
- Bawat desisyon ay makikita sa card at sa event log, pati kung bakit nilaktawan (hal. "1 caution: RSI 80 overbought", "news window: USD CPI", "max 3 auto trades today reached").
- Sa Journal, may **AUTO** tag ang mga ito, at may hiwalay na stats ang **Manual vs Auto vs News**.
- Hindi ito humahawak ng trailing. Tumatakbo ang SL/TP sa MT5 mismo, at pwede mong gamitin ang Break-even o Close sa SuperChart trades card.
- **Subukan muna sa paper nang ilang araw** (`run_superchart_paper.bat`).

- **Max open trades + bawal ang kontrang trade (naka-ON by default):**
  - Puwede ang ilang bukas na trade nang sabay, hanggang sa **Max open trades**.
  - Pero **iisang direksyon lang**: kapag may bukas na BUY, walang SELL na papasok, at kabaligtaran. Walang hedge.
  - Kasama ang **manual** na BUY/SELL: lalabas ang "bawal ang kontrang trade…".
  - Puwedeng i-OFF sa AUTO TRADE ⚙.
- **Re-entry (walang sabay na pasok):** Kapag may bukas nang trade, ang dagdag na entry sa **parehong direksyon** ay
  papasok lang kapag:
  1. **lumipas na ang 30 minuto** (default, "Re-entry: maghintay") mula sa huling entry sa direksyong iyon;
  2. **nasa trend pa**: kasabay pa ng HTF bias, at hindi kontra ang market regime;
  3. **pasado pa** ang analyser score sa oras ng bagong signal.

  Hanggang sa Max open trades lang. Para sa re-entry mula sa Analyser Signals, i-OFF ang `one_at_a_time` sa ⚙ nito.
- **Kumpirmasyon ng score (naka-ON by default):** Kapag galing sa **Conso Breakout** o **Trend Line Breakout** ang
  signal, papasok lang ito kapag kinumpirma ng analyser: **kasabay ng HTF bias at pasado sa passing score**, sa oras
  mismo ng bar na iyon. Makikita sa AUTO log ang "AUTO skip … (no confirmation)" kapag hindi ito kinumpirma.
  Puwedeng i-OFF sa AUTO TRADE ⚙.

## Order Flow (footprint, delta, imbalance, absorption)
Bagong page sa sidebar: **Order Flow**.
- **Mahalaga:** sa MT5 CFD gold, walang totoong exchange volume. Inuuri ang bawat tick gamit ang **tick rule**: kapag tumaas ang bid = BUY, bumaba = SELL. Ito ang karaniwang ginagawa ng order flow tools para sa FX/CFD. Ipinapakita nito ang presyon, pero hindi ito kapareho ng footprint ng GC futures.
- Pagbukas ng app, kinukuha ang **huling 12 oras na ticks** mula sa MT5, tapos live na.
- **Footprint:** bawat candle, nakahati sa price rows (`Row $`, hal. 0.25). Bawat cell ay **SELL × BUY**. Ang cell na may kulay na border ay **imbalance** (≥3× ang pahilis na kabila). Dilaw ang border ng **POC**. Nasa ilalim ang **Δ** (buy − sell), volume, at **Cum Δ** (reset bawat araw). May M1, M5, M15, M30 at H1, at 12 hanggang 40 bars.
- **Events:** **A** absorption (malaking volume pero maliit na galaw; kapag malakas ang selling pero nag-close pataas = "sellers absorbed" = bullish), **S** stacked imbalance (3 o higit pang sunod-sunod = malakas na zone), at **D** divergence (5-bar high pero malakas ang selling = bearish, at kabaligtaran).
- **Order Flow (tick delta)** indicator sa LAYERS (grupong **ORDER FLOW**): Δ histogram at Cum Δ pane sa main chart, may A/S/D markers.
- **Score:** kapag naka-SCORE, may **sariling grupo ang ORDER FLOW** (may sariling timbang sa ⚙ SCORING), na may hanggang apat na item sa vote TF nito (default M5, sarado na mga bar lang):
  - **OF Delta**: Δ ng huling 3 bars at Cum Δ. Kapag parehong positibo = bullish, parehong negatibo = bearish, kung hindi = neutral.
  - **OF Absorption**: pinakabagong absorption sa huling 6 bars ("sellers absorbed" = bullish, "buyers absorbed" = bearish).
  - **OF Imbalance**: pinakabagong stacked imbalance sa huling 6 bars. Kapag buo pa ang zone (hindi pa na-close-through), sang-ayon ito sa direksyon ng imbalance. Kapag na-close-through na, **kabaligtaran** (failed).
  - **OF Divergence**: pinakabagong delta divergence sa huling 6 bars.
  - Isinasama lang ang Absorption, Imbalance at Divergence **kapag talagang nangyari kamakailan**, kaya hindi nito binababa ang score kapag walang event. Makikita ang bawat isa sa SANG-AYON o SALUNGAT ng signal, halimbawa: "OF Absorption: sellers absorbed → bullish" habang "OF Delta: −160" ang salungat.
  - Pwedeng patayin ang bawat check sa ⚙ (`use_absorption`, `use_imbalance`, `use_divergence`) at palitan ang `lookback`.

## Intermarket (kasabay at salungat ng gold)
Card na **INTERMARKET** sa kanan. Binabasa ito mula sa **master MT5** kada 30 segundo, at awtomatikong hinahanap ang pangalan ng symbol sa broker (prefix/suffix):
- **DXY/USDX** (salungat), **EURUSD** (kasabay), **XAGUSD silver** (kasabay), **USDJPY** (salungat), **US10Y yields** (salungat), **US500** at **BTCUSD** (auto, sinusunod ang nasukat na correlation).
- Bawat isa ay may presyo, % ngayong araw, trend (EMA20/50), **ρ correlation** sa gold, at **"gold ▲/▼"**: ang ibig sabihin ng galaw nito para sa gold (hal. bumababa ang dollar = gold ▲).
- Nasa itaas ang buod: **BULLISH/BEARISH para sa gold**. Kapag walang symbol ang broker, "not on this broker" ang lalabas. Pwedeng magdagdag, magtanggal, o maglagay ng sariling symbol at relasyon sa ⚙.
- **Intermarket** indicator sa LAYERS: kapag naka-SCORE, may sariling grupo ito sa score (**Intermarket**, may sariling timbang sa ⚙ SCORING).
- Sa demo mode, **simulated** ang mga symbol (pang-test lang ng screen).

## Journal: equity curve at P/L calendar
- **Equity curve:** cumulative P/L ng TOTAL at bawat account, may **max drawdown**. I-click ang pangalan para itago o ipakita.
- **P/L calendar** (Manila time): P/L at bilang ng trades bawat araw (berde/pula, mas matingkad kapag mas malaki), total bawat linggo, at buod ng buwan (green days). I-click ang araw para makita lang ang trades noong araw na iyon.

## Conso Breakout Signals (arrows ng posibleng BUY/SELL + target profit) — SIGNAL LANG
I-ON sa LAYERS, sa grupong **SIGNALS**.
- **▲ BUY / ▼ SELL** sa bar kung saan lumabas ang presyo sa box (2 tops + 2 bottoms, ±5 pips). Default: dapat **mag-CLOSE** lampas sa box ± 5 pips (`confirm = touch` kung gusto mo ng parang stop order).
- Iginuguhit ang **entry, SL at TARGET PROFIT** (berde = TP area, pula = SL area), gaya ng long/short position tool sa TradingView, hanggang tumama.
- May resulta ang bawat signal: **✓ TP**, **✗ SL**, o **RUNNING**. Sa pinakahuling signal, may buod ng history: W/L, win rate, at kabuuang R. Kapag sabay na tumama ang SL at TP sa isang bar, SL ang binibilang para siguradong hindi pinapaganda ang resulta.
- SL = kabilang side ng box (max $10) o fixed $. TP = SL × RR (default 2R). Kapag `ema_len` > 0 (hal. 50), BUY lang sa itaas ng EMA at SELL lang sa ibaba (trend filter).
- **Conso Breakout alert** (strategy, i-ON sa Plugins page): nag-a-alert sa Signal Center at event log tuwing may bagong breakout sa M5, kasama ang entry/SL/TP at kung **sang-ayon o salungat ang analyser bias**. Walang order.

## Analyser Signals (arrows mula sa SCORE mo)
I-ON sa LAYERS, sa grupong **SIGNALS**. Signal lang ito, walang trade.
- **▲ BUY / ▼ SELL** (asul/pink) sa bar kung saan nag-**READY** ang analyser: HTF bias + trigger (CHoCH/BOS) + score na pumasa sa passing score mo. Ginagamit nito **mismong mga indicator na naka-ON sa SCORE** at ang **timbang ng bawat grupo** mo.
- Kinukuwenta ulit ang score ng bawat lumang bar gamit **lang ang mga bar na sarado na noong oras na iyon**. Walang silip sa hinaharap, kaya tapat ang resulta.
- Isang arrow lang sa **simula ng bawat READY**. Hangga't tuloy-tuloy ang READY, walang bagong arrow. Kapag `one_at_a_time` = true (default), walang bagong signal habang tumatakbo pa ang nauna.
- Bawat signal ay may **SCORE %**, entry, SL at TP, at resulta: **✓ TP / ✗ SL / ⇢ TRAIL / RUNNING**. Sa huling signal, may W/L, win rate at kabuuang R.
- SL (`sl_mode`): `atr` (1.5×ATR, default) | `swing` (pinakamababang low / pinakamataas na high ng huling 10 bars) | `vwap` | `ob` | `fixed`. TP (`tp_source`): `rr` (2R, default) | `vwap` | `ob` | `atr` | `all`. Trail: `none` | `vwap` | `atr`.
- `min_score` > 0: gamitin ang sariling threshold imbes na ang passing score, para masubukan kung ano ang mangyayari sa 60% o 80%.
- **Paano gamitin:** palitan ang mga indicator na naka-SCORE, ang timbang ng grupo, o ang passing score, tapos tingnan kung tumataas ang win rate at R. Awtomatikong nire-recalculate ang mga arrow. Ang unang load ay mga 1 segundo; mabilis na pagkatapos.
- Hanggang 300 bars pabalik (`lookback`).

**Bakit nag-signal, at ano ang salungat** (panel na **SIGNALS** sa **ilalim ng chart**; i-click ang row o ang arrow sa chart). Nasa kaliwa ang listahan ng mga signal. Sa kanan, magkakatabi ang **SANG-AYON | SALUNGAT | ⚠ CAUTION** ng napiling signal, pati ang % ng bawat grupo. Kasama rito ang Analyser Signals at ang Conso Breakout Signals (BOX):
- **SANG-AYON:** bawat check at indicator na pabor sa trade, halimbawa "Momentum: RSI (RSI 56)" o "Structure: Trigger (M5 CHoCH UP)".
- **SALUNGAT:** bawat indicator o check na kontra, halimbawa "Structure: M15 aligned (M15 BEAR)".
- **NEUTRAL:** mga indicator na walang direksyon noong oras na iyon.

**⚠ CAUTION: posibleng reversal kahit pasado ang score.** Hiwalay ito sa score at hindi nito binabago ang score, kaya tumatakbo ito kahit naka-OFF ang mga indicator na ito:
- RSI ≥ 70 sa BUY / ≤ 30 sa SELL (overbought/oversold), at Stochastic ≥ 80 / ≤ 20
- RSI divergence laban sa trade (HH ang presyo pero LH ang RSI sa BUY)
- Nasa loob o malapit (≤ 0.5×ATR) sa **kabilang order block**
- Lampas na sa VWAP ±2σ (stretched)
- Malapit sa **PDH** (BUY) o **PDL** (SELL)
- Malaki na ang itinakbo, higit 3×ATR sa huling 6 bars (huli na ang entry)

Dilaw ang arrow kapag may ⚠, at may "⚠2" sa label. Sa kanang column, ang card na **SIGNAL SCORE RATE** ay nagpapakita ng win rate, W/L at R: lahat, **malinis vs may ⚠**, at bawat source (Analyser vs Conso Breakout), para makita mo kung dapat iwasan ang mga may caution. Kapag napatunayan mo, gamitin ang `max_cautions` sa ⚙: `0` = huwag ipakita ang signal na may kahit isang ⚠, `1` = payagan ang isa, at `-1` (default) = ipakita lahat pero markahan.

Nasa **Confluence read** din ang live ⚠ CAUTION (M5 at M15) para sa kasalukuyang bias, at may "⚠ n caution" sa verdict card.

## TP/SL group (VWAP, Order Blocks at ATR para sa target, stop at trailing)
Bagong grupo sa LAYERS: **TP/SL**. Signal lang lahat ito, walang trade.
- **VWAP Bands (TP/SL):** daily VWAP na may ±1σ, ±2σ at ±3σ. Karaniwang gamit: TP sa susunod na band sa direksyon mo, SL lampas sa VWAP o sa band sa likod mo, at i-trail ang SL kasama ng VWAP. Hindi hinahatak ng ±2σ at ±3σ ang scale ng chart.
- **Order Blocks:** ang huling kabaligtarang candle bago ang break of structure. Ang **Bull OB** (berde) ay support: dito sa ilalim ilalagay ang SL ng BUY, at ito ang target ng SELL. Ang **Bear OB** (pula) ay resistance: target ng BUY, at SL ng SELL sa itaas nito. Aktibo ito hangga't walang candle na nag-close lampas dito. Ang mga nasira ay may ✗ at kupas. Ipinapakita lang ang mga OB na malapit sa presyo (`max_dist` $25).
- **ATR Exits (chandelier):** ATR na SL at TP. **Chandelier trailing stop**: para sa BUY, pinakamataas na high ng huling 22 bars − 3×ATR (berde); para sa SELL, pinakamababang low + 3×ATR (pula). Kapag nag-close ang presyo lampas sa trail, lilipat ito sa kabilang side. May levels din para sa kasalukuyang presyo: SL = 1.5×ATR, TP1 = 2×ATR, TP2 = 3×ATR (`show_levels` = false kung ang TP/SL Map na lang ang gusto mo).
- **TP/SL Map:** para sa **kasalukuyang presyo**, ipinapakita ang posibleng **TP1, TP2, SL at trailing (TRL)** mula sa **VWAP, OB at ATR**. Source: `all` (default), `vwap`, `ob`, `atr`, o kombinasyon gaya ng `vwap+atr`. Ang trail ay ang mas masikip sa VWAP at ATR chandelier. Ang side ay `auto` (sinusunod ang analyser bias, o kung wala, ang presyo kumpara sa VWAP), o `buy`, `sell`, o `both`. Kailangang kahit `min_rr` (1R) ang layo ng TP. May buod na label, halimbawa "BUY map: TP1 … (VWAP 2.3R) · TP2 … (Bear OB) · SL … (Bull OB)".

**Sa Conso Breakout Signals, pwede mong subukan ang VWAP/OB bilang TP/SL** (⚙ ng indicator):
- `tp_source`: `rr` (default) | `vwap` | `ob` | `atr` | `all` | kombinasyon gaya ng `vwap+atr`. Kukunin ang unang level na kahit `min_rr` × SL ang layo. Kapag walang ganoong level, RR ang gagamitin.
- `sl_mode`: `box` | `fixed` | `vwap` | `ob` | `atr` (1.5×ATR).
- `trail`: `none` | `vwap` | `atr` (chandelier mula sa entry, 3×ATR). Susunod ang SL sa VWAP o sa ATR trail, pero sisikip lang ito at hindi lalayo. Ang resulta ay **⇢ TRAIL** at ang aktwal na R.
- Palitan-palitan ang mga ito at tingnan ang W/L at kabuuang R sa label ng huling signal. Doon mo makikita kung mas maganda ang VWAP kaysa sa fixed RR sa XAUUSD_ph.

## Backtest / Strategy tester (at "Apply best setup")
Page **Backtest** sa kaliwa. Kumukuha ng history mula sa **master MT5** (M5 hanggang D1) at ini-score ang bawat
bar gamit ang **parehong Analyser** (mga plugin na naka-ON sa SCORE at ang timbang mo), gamit lang ang mga bar na
sarado na noon kaya walang silip sa hinaharap. Pagkatapos, ginagaya ang Analyser Signals: entry, SL, TP at trail.
- **Run backtest**: ang KASALUKUYANG setup mo (Analyser Signals ⚙, SCORING passing at timbang, Auto trade filters).
  Makikita ang trades, win rate, total R, profit factor, max drawdown, $ sa lot mo (100 oz/lot), equity curve (R),
  resulta bawat buwan at listahan ng trades.
- **Optimize**: sumusubok ng mga 2,500 kombinasyon (passing score 55-90%, SL ATR/swing, TP R/ATR/VWAP, trail, max ⚠,
  sessions; kapag naka-check ang "also try group weights", pati ang timbang ng mga grupo).
  - **In-sample (unang 70%)**: dito PINIPILI ang pinakamaganda.
  - **Out-of-sample (huling 30%)**: hindi ito nakita ng optimizer. Kapag kumita rin dito (✓ robust), mas
    mapagkakatiwalaan ang setup. Kapag maganda lang sa in-sample at talo sa out-of-sample, **overfit** iyan:
    tumugma lang sa nakaraan.
  - **Test**: patakbuhin ang buong period gamit ang setup na iyon. **Apply**: may confirm, tapos isusulat sa Analyser
    Signals ⚙, SCORING at Auto trade filters. Makikita agad ang bagong arrows. **Hindi nito binubuksan ang AUTO TRADE.**
- May bayad na spread sa bawat entry (Spread cost $, default 0.30). Isang trade lang sa isang pagkakataon.
- **Skip recent days**: para mag-test sa mas lumang panahon, tapos ikumpara sa pinakabago (walk-forward).
- **Custom dates** (sa Period): pumili ng **From** at **To** na petsa, gaya ng sa MT5 Strategy Tester. Ang petsa ay
  sa oras ng broker server, kasama ang buong araw ng To, at hanggang 1 taon. Kung hindi abot ng history ng MT5 ang
  From, sasabihin ito ng terminal. Taasan ang "Max bars in chart" o i-scroll pabalik ang M5 chart.
- Paalala: sariling tester ito ng terminal, **hindi** ang Strategy Tester ng MT5. Ang MT5 tester ay para lang sa
  EA na nakasulat sa MQL5. Pareho ang ideya: history mula sa broker, at walang silip sa hinaharap.
- Tandaan:
  - Ang **Order Flow** (kailangan ng live ticks) at **Intermarket** ay walang lumang data, kaya halos wala silang
    boto sa backtest.
  - Limitado ang history sa dami ng bar na hawak ng MT5. Para sa mahabang period, taasan ang "Max bars in chart"
    (Tools > Options > Charts) at i-scroll pabalik ang chart.
  - Sa demo mode, SIMULATED ang data: pang-test lang ng screen, walang kahulugan ang resulta.
  - Ang backtest ay hindi garantiya. Subukan muna sa paper/demo account ng ilang linggo bago mag-live.

## News box (dating News Trade)
**Naka-patay muna ang automatic news trading.** Ang card na **NEWS BOX** ay pang-tingin lang: ipinapakita nito ang box, at kung may news, kung saan papasok ang buy stop o sell stop. Nandiyan pa ang code, at pwedeng buksan ulit sa susunod na version kapag napatunayan na sa testing.
Ganito ang gagawin nito kapag binuksan ulit:
1. **Nakikita ang box kahit bago pa ang news:** laging ipinapakita sa chart ang **NEWS BOX** (at sa NEWS TRADE card kung ilang tops/bottoms).
2. **Ano ang box (consolidation):** kahit **2 tops sa parehong level** at **2 bottoms sa parehong level**, ±**5 pips** (1 pip = $0.10 sa gold, kaya ±$0.50). Kailangang **salitan** ang tops at bottoms (top → bottom → top…). Kapag lahat ng bottoms ay nauna at saka lang ang tops, pataas na galaw iyon at hindi box. Mula sa unang touch, dapat nasa loob pa rin ng box ang presyo hanggang ngayon. Default sa M5, hinahanap sa huling 48 bars, box mula $0.50 hanggang $10.
3. **120s bago ang high-impact news** (galing sa News settings ang currencies at impact): kapag may box, **BUY STOP 5 pips sa itaas** ng box at **SELL STOP 5 pips sa ibaba**, sa **master at lahat ng naka-enable na follower** (sariling fixed lot ang follower; inaayos ang presyo ayon sa quote ng broker nito). **Walang box = walang trade** (nakalog kung bakit).
   - SL = kabilang side ng box (may cap na Max SL $10) o fixed $. TP = SL × RR (default 2) o fixed $.
   - **Grid levels**: 1 = isang buy stop + isang sell stop. 2 pataas = dagdag na stops bawat "Grid step $".
4. **OCO:** kapag na-trigger ang isang side sa isang account, kinakansela agad ang kabilang side sa account na iyon.
5. Kinakansela ang hindi na-trigger **10 min** pagkatapos ng news.
6. Ang na-trigger na trade sa master ay nagiging normal na SuperChart trade: may journal (NEWS tag, score, news), at sumusunod ang followers sa close / SL-TP ng master.
- **Safety:** kapag nag-OFF, nag-DISARM, o na-lock ang master (daily DD/TP), kinakansela ang lahat ng news pending orders. Pagbukas ng app, kinakansela rin ang mga naiwang news pending order mula sa dating run. Makikita sa chart ang **NEWS BOX** at ang **BS/SS** lines.
- Lahat ng setting ay nasa ⚙ SETTINGS ng card (saved sa `news_trade_settings.json`).
- **Subukan muna sa paper** (`run_superchart_paper.bat`) nang ilang news bago mag-live.

**Consolidation Box indicator** (Volatility group sa LAYERS): pareho ang rule (2 tops + 2 bottoms, ±5 pips, salitan). Iginuguhit nito ang mga dating box at ang LIVE box (BXH/BXL lines). Hiwalay ang settings nito sa News Trade.

# REYGUN TERM

Terminal-style chart reader para sa MT5. Babasahin nito ang live ticks at candles galing sa MT5 sa VPS, tapos ipapakita ang structure read (BOS/CHoCH, sweeps, FVG, premium/discount) sa iisang screen.

**Read-only lang ito. Hindi ito nag-o-open ng trades.**

## Install (Windows VPS)
1. Mag-install ng Python 3.10+ galing python.org. I-check ang "Add Python to PATH".
2. I-extract ang folder, tapos i-double-click ang `setup.bat`.
3. Buksan ang MT5 at mag-log in. Dapat nasa Market Watch ang XAUUSD.
4. I-double-click ang `run_live.bat`.

Kung iba ang suffix ng symbol ng broker mo (halimbawa `XAUUSD.m` o `GOLD`), palitan ang `--symbol` sa loob ng `run_live.bat`.
Kung marami kang MT5 sa VPS, idagdag ang `--mt5-path "C:\Program Files\...\terminal64.exe"`.

Tip: Mas malinaw ito sa Windows Terminal kaysa sa lumang cmd. Gawing full screen, mga 160x48 o mas malaki.

## Modes
| Mode | Command |
|---|---|
| Live MT5 | `python reygun_term.py --mode live --symbol XAUUSD` |
| Demo (walang MT5, synthetic data) | `python reygun_term.py --mode demo --speed 20` |
| Replay ng recording | `python reygun_term.py --mode replay --file recordings\XAUUSD_2026....csv --speed 50` |

Sa `--record` o sa pag-press ng `r`, sine-save ang bawat tick sa `recordings\`. Pwede mo itong i-replay mamaya sa mas mabilis na speed.

## Keys
`SPACE` pause · `+`/`-` replay speed (1x hanggang 500x) · `1`-`5` chart TF (M1 M5 M15 H1 H4) · `p` plugins on/off · `r` record on/off · `c` clear log · `q` quit

## Panels
1. **CHART**: plain candles lang by default. Ang indicator plugins ang nagdadagdag ng lines, levels, markers, at zones.
2. **ANALYSER**: trend at last break per TF (H4/H1/M15/M5), P/D % sa dealing range, BSL/SSL levels, FVGs, ATR
3. **TAPE**: bawat tick (bid, direction, spread; dilaw kapag spread > 0.40)
4. **EVENT LOG**: bagong BOS/CHoCH/SWEEP/FVG habang nangyayari (naka-highlight ang H4/H1 breaks)
5. **READ**: bias, confluence score 0-6, at verdict (READY / WATCH / WAIT), status ng naka-on na strategies, balance/equity, at open positions

## Plugins (paunti-unting pagdagdag ng indicator / strategy)
Lahat ng `.py` sa `plugins\` ay auto-load pag-start. Press `p` para i-on/off (naka-OFF lahat sa umpisa). Naka-save ang pinili mo sa `plugins_enabled.json`.

| File | Type | Ginagawa |
|---|---|---|
| `ema.py` | Indicator | EMA 20/50 lines |
| `prev_day_levels.py` | Indicator | PDH / PDL / PDC |
| `smc_overlay.py` | Indicator | SH/SL, B/C/x markers, FVG zones |
| `premium_discount.py` | Indicator | H1 premium/discount zones + EQ · idinadagdag ang location sa score |
| `sessions.py` | Indicator | Asia / London / New York boxes (UTC hours, editable at the top of the file) |
| `period_separator.py` | Indicator | vertical line per day (M1-H1), week (H4), month (D1) |
| `ema_cross.py` | Strategy | sample: M5 EMA cross + H1 filter |
| `smc_ready_alert.py` | Strategy | alert kapag READY ang analyser |
| `_template.py` | - | kopyahin para gumawa ng bago |

**Paano magdagdag:** kopyahin ang `_template.py` gamit ang bagong pangalan na walang `_` sa unahan (halimbawa `my_rsi.py`), i-edit ito, i-restart ang terminal, tapos press `p`.
- **Indicator** → `compute(bars, tf, ctx)` at mag-return ng `Overlay(lines, levels, markers, zones)`
- **Strategy** → `evaluate(ctx)` at mag-return ng `Signal(t, direction, reason, entry, sl, tp)`. Lalabas ito sa event log na may beep. **Alert lang ito, hindi trade.**
- Kapag nag-error ang isang plugin, idi-disable lang ang plugin na 'yon at ilalagay ang error sa log. Hindi magka-crash ang terminal.

## Indicator pack: naka-grupo ayon sa GAMIT (lahat naka-OFF sa umpisa, i-on sa LAYERS)
Bawat indicator ay may **kulay na tag ng grupo** at maikling **"para saan"**. Ang grupo rin ang basehan ng score (1 puntos bawat grupo).

| Grupo | Para saan | Indicators |
|---|---|---|
| **TREND** | Direksyon ng market at lakas ng trend: pataas ba o pababa? | EMA 20/50, EMA Ribbon, SMA 20/50/200, Supertrend, Parabolic SAR, Ichimoku, ADX/DMI |
| **MOMENTUM** | Bilis ng galaw, overbought/oversold: lumalakas ba o humihina? | RSI, MACD, Stochastic, CCI, Williams %R |
| **VOLATILITY** | Laki ng galaw, bands at channels: extremes, breakouts, laki ng SL | Bollinger, Keltner, Donchian, ATR |
| **VOLUME** | Dami ng galaw at flow: kinukumpirma ba ang galaw? | Volume, MFI, CVD (tantya), OBV, VWAP, Volume Profile |
| **LEVELS** | Mga presyong support at resistance | Pivot Points, Prev Day H/L/C, Auto Fibonacci |
| **SMC** | Structure, liquidity, premium/discount | SMC Structure, Premium/Discount |
| **TIME** | Sessions at hati ng araw/linggo | Sessions, Period separator |

Tungkol sa volume: **tick volume** lang ang binibigay ng MT5 para sa gold CFD (bilang ng galaw ng presyo), hindi totoong exchange volume. Kaya ang MFI, OBV, VWAP, at Volume Profile ay naka-base sa tick volume. Ang **CVD ay tantya lang**: walang buy/sell data ang MT5, kaya kinukuwenta ito mula sa katawan ng candle kumpara sa range nito (`delta = volume x (close-open)/(high-low)`). Mas maaasahan ito para sa divergence kaysa sa eksaktong numero.

Sa blue terminal (REYGUN TERM), ang mga indicator sa price chart lang ang lalabas. Ang mga panel indicator (RSI, MACD, at iba pa) ay sa SuperChart lang.

## CHART, SCORE at ⚙ settings (sa LAYERS menu o sa Plugins page)
- **CHART**: iguhit ang indicator sa chart.
- **SCORE**: isama sa analyser score. **Gumagana kahit naka-off ang CHART**, kaya pwedeng malinis ang chart pero kasama pa rin sa score.
- **⚙**: palitan ang settings (length, multiplier, levels, at iba pa) at ang **vote timeframe**. Pindutin ang **Apply** para i-save, o **Reset to default** para ibalik sa dati.
- Naka-save lahat sa `plugin_settings.json`. Awtomatikong nalilipat ang lumang `plugins_enabled.json`.
- Sa blue terminal, CHART lang ang nai-toggle ng `p`. Ang SCORE at settings ay itinatakda sa SuperChart, pero pareho silang gumagamit ng iisang settings file.


**Timeframe bawat indicator (chart + score):** sa ⚙, i-check ang mga TF. Hal. SMC sa H4 lang, VWAP at EMA sa M15
lang. Kung nasaan ang indicator, doon din ang score nito; wala sa ibang TF.
- Kapag higit sa isang TF ang naka-check, **isang boto lang** ito: majority ng mga TF, at kapag tabla, neutral.
- Ang Premium/Discount at SMC Structure checks ay sumusunod din, pero H4/H1/M15/M5 lang ang may structure data.
- Kapag walang naka-check, lalabas ito sa lahat ng TF sa chart, at ang score ay ayon sa "vote timeframe" gaya ng dati.
- Pareho ang live at backtest. Hindi apektado ang AUTO TRADE signal plugins.

Para bumoto ang sarili mong indicator, lagyan ito ng `vote(self, ctx)` na nagre-return ng `Vote(+1 / -1 / 0, "reason")`, at `category` para sa grupo nito.

## Engine rules (engine.py): deterministic, walang AI
- Swings: fractal, 3 bars bawat side sa H4/H1, 2 sa M15/M5
- BOS/CHoCH: kapag **nag-close** lampas sa huling swing. CHoCH kapag kontra sa kasalukuyang trend.
- Sweep: wick lampas sa swing pero nag-close pabalik sa loob (BSL = high, SSL = low)
- FVG: 3-candle gap na > 0.15 x ATR, aktibo hangga't hindi pa fully filled
- Score (+1 bawat isa): may HTF bias · H1 aligned · M15 aligned · tamang side ng H1 range · opposite-side sweep sa M15/M5 · M5/M15 CHoCH/BOS trigger sa direksyon ng bias
- **READY** = score >= 5 + trigger + tamang location

Lahat ng oras ay broker server time.

## Safety layer (bago)
- **Server confirmation ng manual trade:** Kapag pinindot mo ang BUY o SELL, ang **server** ang unang nagsusuri ng
  lahat (ARM, lock, kontrang trade, halted…). Kapag pumasa, naglalabas ito ng **one-time token** na tatagal lang
  nang **15 segundo**. Nakatali ang token sa account, symbol, side, lot, SL at TP.
  - Kapag binago ang alinman sa mga iyon o nag-expire ang token, walang order.
  - Hindi na sapat ang dialog sa browser lang.
- **Walang dobleng order:** May **request_id** ang bawat request. Kapag naulit ito (retry, double-click), ang sagot
  ay **ALREADY_EXECUTED**. May sariling request_id rin ang auto trade bawat signal.
- **⏸ STOP NEW TRADES** (sidebar): AUTO OFF, DISARM, kinakansela ang lahat ng REYGUN pending orders. Hindi nito
  isinasara ang mga position. Para magpatuloy, i-ARM ulit.
- **🛑 EMERGENCY STOP:** Pareho sa STOP NEW TRADES. Isasara rin nito ang SuperChart positions kung iche-check mo
  iyon mismo sa dialog. Hindi kailanman ginagalaw ang ibang EA o manual trades.
- **Protective SL watchdog:** Kapag nag-fill ang trade pero walang SL sa isang account, lalabas ang
  **⚠ UNPROTECTED POSITION**.
  1. Ikakabit ang SL, hanggang 3 subok, 2 segundo ang pagitan.
  2. Kapag hindi talaga maikabit, **isasara ang leg na iyon** (`close_unprotected`, naka-ON), at may alert sa
     Telegram.
- **Follower freshness:** Hindi gagalawin ang follower kapag luma ang basa ng positions niya, at ipapakita ang
  "FOLLOWER DATA STALE". Kapag nagsara ang master, hinihintay ang sariwang basa bago isara ang follower (hanggang
  10 minuto, tapos alert). Hindi na mauulit ang naiiwang bukas na follower.
- **Daily DD/TP bawat account:** Sariling broker day ng bawat account (sariling server time). Ang default na
  "When hit, close" ay **only SuperChart trades**. Ang "ALL trades" ay kailangang piliin mo mismo; ibinalik sa
  SuperChart-only ang mga lumang setting.
- **Lifecycle sa journal:** Naka-record ang bawat hakbang, halimbawa REQUESTED → CONFIRMED → RISK_APPROVED → SENT →
  FILLED → PROTECTED → CLOSING → CLOSED. Kasama rin ang mga bigong order (FAILED), pero hindi sila binibilang sa
  stats.
- **Tests:** `python tests/run_tests.py`. Tumatakbo ito sa sariling temp folder, kaya hindi nagagalaw ang settings
  mo.

## Command Center (bagong page, pinakaitaas sa sidebar)
Isang tingin kung **bakit** may trade o wala. Binabasa lang nito ang dating analyser at plugins: **hindi nito
binabago ang score** at hindi ito nagte-trade nang mag-isa.
- **Taas:** H4 / H1 / M15 / M5, score vs passing, trigger, regime, session, news, spread, risk, at ang
  **decision**: `WAIT`, `WATCH`, `READY`, `BLOCKED`, `OPEN` o `MANAGING`. May kulay na tuldok din sa sidebar
  (hindi pumupula kapag ARM OFF lang ang dahilan).
- **Setup matrix:** bawat item na may ✓ / ✗ at uri nito. Magkahiwalay ang tatlo:
  - **REQUIRED** (score ≥ passing, HTF bias, trigger): kailangan para maging READY.
  - **confirmation** (M15 structure, premium/discount, FVG, OB, VWAP, VP, order flow, intermarket, regime,
    session): impormasyon lang, **hindi dinadagdag sa score**.
  - **blockers** (ARM off, lock, kontrang trade, spread, news, execution…): kapag may hard blocker, BLOCKED.
- **Why not trade?** Ang eksaktong kulang o nakaharang.
- **Trade plan:** entry, SL, TP, RR, risk $ at target $, lot bawat account. Ang **CONFIRM** ay dumadaan sa parehong
  server check + 15 s na one-time token ng BUY/SELL sa sidebar. Naka-disable kapag hindi naka-ARM o may hard
  blocker. May babala kapag hindi READY.
- **Decision timeline:** kailan naging WATCH / READY / BLOCKED / OPEN, kailan lumabas ang trigger.
- **Telegram:** may bagong event na `READY` at `BLOCKED` (isang beses bawat 10 minuto bawat side; hindi nagpapadala
  ng BLOCKED kapag ARM OFF lang ang dahilan). I-on/off sa System & Alerts.

## Execution guard (broker check bago ang bawat order)
Bawat account (makikita sa Command Center → Broker / execution monitor): connection, trading allowed, symbol,
lot min/step/max, **stops level**, **freeze level**, filling, spread, latency, gaano katanda ang data.
- Kapag may problema ang **master**, walang order (hal. `BROKER_STOPS_LEVEL`: masyadong malapit ang SL).
- Kapag may problema ang isang **follower**, ang follower lang na iyon ang nilalaktawan (`FOLLOWER_SKIPPED`), tuloy
  ang iba.
- Default: **kailangan ng SL** (`NO_PROTECTIVE_SL`). Settings: `execution_settings.json`.

## Risk bawat account
Sariling broker day, daily P/L, DD limit, TP target, bukas na trades, open risk at trades today ng bawat account
(Command Center → Risk bawat account).

## Copier reconciliation (master vs followers, bawat 5 s)
Ikinukumpara ang inaasahan (master) sa totoong nasa bawat follower:
- **Kontrang side** → alert + **PAUSE** ng follower na iyon (walang bagong trade hanggang pindutin ang **Resume**).
- **Sobrang lot** → isinasara ang sobra. **Kulang na lot** → report lang, hindi dinadagdagan (dagdag risk iyon).
- **Iba ang SL/TP** → inaayos para kapareho ng layo ng master.
- **Orphan** (REYGUN position na wala nang group) → isinasara pagkalipas ng 30 s (News Trader: alert lang).
- **Lumang data** → walang galaw. **3 bigong ayos** → PAUSE.
- May **auto repair** checkbox; kapag OFF, report at alert lang.

## Journal analytics (descriptive lang)
Sa Journal page: resulta **ayon sa exit** (TP, SL, BE, TRAIL, EXIT_GUARD, RISK_LOCK, MANUAL…), **ayon sa session**
at **regime** sa entry, **ayon sa setup** (fingerprint mula sa decision engine), at **MFE / MAE** (gaano kalayo
pumabor / kumontra, ilang R ang naiwan sa mesa). Walang "best setup" ranking at walang binabagong setting.

## Backtest: costs, walk-forward at Monte Carlo
- **Costs:** spread (fixed o **variable** kung may history ang MT5), commission bawat lot, slippage, swap bawat
  gabi, execution delay (kapag > 0, sa open ng susunod na bar papasok), at **Break-even** sa X R.
- **Intrabar (M1):** kapag parehong bar tinamaan ang SL at TP, ang M1 ang titingnan kung alin ang nauna. Kapag
  walang M1 history, bar mode (SL muna, konserbatibo). Makikita sa Trades table ang **Mode** at bawat event.
- **Optimizer sensitivity:** bawat top setup ay sinusubok sa maliit na palit ng parameter. **✓ stable** o
  **POSSIBLE OVERFIT**.
- **Walk-forward:** optimize sa TRAIN window (hal. 20 araw), subok sa sumunod na TEST window (hal. 5 araw) na
  hindi pa nakita, tapos iurong. Ipinapakita: OOS total R, ilang window ang kumita, ilang beses nagpalit ng setup.
- **Monte Carlo:** parehong mga trade sa ibang pagkakasunod (at opsyonal na dagdag slip / nilaktawang trade):
  5% / median / 95% ng total R, max drawdown at losing streak. **Stress test ito, hindi hula.**
- **Ligtas na daan:** OPTIMIZE → review → WALK-FORWARD → PAPER ng ilang araw → saka manual na **Apply**. Hindi
  kailanman kusang ina-apply ng optimizer sa live.

## Volume Profile (daily): iisa na lang ang Volume Profile (LAYERS → VOLUME)
Hindi ginagamit ang profile ngayong araw sa entry dahil nabubuo pa lang ito. Ang basehan ay ang **buong profile ng
nakaraang trading day**.
- **Sa chart:** may sariling profile ang bawat araw, iginuguhit sa loob ng araw na iyon, para sa huling
  `days_shown` na araw. Hanggang **5** lang para hindi bumigat.
  - May kulay kahel ang POC ng bawat araw, at may faint na kahon ang value area nito.
- **pPOC / pVAH / pVAL:** mga linya ng KAHAPON. Dinadala rin sa ngayong araw ang value area nito, bilang "prev day
  value area".
- **show_today** (OFF sa umpisa): ipakita ang developing profile ngayong araw, faint at kulay abo. Hindi ito
  ginagamit sa vote.
- Nakapirmi ang mga level mula sa unang bar ng araw at walang bar ngayong araw ang kasama, kaya pareho ang live at
  backtest.
- Broker server day ang basehan, at nilalaktawan ang maikling araw (Sunday open).
- **Data:** M5 kapag sapat ang history, at kung hindi, M15, M30 o H1, para laging may 5 araw kahit kaunti ang bars
  na naka-load sa MT5.
- **Vote:** close sa itaas ng pVAH = +1, sa ibaba ng pVAL = −1, at sa loob ng value area = 0 (rotation).
- **Settings:** days_shown (1–5), rows, value area %, min_bars, show_today, show_value_box, profile_width.

## Forecast (bagong page: prediction lang, HINDI trade)
Isang **read-only** na layer: binabasa nito ang chart at gumagawa ng forecast na puwedeng i-test, pero **walang
order**. Hindi rin nito binabago ang score, ang TF settings, ang AUTO TRADE o ang execution. Violet ang kulay, may
"FC" na label, at may "FORECAST" na banner, kaya hindi ito mapagkakamalang totoong trade.
- **Regime:** NO_DATA / NO_BIAS / RANGING / TREND / PULLBACK / BREAKOUT / REVERSAL (BULL o BEAR). Kapag hindi
  malinaw ang market, walang forecast; hindi ito pinipilit.
- **Bawat forecast:** ID (FC-000017), direksyon, regime, bawat TF (H4 / H1 / M15 / M5 / M1), analyser score (hindi
  binabago), **forecast confidence** (hiwalay, 0–100, may breakdown), setup A+ / A / B / C, at entry na MARKET /
  LIMIT (zone) / BREAKOUT.
  - SL mula sa structure (sweep extreme, zone, swing); ATR kapag wala.
  - TP1 1R, TP2 = RR (default 2), TP3 = opposing liquidity o RR+1.
- **Lifecycle:** WAITING → TRIGGERED → RUNNING → TP_HIT / SL_HIT, o EXPIRED / INVALIDATED.
  - Hindi ito panalo hangga't hindi na-trigger ang entry.
  - Kapag nasira ang setup bago ang entry, **INVALIDATED** ito (hindi SL), may reason: HTF_BIAS_FLIPPED,
    OPPOSITE_CHOCH, ENTRY_ZONE_INVALIDATED, STRUCTURE_BROKEN, SETUP_REPLACED o MANUAL_CANCEL.
  - Kapag parehong tinamaan ang SL at TP sa iisang bar: **AMBIGUOUS_BAR**, at default ay SL muna (conservative).
    Sa live, ang totoong pagkakasunod ng ticks ang gamit.
- **Snapshot:** naka-save ang lahat sa `forecast_journal.jsonl`, hiwalay sa trade journal, at hindi binubura o
  binabago ang lumang record.
- **Stats:** win rate, trigger rate, avg R, PF at MFE / MAE, ayon sa regime, direksyon, quality, session, araw at
  buwan. May **confidence calibration** (lumalabas ang "kulang pa" hanggang umabot ng 30 triggered bawat bucket) at
  **score vs result**, para makita kung may silbi ang score nang hindi ito binabago.
- **Replay:** subukan sa history gamit ang `read_at()` (sarado na bars lang). Pareho ang resulta tuwing uulitin.
- **Settings:** para sa Forecast lang, hindi sa AUTO TRADE.
- **Forecast chart:** may sariling chart ang Forecast page (M1 / M5 / M15 / H1 / H4).
  - Violet ang entry, SL at TP ng forecast, at may marka kung saan ginawa, pumasok at natapos ang bawat forecast.
  - Lumalabas din dito ang mga indicator na naka-CHART sa forecast setup.
- **Forecast indicators (parang demo account):** puwedeng **LIVE setup** (parehong score ng terminal) o **sariling
  forecast setup**.
  - Sa sariling setup, pipili ka ng CHART, SCORE, TF, vote TF at params bawat indicator, pati sariling passing %.
  - Tumatakbo ito sa hiwalay na analyser sa parehong presyo, kaya **hindi naaapektuhan** ang live score, ang live
    indicators at ang AUTO TRADE.
  - "Copy from live" para magsimula sa kasalukuyang setup.
  - Nakatala sa bawat forecast kung LIVE o FORECAST setup ang ginamit, at ginagamit din ito ng replay.
- **Comparison forecast (Structure vs Volume Profile):** sabay na gumagawa ng forecast ang bawat setup na naka-check
  sa Settings → *Setups na ikukumpara*. May tig-isang active forecast ang bawat setup.
  - **Structure:** ang dating forecast (trend / pullback / breakout / reversal).
  - **Structure + POC pullback (pinagsama):** market structure ang main indicator; ang pPOC ng kahapon ang lugar ng entry.
    1. Structure ang direksyon: TREND o PULLBACK lang (H4 / H1 bias + M15 structure). Walang setup kapag RANGE o NO BIAS.
    2. Nasa panig ng trend ang presyo (LONG: nasa itaas ng pPOC) at hindi lalayo sa `sp_max_dist_atr` (default 3 ATR).
    3. Dapat nasa LOOB ng structure ang POC. Sa LONG, nasa itaas ng M15 swing low (HL); sa SHORT, nasa ilalim ng
       M15 swing high (LH). Kung lampas doon ang POC, sira na ang structure pagbalik ng presyo, kaya walang setup.
    - **Entry:** LIMIT sa pPOC. Naghihintay (WAITING) hanggang bumalik ang presyo sa POC.
    - **SL:** lampas sa M15 swing (structure). Kung hindi puwede, pVAL / pVAH o ATR.
    - **TP2:** ang M15 swing high / low na pinanggalingan ng pullback (structure target), kung RR ≥ `vp_min_rr`;
      kung hindi, ang RR sa settings. TP3: pVAH / pVAL.
    - **Invalid:** kapag may opposite CHoCH, nag-flip ang bias, nag-close lampas SL, o umabot sa TP2 nang hindi
      bumalik sa POC (`MISSED_NO_PULLBACK`).
  - **VP · POC rejection:** tumama ang bar sa **pPOC** ng kahapon, nag-reject, at nag-close pabalik.
    - Rejection pataas → LONG, target **pVAH**.
    - Rejection pababa → SHORT, target **pVAL**.
  - **VP · VAH/VAL rejection:** lumampas ang wick sa pVAH / pVAL pero nag-close pabalik sa loob → fade, target **pPOC**.
  - Parehong VP setup ay:
    - galing lang sa profile ng **kahapon** (walang bar ng ngayong araw);
    - SL lampas sa wick + buffer;
    - kailangan ng **min RR** (`vp_min_rr`, default 1.5);
    - puwedeng salain ng **HTF bias** (`vp_bias_filter`).
  - Sa Stats at sa Replay, may **"Comparison ng setups"** table: forecasts, triggered, TP / SL, win %, avg R,
    total R, profit factor at avg confidence ng bawat setup. Naka-highlight ang pinakamataas sa bawat column.
  - "kulang pa (n/30)" = wala pang 30 triggered ang setup, kaya **huwag pang husgahan**.
  - Forecast lang ito: walang order, walang epekto sa score, sa gate at sa AUTO TRADE.

## STRATEGIES (Trading → STRATEGIES card): Trend · Pullback · Low Volume Grid
Bawat strategy ay may **sariling set ng indicator conditions** (✓ / ✗ live sa huling sarado na bar ng AUTO TF),
sariling **passing %**, RR at SL. 🔒 = required. Naka-OFF lahat sa AUTO hanggang i-check mo ang **AUTO** ng
strategy (may confirm).
| Strategy | Kailan | Ano ang ginagawa |
|---|---|---|
| **TREND** | **2 BOS sa M15 🔒 + 1 BOS sa H4 🔒** sa parehong direksyon (mula sa totoong M15 / H4 bars, kahit anong AUTO TF) | **market entry sa direksyon ng trend** (default 1:2, SL 1.5×ATR) kapag **bago** itong nag-align (hindi bawat bar) |
| **PULLBACK** (on/off) | may trend + RSI/Stoch umabot sa oversold/overbought kontra sa trend + bumabalik 🔒 | market entry sa direksyon ng trend (1:1.5). **Hindi papasok kapag VOLATILE** (ATR percentile ≥ 85) |
| **CONSOLIDATION** | **score 1-2-3: mahinang volume (1) 🔒 + ADX < 20 (2) 🔒 + box (3) 🔒**, tahimik nang min. 4 kandila | **pending orders sa paligid ng box:** BUY STOP sa itaas, SELL STOP sa ibaba, nandoon na bago mag-break. **Na-hit ang isa = cancel ang kabila (OCO)**, kanselado pagkatapos ng `expiry_bars` |
| **NEWS** | high-impact news | NEWS TRADE card (isara ang open trades + BUY/SELL STOP) |
- **Chart:** tig-iisang indicator sa LAYERS: **Strategy · TREND** (`TR▲/▼` + `B15↑/↓` at `H4↑/↓` kung saan nag-BOS),
  **Strategy · PULLBACK** (`PB▲/▼`) at **Strategy · CONSOLIDATION** (box `x/3` + BUY STOP / SELL STOP levels).
- **Market Overview (kanan) → STRATEGIES card:** CONSOLIDATION (x/3), TREND (H4 BOS + bilang ng M15 BOS),
  PULLBACK (Oversold / MFI / Doji / Order block) at NEWS (susunod na news + countdown, NEWS TRADE ON/OFF).
  May **AUTO** badge ang strategy na naka-AUTO (dilaw kapag naka-ON ang AUTO TRADE).
- **Forecast:** setups `Strategy TREND / PULLBACK / LOW VOL GRID` sa comparison.
- **Backtest:** Entry signal → Strategy. Gumagamit ito ng mismong settings mo; level 1 lang ng grid ang sinusukat.
- **AUTO:** dumadaan pa rin sa lahat ng gate (session, news, risk, DD, max trades). Ang TREND / PULLBACK ay
  kailangang kasabay ng HTF bias. Ang grid ay walang direksyon.

## "Bakit wala pang trade?" (Trading → AUTO TRADE card)
Live checklist ng lahat ng kailangan ng AUTO **ngayon**, na nire-refresh kada 10 segundo:
- **Lagay ng AUTO:** ARM, AUTO ON, MASTER, session, news window, trades ngayon at open trades.
- **Kondisyon ng signal:** HTF bias, trigger (M5/M15 CHoCH/BOS), at score kontra sa kailangan.
- **Iba pa:** ang router (kung ON) at ang threshold ng Analyser arrows.
- **Huling arrow ng bawat source** sa AUTO TF (hal. "huling arrow: SELL 29 07:00 · 69% · 20 M15 bar na ang
  nakalipas") at ang **huling skip** kasama ang dahilan.

**Min score % ng AUTO (0 = passing)** ang score threshold ng automatic entries:
- sa **gate** (dati, passing score pa rin ang hinihingi);
- sa **Analyser arrows na tini-trade ng AUTO** (bias + trigger + score ≥ min score).
Hindi nito binabago ang arrows sa chart (⚙ ng Analyser Signals) at ang manual entries.

## PULLBACK = uptrend + oversold + MFI + doji + order block · CONSO = ADX < 20 + low volume (min. 4 kandila)
**PULLBACK** (Trading → STRATEGIES), **4 required (🔒)** na kondisyon:
- **RSI oversold** sa huling 6 bars (≤ `os` sa uptrend / ≥ `ob` sa downtrend);
- **MFI ≤ `mfi_os` (20)** sa uptrend / **≥ `mfi_ob` (80)** sa downtrend sa huling 6 bars;
- **Doji** sa dulo ng dip, sa bar na kasasara o sa nauna rito (body ≤ `doji_body` × range);
- **Order block:**
  - Tumama ang dip sa **bullish OB** sa uptrend (o **bearish OB** sa downtrend) sa huling 6 bars, ± `ob_near` × ATR.
  - Ang OB ay ang huling kontra-kandila bago ang BOS. Hindi na ito binibilang kapag nabasag na.

Kasama ang uptrend → **BUY**, at downtrend → **SELL**. Dagdag sa score ang Stoch, EMA touch, turn, CCI at W%R.

**LOW VOLUME GRID (consolidation)**:
- Ang **ADX < `adx_max` (20)** at ang **mahinang volume** (≤ `vol_pct` percentile) ay dapat **parehong totoo sa
  bawat isa sa huling `quiet_bars` (4) na sarado na kandila**. Saka lang ilalagay ang BUY STOP / SELL STOP grid.
- Makikita sa card ang "tahimik X/4 kandila".

Kung may naka-save ka nang strategy settings, i-check ang 🔒 ng MFI, Doji at Order block sa PULLBACK, tapos
**Save strategies**.

## v2.1.0: TREND = 2 BOS M15 + 1 BOS H4 · CONSOLIDATION = volume + ADX + box (x/3)
- **TREND:** BUY kapag ang huling **H4 BOS** ay pataas (sa huling `h4_window` = 30 H4 kandila) **at** may
  **≥ 2 BOS pataas sa M15** sa huling `bos_window` = 32 M15 kandila (8 oras) at pataas din ang huling M15 BOS.
  Salamin para sa SELL. Ginagamit ang **sarado** lang na M15 / H4 kandila (walang look-ahead).
  Naka-OFF na by default ang EMA / ADX / MACD / Supertrend / HTF; puwede mong i-check ulit para sa score.
- **CONSOLIDATION:** 3 kondisyon, lahat required: **1 volume mahina**, **2 ADX < 20** (pareho nang min. 4 kandila),
  **3 box** (2 tops + 2 bottoms). Ang pending orders ay nasa labas ng **box**, kaya nandoon na bago ito mag-break;
  kapag na-fill ang isa, kina-cancel ang kabila (OCO).
- **Lumang settings:** kapag in-load ang lumang `strategy_settings.json`, pinapalitan ang conditions / required ng
  TREND at CONSOLIDATION ng bagong rules. Hindi ginagalaw ang ibang values mo (RR, lot, AUTO, levels…).

## v2.5.0: STRATEGY PANEL (⚙)
- **I-click ang ⚙** sa tabi ng strategy sa kanan ng chart.
  - **NAKA-ON:** ang setup mo, may live ✓ / ✗. "✕ alisin" para tanggalin ang indicator.
  - **＋ MAGDAGDAG NG INDICATOR:** lahat ng puwedeng idagdag (hal. Stoch, CCI, W%R, EMA, Engulfing, BB band, RSI 50,
    EMA200, MACD, Supertrend, ADX, Squeeze…).
  - **VALUES:** RR, SL, RSI / MFI levels, ADX max, BOS count, OB TF, atbp. → **Save**.
- **Papasok lang kapag LAHAT ng naka-ON ay ✓** (100%).
- Magiging **CUSTOM** ang profile pagkatapos mag-Save. I-click ulit ang SCALP / NORMAL / AGGRESSIVE para ibalik.

## v2.4.1: PULLBACK = order block sa M15 na hindi na-break
- **SELL (downtrend):** umakyat ang presyo → overbought (RSI + MFI) → tumama sa **bearish order block sa taas** →
  **walang M15 close sa itaas ng OB** (hindi na-break) → doji → SELL.
- **BUY (uptrend):** salamin nito: bumaba sa bullish OB sa ilalim na hindi na-break.
- **SL:** nasa labas lang ng order block. Kapag na-break ang OB, mali na ang setup.
- **Setting:** "Order block TF" (default M15) sa Trading → STRATEGIES → PULLBACK → Settings.

## v2.4.0: per strategy lang · lahat green = trade · CONSO = ADX < 20
- **Wala na ang Confluence score.** Ang AUTO ay pumapasok lang sa strategy na naka-AUTO ("STRATEGIES LANG" sa
  Trading → AUTO TRADE settings; i-OFF lang kung gusto mo ulit ang analyser arrows).
- **Pagpili ng strategy:** sa itaas ng chart, **AUTO: ○ TREND ○ PULLBACK ○ CONSO**. I-click para maging ● (naka-AUTO).
  Puwede rin ang ○ MANUAL / ● AUTO sa tabi ng strategy sa kanan.
- **Kapag lahat green** ang naka-AUTO na strategy sa huling sarado na bar:
  - Papasok ang AUTO sa loob ng ~15 s, sa kasalukuyang presyo, kahit green na ito bago mo in-ON ang AUTO.
  - Isang trade lang bawat READY; uulit lang kapag nawala at bumalik ang READY.
- **CONSOLIDATION:**
  - Kapag **ADX < 20**, ilalagay agad ang BUY STOP / SELL STOP: sa box kung meron, kung wala, sa huling 12 bars.
  - Na-hit ang isa = cancel ang kabila (OCO).

## v2.3.0: strip sa itaas ng chart · 4 strategy lang sa kanan
- **Sa itaas ng chart:**
  - **✋ MANUAL / ⚡ AUTO** at **ARMED / DISARMED**, parehong may confirm.
  - **SCALP / NORMAL / AGGRESSIVE**.
  - **BUY / SELL ▾** para sa manual ticket.
- **Sa kanan:** ang 4 strategy lang, walang scroll. Puwedeng ibalik ang ibang card sa ⚙ LAYOUT → "Ipakita lahat".
- **MANUAL / AUTO badge ng bawat strategy:** i-click para i-AUTO o ibalik sa MANUAL. Ang MANUAL na strategy ay
  hindi kailanman ite-trade ng AUTO, kahit READY.
- **Kailan papasok ang AUTO:**
  - Sa pagsara ng bar kung saan **unang** nag-READY ang naka-AUTO na strategy.
  - Ang READY na kanina pa ay hindi na papasukan (walang late entry). Nakasulat ito sa ilalim ng strategy.

## v2.2.0: 100% bawat strategy · NORMAL / AGGRESSIVE / SCALP
- **100%:** papasok lang ang strategy kapag **lahat** ng naka-check na kondisyon ay tama. Wala nang "passing %".
- **PROFILE** (Trading → STRATEGIES, isang click, may confirm, hindi nito ino-ON ang AUTO):

  | Profile | AUTO TF | TREND | PULLBACK | CONSOLIDATION | HTF bias ng analyser |
  |---|---|---|---|---|---|
  | **NORMAL** | M15 | 2 BOS M15 + 1 BOS H4, 1:2 | oversold + MFI + doji + OB, 1:1.5 | volume + ADX<20 + box, tahimik 4, 1:2 | kailangan |
  | **AGGRESSIVE** | M15 | 1 BOS M15 + 1 BOS H4, 1:1.5 | oversold + MFI + OB (walang doji), 1:1.5 | volume + ADX<25 + box, tahimik 3, 1:1.5 | hindi (sariling trend) |
  | **SCALP** | M5 | 2 BOS M5 + 1 BOS H1, SL 1×ATR, 1:1 | 4 kondisyon, 1:1 | tahimik 3, 1 level, max SL $4, 1:1 | hindi (sariling trend) |
- Kapag binago mo ang numbers, magiging **CUSTOM** ang profile.
- **"Kailangan ding kasabay ang HTF bias ng analyser":** kapag OFF, ang sariling trend ng strategy (hal. H4 BOS) ang
  susundin, kahit iba ang HTF bias ng analyser. Dadaan pa rin sa risk, session, news, max trades at ARM.

## v2.1.1: ⚙ LAYOUT at "malapit ma-hit"
- **⚙ LAYOUT** (sa itaas, katabi ng REC): i-check / i-uncheck ang cards na gusto mong makita sa Market Overview,
  at ↑↓ para sa pagkakasunod sa kanan. "STRATEGIES + Trade lang" = malinis na view. Naka-save sa browser.
- **STRATEGIES card (una sa kanan):**
  - Sa itaas: AUTO TRADE ON/OFF · ARMED/DISARMED · LIVE/PAPER.
  - Bawat strategy: AUTO o MANUAL badge, progress bar, **MALAPIT** (≥ 60%) o **READY**, at kung saan / gaano
    kalayo ang presyong kukumpleto rito (hal. "M15 BOS #2: close above 4185.20 · $3.40 ang layo").
- **Bug fix:** hindi na umaakyat ang buong page (pati sidebar) pagkatapos mag-click ng signal. Puwede nang i-scroll
  ang sidebar sa maliit na screen.

## Trading menu, MANUAL / AUTO card at mga konektadong account
- **Trading** (sidebar, pangalawa sa itaas): iisang page para sa lahat ng automatic trading.
  - Sa itaas ay malalaking card: **AUTO TRADE**, **NEWS TRADE** at **EXIT GUARD**, kasama ang ON/OFF, status at log.
  - Sa ilalim ang **AUTO TRADE settings** at **NEWS TRADE settings**. Ang "⚙ SETTINGS →" ay dadalhin ka mismo sa
    settings nito.
  - May dilaw na tuldok ang menu kapag naka-ON ang AUTO.
- **Market Overview (kanan)**, malinis na:
  - **MANUAL / AUTO TRADE** card kasama ang ARMED / LIVE / PAPER / NEWS chips at ang "⚡ Trading →" button;
  - **lahat ng account** (master at followers: 🟢 konektado · 🔴 hindi · 🟠 locked · ◯ disabled, kasama ang
    equity, lot at P/L);
  - ang manual **Trade** box, open trades, verdict at regime.
- **Babala sa lumang server:** pulang banner kapag hindi pa na-restart ang terminal pagkatapos mag-update.

## NEWS TRADE (auto): isara ang open trades, tapos BUY STOP / SELL STOP bago ang news
Nasa **News trade** card (Market Overview → kanang column). **Laging OFF pagbukas**, kailangan ng **ARM + confirm**,
at kusang **mag-o-OFF kapag nag-DISARM** (kakanselahin din ang lahat ng news pending orders).
Bago ang bawat high-impact news (currencies at impact mula sa News settings):
1. **`close_before_sec` (default 180s) bago:** isasara ang **lahat ng open SuperChart trade** sa master at followers.
   Hindi ginagalaw ang trades ng ibang EA o manual sa MT5. Puwedeng i-off (`close_before_news`).
2. **`place_before_sec` (default 120s) bago:** maglalagay ng **BUY STOP** sa itaas at **SELL STOP** sa ibaba.
   - Kapag may **consolidation box**, sa labas ng box (+ buffer); SL = kabilang panig ng box (max SL).
   - Kapag **walang box** (`nobox_mode = distance`, default), sa **presyo ± $nobox_offset** (default $3) at SL
     `nobox_sl` (default $5). Ang `skip` = walang trade kapag walang box.
   - TP = RR (default 1:2) o fixed $.
3. **OCO:** kapag na-trigger ang isa, kakanselahin ang kabila. Ang hindi na-trigger ay kakanselahin
   `cancel_after_min` (default 10) min pagkatapos ng news.
- **Safety:** sinusunod ang **MASTER TRADING** at ang **risk limits** (daily DD / target lock, total DD, risk per
  trade, max trades per day). Kapag bawal, hindi maglalagay ng order at makikita ang dahilan sa card.
- **AUTO TRADE:** hindi papasok ng bagong trade sa loob ng news window (`news_block`).

## 2 SCREEN at pagpili ng AUTO TRADE timeframe
- **⧉ 2 SCREEN** (itaas ng chart): pangalawang chart sa tabi ng una, may sariling TF (hal. H1 + M5).
  - Kasama ang candles, mga indicator sa price chart, levels at arrows.
  - Ang mga panel (RSI, MFI…) ay nasa unang chart lang.
  - Naaalala ang huling pinili.
- **⚡ AUTO → TF:** sa alinmang chart, pindutin para gawing **AUTO TRADE timeframe** ang TF ng chart na iyon (M1–H1).
  - May kumpirmasyon; kapag naka-ON ang AUTO, magsisimula ito sa susunod na sarado na bar.
  - May ⚡ sa TF button na ginagamit ng AUTO ngayon.
  - Pareho pa rin ang lahat ng filter at gate.
- **↗ (pop-out):** bubukas ang chart sa bagong window (`/?tf=H1`) na puwedeng ilipat sa pangalawang monitor.
  Sariling TF ang window na iyon at hindi nito binabago ang TF ng pangunahing window.

## Regime router: trend → 1:2 · walang trend + arrow → 1:1 (AUTO TRADE)
**AUTO TRADE → ⚙ → Regime router → ON.** Ang regime (Regime settings, hal. M15) ang pumipili kung aling signal ang
papasok at kung anong RR:
| Regime | Signal na papasok | RR (TP = RR × SL ng signal) |
|---|---|---|
| TREND UP / TREND DOWN | trend signal lang (Analyser; pati Breakout / Trend line kung naka-check), **sa direksyon lang ng trend** | **1:2** |
| RANGE | **reversal arrow lang** (BUY sa bottom, SELL sa top) | **1:1** |
| VOLATILE / QUIET / TRANSITION | **walang trade** | - |
- **Settings:** puwedeng baguhin ang RR at kung aling regime ang "trend" o "range".
- **Arrows sa range:** sinusunod pa rin ang R✓ lang at min reversal score. Sa range, puwede sa dalawang direksyon ang
  arrow, pero **hindi kontra sa malinaw na HTF bias** (puwedeng i-allow).
- **Gates:** dumadaan pa rin sa lahat ng gate (session, news, spread, risk, DD, max trades, walang kontrang trade,
  duplicate, ARM). Kapag may `min RR` ka sa Risk settings na lampas 1, haharangin ang 1:1 na range trades (`MIN_RR`).
- **Kapag OFF ang router:** gumagana ang AUTO TRADE gaya ng dati.
- **Backtest → Entry signal → Regime router:** ginagamit ang AUTO TRADE settings mo, kaya ito mismo ang gagawin ng AUTO.
- **Forecast:** may bagong setup na `ROUTER` sa comparison.

## Trade sa arrow: Reversal (R? / R✓)
Nakikita ang mga arrow sa chart bilang `R✓65 PDH ENG` / `R?70 SWH DIV`. Ang **R✓** ay kumpirmado (may close lampas sa
reversal bar); ang **R?** ay posible pa lang. Ang numero ay ang reversal score.
- **AUTO TRADE → ⚙ → Signals → `reversal`** (naka-OFF sa simula, ikaw ang magche-check):
  - **R✓ lang** (default): papasok sa pagsara ng bar na **kumumpirma** sa reversal. Puwedeng isama ang R?, pero mas
    maaga at mas mapanganib iyon.
  - **Min reversal score** (default 60).
  - **Kumpirmasyon:** `HTF bias lang` (default) o `HTF bias + pasadong score`.
  - Kailangang **kasabay ng HTF bias** ang arrow. Hal. top arrow sa bearish market = dulo ng pullback. Hindi kailanman
    tini-trade ang kontra-trend na arrow.
  - Sa `HTF bias lang`, ang reversal score ang pumapalit sa analyser score. Ang HTF bias ay hindi kailanman nilalaktawan.
  - Dumadaan pa rin sa lahat ng gate: session, news, spread, risk, DD, max trades, walang kontrang trade, duplicate, ARM.
  - SL / TP: sa signal ng arrow (lampas sa wick / RR ng plugin).
- **Forecast comparison:** bagong setups na `Arrow R✓ (kumpirmado)` at `Arrow R?/R✓ (lahat)`.
- **Backtest → Entry signal:** `Reversal arrow R✓` o `R? + R✓`, may min score at HTF bias filter. Pareho ang detector
  ng nasa chart, at walang silip sa hinaharap (may test).
- **Payo:** i-backtest muna sa totoong MT5 history at panoorin sa forecast bago i-ON sa AUTO, lalo na sa live account.

## Naka-ON na indicators sa ilalim ng chart
Sa ilalim ng main chart ay may linya ng lahat ng naka-ON na indicator.
- **Bawat indicator:** pangalan, TF, boto ngayon (▲ / ▼ / •), at `C` = chart / `S` = score.
- **Kulay:** 🟢 sang-ayon sa bias · 🔴 kontra · ⚪ neutral / walang boto · ◯ chart lang.
- **Detalye:** i-hover para makita ang dahilan ng boto.
- **Strategies:** kasama rin ang mga naka-ON na strategy. Pareho ito sa `/m` companion view.

## Companion view para sa cellphone (`/m`, read-only)
Buksan ang `http://127.0.0.1:<port>/m`, o i-click ang "📱 Companion view" sa sidebar. Pang-cellphone ang layout.
- **Status:** MASTER, ARMED / DISARMED, PAPER / LIVE, at AUTO ON / OFF.
- **Score:** ang score, ang pasado, ang bias, ang trigger, at ang trend ng H4 / H1 / M15 / M5.
- **Aktibong indicators:** lahat ng naka-CHART at / o naka-SCORE, kasama ang TF at ang boto ngayon.
  - 🟢 sang-ayon · 🔴 kontra · ⚪ neutral · 🟡 halo · ◯ chart lang
  - Kasama ang P/D at liquidity modules at ang mga strategy na ON.
  - May filter: Lahat / SCORE / CHART lang.
- **Iba pang laman:** bawas sa score, open trades (master), forecasts, huling sagot ni Claude, at mga event.
- **Read-only:** walang button na nag-o-order o nagbabago ng setting. Naka-redact ang tokens at keys.
- **Paano buksan mula sa cellphone:** naka-localhost pa rin ang terminal, kaya gumamit ng private tunnel (hal.
  Tailscale: `tailscale serve <port>`, o SSH tunnel). Huwag i-bind sa public IP.
- **Cloud app:** ang `/api/companion` ang magiging data ng cloud companion app kung itutuloy natin iyon.

## Score: bakit ganito ang score? (⚙ SCORING + "BAKIT X% ANG SCORE?")
Sa ilalim ng SCORE BREAKDOWN, nakalista ang bawat item na **hindi sang-ayon** sa bias at **ilang puntos ang bawas**
nito sa OVERALL. Eksakto ito: kapag pinagsama ang lahat ng bawas, katumbas iyon ng 100 − score.
Karaniwang dahilan ng mababang score kahit kaunti lang ang naka-ON:
- **Laging kasama ang Structure** (HTF bias, H1 aligned, M15 aligned, trigger). Kapag wala pang trigger o M15 ay
  nagpu-pullback, 50–75% lang ang grupong ito.
- **Iisang indicator sa isang grupo:** 0% o 100% agad ang grupo. Sa apat na indicator na magkakaiba ang grupo, isang
  neutral o kontrang vote = buong grupo ang nawawala (hal. 1 sa 4 grupo = −20 hanggang −25 puntos).
- **Neutral = hindi sang-ayon** (dati). Hal. Volume Profile sa loob ng value area, RSI sa gitna.

Dalawang bagong option sa ⚙ SCORING (default = dati, kaya walang nagbago hangga't hindi mo pinapalitan):
- **Neutral na vote:** `bilang na hindi sang-ayon` (dati) o `hindi bilang` (abstain; ang grupong puro neutral ay
  hindi kasama).
- **Paano pagsamahin:** `bawat grupo` (dati) o `bawat indicator` (bawat item ay isang boto, may timbang ng grupo niya).
- Makikita mo agad ang score sa **apat na kombinasyon** bago mo palitan ("Kung ibang scoring option").
- Pareho ang scoring na gamit ng live score, AUTO TRADE, backtest at forecast. Kapag pinalitan mo ito, i-backtest
  muna bago gamitin sa auto.

## AI Analyst: Claude Fable (bagong page, read-only)
- **Tatlong button:**
  - **Basahin ang chart ngayon:** bias, key levels, LONG / SHORT / WAIT scenario, invalidation at target.
  - **Suriin ang score ko:** alin ang humihila pababa, totoong conflict ba o epekto lang ng scoring, at alin sa
    mga option ang bagay sa market ngayon.
  - **Sariling tanong.**
- **Ano ang ipinapadala:** snapshot ng alam na ng terminal. Kasama ang presyo, score at bawat grupo / item, ang
  "lost" points, ang alt scores, bawat TF (trend, swings, huling BOS / CHoCH), SMC, regime, pPOC / pVAH / pVAL,
  active forecasts, news state, at huling sarado na candles ng H4 / H1 / M15 / M5.
- **Model:** `claude-fable-5-1` (default). Puwede ring Opus / Sonnet / Haiku. Ang sagot ay nasa Taglish, English o
  Filipino, at may history.
- **API key:** kukunin sa console.anthropic.com at ilalagay sa AI settings.
  - Nasa `ai_settings.json` sa PC mo lang.
  - Hindi ito ibinabalik ng server sa browser (makikita lang ang "✓ …abcd").
  - Naka-redact ito sa error log.
- **Bayad:** may bayad ayon sa tokens ang bawat pindot. May **cost guard**: isang request lang bawat N segundo
  (default 15). Walang auto / timer: tatawag lang ito kapag pinindot mo.
- **Kaligtasan:** hindi ito nagte-trade, hindi nag-a-ARM, at hindi binabago ang score, plugins, settings o AUTO
  TRADE. Kahit "BUY NOW" ang isagot, text lang iyon (may test para dito).
- **Internet:** kailangan ng PC / VPS na makaabot sa `api.anthropic.com`.

## Master improvement (bagong layer: SMC engine, setup quality, master gate, position manager)
Hindi binago ang dating score: ito pa rin ang "score" condition. Ang mga bago ay nakapatong dito.

**MASTER TRADING (sidebar: `MASTER: ENABLED/DISABLED`)**
- Kapag DISABLED, walang kahit anong bagong trade (manual, auto, news box). Naka-save ito, kaya OFF pa rin pagka-restart.
- Kapag sira ang `master_switch.json` o `risk_settings.json`, **bawal mag-trade** hanggang ayusin o i-save ulit sa UI (fail safe).

**Master gate para sa AUTO TRADE (Command Center → Master gate)**
- Ang signal ay hindi pa trade. Bawat auto signal ay dumadaan sa 15 gate, sunod-sunod: DATA → MASTER → SESSION →
  NEWS → BIAS → STRUCTURE → SMC → LOCATION → CONFIRM → CONFLICT → RISK → EXECUTION → DUPLICATE → AUTO → ARM.
- Kapag may bumagsak, walang order at may malinaw na code, hal. `BLOCKED_SPREAD`, `BLOCKED_NEWS`,
  `BLOCKED_DD_LIMIT`, `BLOCKED_OPPOSITE_POSITION`, `BLOCKED_DUPLICATE_SIGNAL`.
- May sariling **signal_id** ang bawat signal (hash ng symbol, TF, oras, direksyon, event, uri), kaya hindi ito
  mauulit kahit mag-restart. Naka-link ito sa **setup_id** (SMC) at sa **trade_id** (gid).
- Lahat ng signal, pasado man o hindi, ay naka-save sa `signals_paper.jsonl` / `signals.jsonl`.

**SMC engine (Command Center → SMC setup)**
- Isang kadena, hindi apat na hiwalay na puntos: **sweep → BOS/CHoCH → FVG/OB → retest**.
- May quality (LOW / MEDIUM / HIGH), confidence 0–1, entry zone at invalidation.
- FVG: FRESH / PARTIALLY_FILLED / MITIGATED / INVALIDATED, may fill %. Walang dobleng FVG sa parehong zone.
- OB: isa lang bawat BOS/CHoCH.
- Sweep: wick lampas + close pabalik. Kapag nag-close sa labas, **breakout** iyon, hindi sweep.
- Premium/Discount: galing sa malinaw na dealing range (piliin: `htf_swing`, `prev_day`, `session`, `custom`).
- Sarado na bars lang ang ginagamit, kaya pareho ang live at backtest.

**Setup quality at decision rules**
- Bawat kategorya (STRUCTURE, SMC, TREND, MOMENTUM, LOCATION, VOLUME…) ay nagbibigay ng isang value lang (0–1).
  Hindi kayang talunin ng 10 magkakamukhang trend indicator ang structure.
- Default na timbang: STRUCTURE 25, SMC 25, TREND 20, MOMENTUM 15, LOCATION 10, VOLUME 5. Puwedeng baguhin.
- **Conflict:** ipinapakita, hindi ina-average. Default `structure_priority`: harang lang kapag structure/SMC ang
  kontra; babala lang ang momentum.
- **Regime:** binabago lang ang requirement, hindi ito entry. Hal. HIGH VOLATILITY = +0.10 confidence at auto lot
  x0.5; counter-trend sa TRENDING = +0.15.
- **Ikaw ang pipili kung alin ang REQUIRED.** Default: score + HTF bias + trigger, kapareho ng dati.

**Risk engine (Command Center → Risk engine)**: max risk bawat trade (% at $), max total DD mula sa peak, max
exposure, max trades/day at max open (lahat ng uri), TP required, min RR. Ang **news unknown** (hindi ma-load ang
calendar) ay hindi na itinuturing na CLEAR; default: block ang AUTO.

**Position manager (naka-OFF sa umpisa)**
- Break-even (R o $), profit lock steps, trailing (ATR / step / structure) at partial close.
- Iisang controller lang ang gumagalaw ng SL, may priority: emergency → broker safety → BE → profit lock → trailing.
- Hindi kailanman niluluwagan ang SL. Pati ang BE button ay hindi na nagluluwag.

**Iba pa**
- Data validation bago ang bawat decision (OHLC, oras, tick, spread, symbol).
- Error log na naka-kategorya (System page), at nakatago ang mga token at password.
- Plugin: hindi ito makapag-order (bawal ang import ng trading / mt5_ops / order_send). Kapag nag-crash, bumagal
  (3x) o mali ang sagot, UNAVAILABLE lang ang plugin na iyon.
- Backtest: may SMC filter (parehong engine) at report na **signals generated / blocked / executed**, expectancy,
  losing streak, worst day at daily target hit rate.

## Exit guard (card sa Market Overview, sa ilalim ng AUTO TRADE)
Kapag may bukas kang trade at **bumaliktad ang trend bago tumama ang TP o SL**, ito ang kumikilos.
- **Default:** kapag may **TREND CHANGE kontra sa trade** sa M15 (trend line break na nag-close sa labas + CHoCH),
  **isasara ang buong trade** sa master at sa lahat ng follower.
- Puwedeng palitan ang trigger: TL✓ break, na mas maaga.
- Puwedeng palitan ang aksyon: partial close, SL → breakeven (kapag nasa profit lang), o alert lang.
- Puwedeng piliin kung aling trades ang babantayan: lahat, auto lang, o manual lang.

**Kaligtasan:**
- **Gumagana lang kapag naka-ARM.** Kapag DISARMED, alert lang. Laging OFF ang ARM pagbukas ng app.
- Ang binibilang lang ay mga trend change **pagkatapos mong pumasok**, at mga bago lang (huling 3 bar). Hinding-hindi
  ito kikilos sa lumang senyales.
- Hindi kailangang naka-ON sa LAYERS ang Trend Line Breakout; sariling kalkulasyon ang gamit ng Exit guard.

Makikita sa journal bilang **"Exit guard: TREND CHANGE DOWN/UP"**, at may Telegram alert (🛡 EXIT GUARD).

## Trend Line Breakout (LAYERS → STRUCTURE)
Awtomatikong gumuguhit ng trend line:
- **Falling resistance** (pink): dumadaan sa mga swing high na pababa nang pababa, kaya **BUY breakout** kapag nagsara
  ang presyo sa ibabaw.
- **Rising support** (berde): dumadaan sa mga swing low na pataas nang pataas, kaya **SELL breakout** kapag nagsara sa
  ilalim.
- Valid lang ang linya kapag walang bar na nag-CLOSE lampas dito. Kapag may bagong swing, ang pinakabagong valid na
  linya ang ginagamit.

**Breakout** = close lampas sa linya ng hindi bababa sa 5 pips (o 0.1×ATR).

**Confirmation**, may puntos ang bawat isa:

| Confirmation | Puntos |
|---|---|
| 2 touches / 3 o higit pang touches ng linya | 20 / 30 |
| Malakas ang breakout candle (body ≥ 50%) | 20 |
| Volume ≥ 1.5× average | 15 |
| Follow-through: mas lumayo ang close ng sumunod na bar | 15 |
| **Retest held**: bumalik sa linya pero nagsara sa tamang panig | 20 |

- **TL✓** = umabot sa 55 puntos, confirmed.
- **TL?** = hinihintay pa ang confirmation.
- **TL✗** = FAILED: nagsara pabalik sa kabilang panig ng linya. Madalas itong bitag (fakeout).
- **RT** = may retest.

**Breakout = CLOSE sa labas ng linya.** Hindi binibilang ang wick na tumusok lang: madalas liquidity sweep lang iyon.

**🔄 TREND CHANGE** (label na **TREND▲ / TREND▼**, dilaw) lumalabas kapag:
1. may confirmed na trend line break (TL✓, close sa labas);
2. sa loob ng 24 bar, may **CHoCH** sa parehong direksyon: close sa ibabaw ng huling swing high (pagkatapos mabasag
   ang falling resistance), o sa ilalim ng huling swing low (pagkatapos mabasag ang rising support);
3. at hindi pa nagsara pabalik sa kabilang panig ng linya.

Kung may higher low (o lower high) na nabuo pagkatapos ng break, isinasama rin ito bilang dagdag na patunay.
Boboto ito sa STRUCTURE bilang "TREND CHANGE UP/DOWN" at may sariling Telegram alert (source: Trend Line Breakout).

**Gamit sa terminal:**
1. **Score, grupong STRUCTURE**: kapag may bagong **TL✓** sa huling 6 bar ng M15, boboto ito sa direksyon ng
   breakout.
   - Kasabay ng HTF bias: tataas ang score.
   - Salungat sa bias: bababa ang score.
   - **TL✗** (failed breakout): boboto sa kabaligtaran, dahil bitag ito.
   - Kapag walang bagong breakout, walang boto.
2. **Sariling signal**: may entry, SL at TP (2R) ang bawat breakout, kasama ang resulta.
   - Sa ⚙, `entry = break` (sa breakout close) o `retest` (sa retest, mas ligtas).
   - `confirmed_only` = papasok lang sa bar kung kailan na-confirm (walang silip sa hinaharap).
   - `sl_mode = swing` o `line`.
3. **Auto trade** (⚙ → Signals: trendline) at **Telegram** (signal sources: Trend Line Breakout), kung gusto mo.

## Reversal indicator (LAYERS → REVERSAL)
Hinahanap nito ang mga senyales na **patapos na ang galaw at baka bumaliktad**. Nagbibigay ito ng puntos sa bawat
saradong bar:

**Liquidity sweep**: nilampasan ng wick ang liquidity pero nagsara pabalik sa loob. Ang pinakamalakas na sweep ang
binibilang, +5 bawat dagdag na sweep, max 40.

| Code sa chart | Sweep | Puntos |
|---|---|---|
| EQH / EQL | equal highs / lows (2 o higit pang swing sa halos parehong presyo, dito nakaipon ang stop) | 35 |
| PDH / PDL | high o low kahapon | 30 |
| ASH / ASL | high o low ng Asia session (00-07 UTC = 8am-3pm Manila), na-sweep sa London o NY | 30 |
| SWH / SWL | huling swing high o low na buo pa | 25 |
| RGH / RGL | high o low ng huling 20 bar | 25 |
| FB | failed breakout: nagsara sa labas ang nakaraang bar, bumalik sa loob ang bar na ito | 25 |

**Candle pattern**: ang pinakamalakas ang binibilang, +5 bawat dagdag, max 30.

| Code | Pattern | Puntos |
|---|---|---|
| ES / MS | evening star / morning star | 25 |
| 3O | three outside down / up | 25 |
| ENG | engulfing | 20 |
| PIN | shooting star / hammer (pin bar) | 20 |
| DC / PL | dark cloud cover / piercing line | 15 |
| TWZ | tweezer top / bottom | 15 |
| HAR | harami | 10 |
| DOJ | doji | 5 |

**Iba pang senyales:**
- **DIV**: RSI divergence, +20.
- **Tig-+10 bawat isa**: RSI overbought o oversold, malayo sa EMA20, climax volume, nasa order block.

**Kailan lalabas:** kapag **≥ 60 puntos** at may trigger (sweep, candle pattern maliban sa doji, o divergence).

**Confirmation:**
- **R?** = posible pa lang.
- **R✓** = kumpirmado sa loob ng 3 bar, sa isa sa dalawang paraan:
  - **CHoCH close**: may close na lumampas sa kabilang dulo ng reversal bar.
  - **Confirmation candle**: malakas ang susunod na bar sa bagong direksyon at nagsara lampas sa body ng reversal bar.

**Halimbawa ng label:** `R✓65 RGH DC SL` = kumpirmado, 65 puntos, na-sweep ang range high, dark cloud cover, at SL
ang naging resulta. Nasa signal panel ang buong paliwanag.

Gamit sa terminal:
1. **Score (grupo "Reversal")**: kapag may bagong reversal sa huling 3 bar ng M15, boboto ito.
   - Kung **salungat sa HTF bias**, bababa ang score (babala).
   - Kung **kasabay ng bias** (tapos na ang pullback), tataas ang score.
   - Kapag walang reversal, walang boto, kaya hindi ito nakakaapekto.
2. **⚠ Caution**: may babala ang BUY signal kapag may bagong reversal TOP, at ang SELL kapag may bagong BOTTOM.
   Kasama ito sa bilang ng "Max ⚠ cautions" ng Auto trade.
3. **Telegram**: sa System & Alerts, i-check ang "Reversal (top/bottom)" sa signal sources.

Payo: gamitin itong **babala o pang-exit**, o pang-entry lang kapag **R✓ at kasabay ng HTF bias**. Sa malakas na
trend, maraming bottom na hindi tumatama. May `confirmed_only` sa ⚙ kung kumpirmado lang ang gusto mong makita.

## System & Alerts (bagong page)
### Telegram alerts
Para malaman mo sa phone ang nangyayari sa terminal kahit nasa VPS ito.
1. Sa Telegram, hanapin ang **@BotFather**, i-type ang `/newbot`, bigyan ng pangalan, tapos kopyahin ang **token**.
2. Sa terminal (System & Alerts, Telegram card), i-paste ang token at pindutin ang **Save**. Awtomatikong mag-o-ON
   ang alerts.
3. Buksan ang bot mo sa Telegram at pindutin ang **START** (o mag-type ng "hi"). Sa loob ng mga 10 segundo, kusang
   makikita ng terminal ang chat id mo at magpapadala ito ng "konektado na". Tapos na.

Pagkatapos nito, **awtomatiko nang ipinapadala** ang bawat trigger. Wala ka nang pipindutin.

Ang ipinapadala (puwedeng i-OFF isa-isa):
- **Signals**: bagong Analyser Signals o Conso Breakout arrow sa pagsara ng bar, kasama ang entry, SL, TP, score,
  sang-ayon at salungat, ⚠ caution at regime. Signal lang ito, walang order na ipinapadala.
- **Trades**: bawat open at close, may kasamang larawan ng chart.
- **Daily DD/TP lock**, **high-impact news**, **health**, **drift** at **daily report**.
- May "walang tunog" na oras (halimbawa 0-6) para hindi ka magising.
- Naka-save lang ang token sa `telegram_settings.json` sa PC o VPS mo. Huwag itong ibigay kaninuman.

### Health watchdog
Bawat 5 segundo, sinusuri nito:
- kung may dumarating pang presyo;
- kung konektado ang master MT5 sa broker at naka-ON ang **Algo Trading**;
- kung buhay ang bawat follower at naka-ON ang Algo Trading nito;
- kung hindi naka-freeze ang terminal.

Kapag may problema nang 10 segundo, lalabas ito sa event log at sa Telegram, at may abiso rin kapag bumalik na sa
normal. Kusang nire-reconnect ang follower na naputol. Walang alert kapag sarado ang gold market (weekend at ang
daily break).

### Drift monitor (live kumpara sa backtest)
1. Sa **Backtest**, patakbuhin ang setup mo o i-**Test** ang isang row, tapos pindutin ang **Set as drift baseline**.
2. Mula noon, ikukumpara ng monitor ang live sa backtest: sunod-sunod na talo, drawdown, average R (statistical
   test) at win rate.
   - Puwedeng **Signals sa chart**: gumagana kahit walang trade, kaya bagay sa testing.
   - O **Journal trades** (auto).
3. Kapag **DRIFT** (mas mahina nang malinaw kaysa backtest), may Telegram alert, at kung naka-check, **awtomatikong
   i-OFF ang AUTO TRADE**. Hinding-hindi ito nag-o-ON ng kahit ano.

### Daily report
Sa oras na pinili mo (default 23:00 Manila), ipapadala ito sa Telegram. Kasama ang:
- trades ng araw, P/L, R, at pinakamaganda at pinakapangit na trade;
- equity at day P/L ng bawat account;
- regime, drift at health;
- high-impact news sa susunod na 24 oras;
- larawan ng chart ng araw.

Naka-save din ito sa `reports/`. May **Preview** at **Send now** sa System page.

## Market regime (card sa Market Overview)
Sinasabi nito kung anong klaseng market ngayon, gamit ang ADX, Choppiness, ATR percentile at EMA20/50:
- **TREND UP / DOWN**: sumabay sa trend.
- **RANGE**: bumili sa ibaba, magbenta sa taas.
- **VOLATILE**: liitan ang lot o maghintay.
- **QUIET**: abangan ang box breakout.
- **TRANSITION**: maging mapili.

Ipinapakita rin kung sang-ayon ang mas mataas na TF. Sa **AUTO TRADE ⚙**, puwedeng limitahan ang auto trade sa mga
regime na gusto mo, at puwedeng ipagbawal ang kontra sa trend. Naka-record sa journal ang regime ng bawat trade.

## Journal: heatmap, regime at larawan ng trade
- **Heatmap oras × araw** (Manila time): P/L, win % o dami ng trades bawat oras. Makikita kung anong oras ka
  kumikita at kung anong oras ka natatalo.
- **Ayon sa market regime**: resulta bawat regime.
- **📷**: larawan ng chart sa entry at sa exit ng bawat trade, naka-save sa `snapshots/`. Kailangan ang `pillow`
  (kasama na sa requirements.txt).

## Logo (web/brand/)
- `mark.svg` / `mark-512.png`: icon lang (sidebar at browser tab)
- `logo.svg` / `logo-dark.png`: buong logo para sa dark na background · `logo-light.svg` / `logo-light.png`: para sa puti
- `reygun.ico`: para sa Windows shortcut (right click sa shortcut > Properties > Change Icon)
