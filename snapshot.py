"""
Chart pictures (PNG) made on the server, so they exist even when no browser is open (VPS, auto trade):
  * trade entry  : last bars + entry / SL / TP lines + arrow
  * trade exit   : from before the entry to the exit, with the exit line and result
  * end of day   : the day's bars with every entry / exit marked
Saved in snapshots/ and shown in the Journal; also attached to Telegram alerts. Needs Pillow (pip install pillow);
without it nothing breaks, there is simply no picture.
"""
from __future__ import annotations

import io
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(HERE, "snapshots")

try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except Exception:                       # pragma: no cover
    HAVE_PIL = False

BG, GRID, TXT, DIM = (11, 17, 32), (30, 40, 60), (226, 232, 240), (139, 152, 184)
BULL, BEAR, GOLD, BLUE, WHITE = (38, 215, 160), (255, 90, 120), (242, 193, 78), (99, 102, 241), (226, 232, 240)
_font_cache = {}


def _font(size, bold=False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    names = (["DejaVuSans-Bold.ttf", "arialbd.ttf", "segoeuib.ttf"] if bold else
             ["DejaVuSans.ttf", "arial.ttf", "segoeui.ttf"])
    f = None
    for n in names:
        for base in ("", "C:/Windows/Fonts/", "/usr/share/fonts/truetype/dejavu/"):
            try:
                f = ImageFont.truetype(base + n, size)
                break
            except Exception:
                continue
        if f:
            break
    if f is None:
        try:
            f = ImageFont.load_default(size=size)
        except TypeError:
            f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def render(bars, title="", subtitle="", lines=(), markers=(), spans=(), width=960, height=540, digits=2):
    """bars: list of Bar (t,o,h,l,c). lines: (price, rgb, label). markers: (t, side, rgb, text).
    spans: (t_from, t_to, price_lo, price_hi, rgba) shaded boxes (entry->TP / entry->SL)."""
    if not HAVE_PIL or not bars:
        return None
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img, "RGBA")
    L, R, T, B = 14, 150, 58, 34
    pw, ph = width - L - R, height - T - B
    prices = [x for b in bars for x in (b.h, b.l)] + [p for p, *_ in lines]
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad
    n = len(bars)
    step = pw / n
    y = lambda p: T + (hi - p) / (hi - lo) * ph
    xi = lambda i: L + step * (i + 0.5)
    tidx = {b.t: i for i, b in enumerate(bars)}

    def x_of(t):
        if t in tidx:
            return xi(tidx[t])
        k = max(0, min(n - 1, sum(1 for b in bars if b.t <= t) - 1))
        return xi(k)

    # grid + price axis
    for k in range(6):
        p = lo + (hi - lo) * k / 5
        yy = y(p)
        d.line([(L, yy), (L + pw, yy)], fill=GRID, width=1)
        d.text((L + pw + 8, yy - 8), f"{p:.{digits}f}", fill=DIM, font=_font(13))
    # time labels
    for k in range(0, n, max(1, n // 6)):
        tt = time.strftime("%m-%d %H:%M", time.gmtime(bars[k].t))
        d.text((max(L, min(L + pw - 76, xi(k) - 38)), T + ph + 8), tt, fill=DIM, font=_font(12))
    # shaded spans
    for t0, t1, p0, p1, rgba in spans:
        x0, x1 = x_of(t0), (x_of(t1) if t1 else L + pw)
        d.rectangle([min(x0, x1), y(max(p0, p1)), max(x0, x1) + step / 2, y(min(p0, p1))], fill=rgba)
    # candles
    bw = max(1.0, step * 0.62)
    for i, b in enumerate(bars):
        col = BULL if b.c >= b.o else BEAR
        cx = xi(i)
        d.line([(cx, y(b.h)), (cx, y(b.l))], fill=col, width=1)
        top, bot = y(max(b.o, b.c)), y(min(b.o, b.c))
        if bot - top < 1:
            bot = top + 1
        d.rectangle([cx - bw / 2, top, cx + bw / 2, bot], fill=col)
    # horizontal lines, tags pushed apart so they never overlap
    tags = []
    for p, col, label in lines:
        yy = y(p)
        for x0 in range(L, L + pw, 12):
            d.line([(x0, yy), (min(x0 + 7, L + pw), yy)], fill=col, width=2)
        tags.append([yy, col, f"{label} {p:.{digits}f}"])
    tags.sort(key=lambda t: t[0])
    for k in range(1, len(tags)):
        if tags[k][0] - tags[k - 1][0] < 22:
            tags[k][0] = tags[k - 1][0] + 22
    for ty, col, tag in tags:
        tw = d.textlength(tag, font=_font(13, True))
        d.rectangle([L + pw + 2, ty - 10, L + pw + 8 + tw, ty + 10], fill=col)
        d.text((L + pw + 5, ty - 8), tag, fill=BG, font=_font(13, True))
    # markers (arrows)
    for t, side, col, text in markers:
        cx = x_of(t)
        k = tidx.get(t)
        if k is None:
            k = max(0, min(n - 1, sum(1 for b in bars if b.t <= t) - 1))
        b = bars[k]
        if side == 1:
            yy = y(b.l) + 8
            d.polygon([(cx, yy), (cx - 8, yy + 13), (cx + 8, yy + 13)], fill=col)
            if text:
                d.text((cx - 20, yy + 15), text, fill=col, font=_font(12, True))
        else:
            yy = y(b.h) - 8
            d.polygon([(cx, yy), (cx - 8, yy - 13), (cx + 8, yy - 13)], fill=col)
            if text:
                d.text((cx - 20, yy - 30), text, fill=col, font=_font(12, True))
    # header
    d.text((L, 10), title, fill=TXT, font=_font(20, True))
    if subtitle:
        d.text((L, 35), subtitle, fill=DIM, font=_font(13))
    d.text((width - 170, 12), "REYGUN SUPERCHART", fill=(45, 212, 191), font=_font(13, True))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def save(name, png):
    if not png:
        return None
    os.makedirs(SNAP_DIR, exist_ok=True)
    path = os.path.join(SNAP_DIR, name)
    with open(path, "wb") as f:
        f.write(png)
    return name


def load(name):
    name = os.path.basename(name)
    try:
        with open(os.path.join(SNAP_DIR, name), "rb") as f:
            return f.read()
    except OSError:
        return None


def _bars_between(bars, t_from, t_to, before=40, after=6, max_n=160):
    i0 = next((i for i, b in enumerate(bars) if b.t >= t_from), len(bars) - 1)
    i1 = next((i for i, b in enumerate(bars) if b.t > t_to), len(bars))
    a = max(0, i0 - before)
    z = min(len(bars), i1 + after)
    sel = bars[a:z]
    return sel[-max_n:]


def trade_picture(hub, e, phase="entry", tf="M5"):
    """e = journal entry dict. Returns PNG bytes or None."""
    if not HAVE_PIL:
        return None
    try:
        bars = hub.src.bars(tf)
    except Exception:
        return None
    if not bars:
        return None
    side = e.get("side", 1)
    word = "BUY" if side == 1 else "SELL"
    t_open = e.get("open_time") or time.time()
    t_open_srv = int(e.get("t_srv_open") or t_open + (hub.utc_offset or 0) * 3600)   # bar times = server time
    tfs = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}.get(tf, 300)
    t_bar_open = t_open_srv - t_open_srv % tfs
    digits = getattr(hub.src, "digits", 2) or 2
    entry, sl, tp = e.get("entry"), e.get("sl") or 0, e.get("tp") or 0
    lines = [(entry, WHITE, "ENTRY")]
    if sl:
        lines.append((sl, BEAR, "SL"))
    if tp:
        lines.append((tp, BULL, "TP"))
    sc = e.get("score") or {}
    reg = (e.get("regime") or {}).get("label", "")
    sub = [time.strftime("%Y-%m-%d %H:%M", time.localtime(t_open)), f"{tf}", f"lot {e.get('lot')}",
           f"{e.get('kind', 'manual')}"]
    if sc.get("overall") is not None:
        sub.append(f"score {sc['overall']:.0f}% (pass {sc.get('passing', 0):.0f}%)")
    if reg:
        sub.append(f"regime {reg}")
    spans = []
    if phase == "entry":
        sel = [b for b in bars if b.t <= t_bar_open + tfs][-90:]
        markers = [(t_bar_open, side, BULL if side == 1 else BEAR, word)]
        t_end = None
        title = f"{word} {e.get('symbol', '')} @ {entry:.{digits}f}"
    else:
        t_close = e.get("close_time") or time.time()
        t_close_srv = int(e.get("t_srv_close") or t_close + (hub.utc_offset or 0) * 3600)
        sel = _bars_between(bars, t_bar_open, t_close_srv)
        ex = e.get("exit")
        if ex:
            lines.append((ex, GOLD, "EXIT"))
        res = e.get("result", "")
        rr = e.get("r")
        markers = [(t_bar_open, side, BULL if side == 1 else BEAR, word),
                   (t_close_srv - t_close_srv % tfs, -side, GOLD, "EXIT")]
        t_end = t_close_srv
        title = (f"{word} {e.get('symbol', '')} {entry:.{digits}f} → {ex:.{digits}f}  {res}"
                 + (f"  {rr:+g}R" if rr is not None else "") + f"  {e.get('pnl', 0):+.2f}")
        sub.append(e.get("reason", ""))
    if tp:
        spans.append((t_bar_open, t_end, entry, tp, (38, 215, 160, 38)))
    if sl:
        spans.append((t_bar_open, t_end, entry, sl, (255, 90, 120, 38)))
    if len(sel) < 5:
        return None
    return render(sel, title, " · ".join(x for x in sub if x), lines, markers, spans, digits=digits)


def day_picture(hub, entries, tf="M15", tz_offset=8):
    """The last ~24h with every trade of the day marked."""
    if not HAVE_PIL:
        return None
    bars = hub.src.bars(tf)[-96:]
    if len(bars) < 10:
        return None
    off = (hub.utc_offset or 0) * 3600
    tfs = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600}.get(tf, 900)
    markers = []
    for e in entries:
        t = int(e.get("t_srv_open") or e["open_time"] + off)
        markers.append((t - t % tfs, e["side"], BULL if e["side"] == 1 else BEAR, ""))
        if e.get("close_time"):
            t2 = int(e.get("t_srv_close") or e["close_time"] + off)
            markers.append((t2 - t2 % tfs, -e["side"], GOLD, ""))
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + tz_offset * 3600))
    return render(bars, f"{hub.src.symbol} · {day}", f"{tf} · {len(entries)} trade(s) · green/red = entry · gold = exit",
                  (), markers, digits=getattr(hub.src, "digits", 2) or 2)
