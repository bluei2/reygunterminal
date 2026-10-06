"""
REYGUN TERM plugin API.

Maglagay ng .py file sa plugins/ folder. Auto-load ito pag-start.
Files na nagsisimula sa "_" (halimbawa _template.py) ay hindi nilo-load.

Dalawang klase ng plugin:

  Indicator  -> nagdo-drawing sa chart (lines, levels, markers, zones)
  Strategy   -> nagbabasa ng market at nagbibigay ng Signal (alert lang, WALANG trade)

Press "p" sa terminal para i-on/off ang bawat plugin. Naka-save ang pinili mo sa plugins_enabled.json.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from engine import Bar, Read, TFState, Tick


# ---------------------------------------------------------------- drawing
@dataclass
class Line:
    values: list            # one value per bar (same length as the bars you got), None = gap
    color: str = "#ffff55"
    label: str = ""
    char: str = "•"
    pane: str = ""          # "" = on the price chart; any name (e.g. "RSI") = own panel below (SuperChart)
    style: str = "line"     # "line" | "dots" | "step" | "dashed"
    width: int = 2
    autoscale: bool = True  # False = this line never stretches the price scale (far bands)
    axis: bool = True       # False = no price-axis label and no legend entry (e.g. old broken trend lines)


@dataclass
class Level:
    price: float
    color: str = "#ff55ff"
    label: str = ""          # max 3-4 chars, shown on the price axis
    pane: str = ""           # "" = price chart, or the pane name of a Line/Hist (e.g. RSI 70/30 lines)


@dataclass
class Marker:
    t: int                   # bar time (Bar.t)
    pos: str = "above"       # "above" the high or "below" the low
    char: str = "▲"
    style: str = "bold #ffff55"


@dataclass
class Zone:
    lo: float
    hi: float
    t_from: int = 0          # draw from this bar time ...
    bg: str = "#004400"      # background color
    t_to: Optional[int] = None   # ... to this bar time (None = to the right edge)
    label: str = ""          # optional text drawn at the box's top-left (SuperChart)
    border: str = ""         # optional border color (SuperChart)


@dataclass
class VLine:
    t: int                   # bar time where the vertical line goes
    color: str = "#8a9bb8"
    label: str = ""          # optional short text (SuperChart)
    dashed: bool = True


@dataclass
class Hist:
    """Histogram in its own pane (SuperChart), e.g. volume, MACD histogram."""
    values: list            # one value per bar, None = gap
    colors: object = "#60a5fa"   # one color for all, or a list with one color per bar
    label: str = ""
    pane: str = "Volume"


@dataclass
class Profile:
    """Horizontal histogram on the right side of the price chart (volume profile)."""
    rows: list              # [(price_lo, price_hi, volume), ...]
    color: str = "#60a5fa"
    highlight: list = field(default_factory=list)   # row indexes to draw brighter (POC / value area)
    highlight_color: str = "#fbbf24"
    width_pct: float = 0.22                          # max width as a share of the chart width (or of its time span)
    t_from: Optional[int] = None                     # optional: draw inside [t_from, t_to] (bar times), e.g. one day;
    t_to: Optional[int] = None                       # None = on the right edge of the chart
    buy: Optional[list] = None                       # optional: BUY volume per row -> drawn split buy / sell with
                                                     # the delta of each row (delta profile); sell = row - buy
    sell_color: str = "#ef4444"
    title: str = ""                                  # optional text drawn at the top of the profile


@dataclass
class Overlay:
    lines: list[Line] = field(default_factory=list)
    levels: list[Level] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    vlines: list[VLine] = field(default_factory=list)
    hists: list[Hist] = field(default_factory=list)
    profiles: list[Profile] = field(default_factory=list)
    signals: list = field(default_factory=list)      # signal details (dicts) for the SuperChart signal panel


# ---------------------------------------------------------------- analyser votes
@dataclass
class Vote:
    """An indicator's read of the market for the analyser score."""
    direction: int          # +1 bullish, -1 bearish, 0 neutral
    reason: str = ""        # short text, e.g. "RSI 62"
    name: str = ""          # optional item name when vote() returns SEVERAL votes (a list), e.g. "Absorption"


# ---------------------------------------------------------------- signals
@dataclass
class Signal:
    t: int                   # bar time the signal belongs to (used to avoid duplicate alerts)
    direction: int           # +1 buy idea, -1 sell idea
    reason: str
    tf: str = ""
    entry: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None


@dataclass
class Context:
    """Everything a plugin can read. Read-only."""
    symbol: str
    bars: Callable[[str], list[Bar]]    # ctx.bars("M5"), "M1" "M5" "M15" "H1" "H4" "D1"
    tick: Optional[Tick]
    smc: dict[str, TFState]             # built-in analyser states: "H4" "H1" "M15" "M5"
    read: Optional[Read]                # built-in analyser verdict/score
    utc_offset: int = 0                 # broker server time minus UTC, in hours (bar times are server time)
    # history(t_end) -> the analyser Read as it was when the bar ending at t_end closed (SuperChart only,
    # None elsewhere). Uses only bars that had closed by then: no look-ahead. Cached, so repeated calls are cheap.
    history: Optional[Callable[[int], "Read"]] = None


# ---------------------------------------------------------------- base classes
class Indicator:
    name = "Indicator"
    description = ""
    default_on = False
    # What the indicator is FOR. Groups the LAYERS menu and the analyser score (1 point per group):
    #   Trend      = direction / trend strength        Momentum  = speed, overbought / oversold
    #   Volatility = size of moves, bands, channels     Volume    = activity, flow, value areas
    #   Levels     = support / resistance prices        SMC       = structure, liquidity, location
    #   Time       = sessions, period separators
    category = "Other"
    purpose = ""            # short "para saan" text shown as a tag, e.g. "Overbought / oversold"
    # Optional: name of a built-in analyser module this indicator switches on while enabled
    # ("premium_discount", "liquidity", "prev_day"). Leave None if it only draws.
    analyser_module = None
    vote_tf = "M15"         # timeframe used by vote() (editable in SuperChart)
    # Editable settings with their defaults, e.g. {"length": 14, "overbought": 70.0}.
    # Read them as self.params["length"]. int stays int, float stays float (SuperChart casts user input).
    params: dict = {}

    def compute(self, bars: list[Bar], tf: str, ctx: Context) -> Overlay:
        """bars = the chart timeframe bars (last bar is still forming)."""
        return Overlay()

    def vote(self, ctx: Context) -> Optional[Vote]:
        """Optional. Return Vote(+1/-1/0, reason) to take part in the analyser score while this
        indicator is ON. Votes are grouped by `category`: each category adds at most 1 point
        (majority of its indicators agreeing with the HTF bias), so similar indicators don't double-count."""
        return None

    def vote_bars(self, ctx: Context) -> list[Bar]:
        """Closed bars of vote_tf (the forming bar is dropped so the vote doesn't flicker)."""
        return ctx.bars(self.vote_tf)[:-1]


class Strategy:
    name = "Strategy"
    description = ""
    default_on = False

    def evaluate(self, ctx: Context) -> list[Signal] | Signal | None:
        """Called about once per second. Return new Signal(s) or None."""
        return None

    def status(self, ctx: Context) -> list[str]:
        """Optional short lines shown under STRATEGIES in the READ panel."""
        return []


# ---------------------------------------------------------------- helpers
# All helpers take plain lists and return lists of the same length (None where not enough data).
def _strip(values):
    i = 0
    while i < len(values) and values[i] is None:
        i += 1
    return i, values[i:]


def _pad(i, out, n):
    return [None] * i + out + [None] * (n - i - len(out))


def ema(values: list, length: int) -> list:
    i, v = _strip(values)
    out = [None] * len(v)
    if len(v) >= length:
        k = 2 / (length + 1)
        e = sum(v[:length]) / length
        out[length - 1] = e
        for j in range(length, len(v)):
            e = v[j] * k + e * (1 - k)
            out[j] = e
    return _pad(i, out, len(values))


def rma(values: list, length: int) -> list:
    """Wilder's moving average (RSI / ATR / ADX)."""
    i, v = _strip(values)
    out = [None] * len(v)
    if len(v) >= length:
        e = sum(v[:length]) / length
        out[length - 1] = e
        for j in range(length, len(v)):
            e = (e * (length - 1) + v[j]) / length
            out[j] = e
    return _pad(i, out, len(values))


def sma(values: list, length: int) -> list:
    i, v = _strip(values)
    out = [None] * len(v)
    s = 0.0
    for j, x in enumerate(v):
        s += x
        if j >= length:
            s -= v[j - length]
        if j >= length - 1:
            out[j] = s / length
    return _pad(i, out, len(values))


def stdev(values: list, length: int) -> list:
    i, v = _strip(values)
    out = [None] * len(v)
    for j in range(length - 1, len(v)):
        w = v[j - length + 1: j + 1]
        m = sum(w) / length
        out[j] = (sum((x - m) ** 2 for x in w) / length) ** 0.5
    return _pad(i, out, len(values))


def highest(values: list, length: int) -> list:
    return [max(values[j - length + 1: j + 1]) if j >= length - 1 else None for j in range(len(values))]


def lowest(values: list, length: int) -> list:
    return [min(values[j - length + 1: j + 1]) if j >= length - 1 else None for j in range(len(values))]


def true_range(bars: list) -> list:
    out = []
    for j, b in enumerate(bars):
        if j == 0:
            out.append(b.h - b.l)
        else:
            pc = bars[j - 1].c
            out.append(max(b.h - b.l, abs(b.h - pc), abs(b.l - pc)))
    return out


def atr(bars: list, length: int = 14) -> list:
    return rma(true_range(bars), length)


def closes(bars: list) -> list:
    return [b.c for b in bars]


def highs(bars: list) -> list:
    return [b.h for b in bars]


def lows(bars: list) -> list:
    return [b.l for b in bars]


def volumes(bars: list) -> list:
    """MT5 gold/forex = tick volume (number of price changes), not exchange volume."""
    return [b.v for b in bars]


def hlc3(bars: list) -> list:
    return [(b.h + b.l + b.c) / 3 for b in bars]


def const(value: float, n: int) -> list:
    return [value] * n
