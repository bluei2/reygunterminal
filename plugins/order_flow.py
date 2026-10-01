"""Order Flow (tick delta): per-bar BUY - SELL delta from MT5 ticks (tick rule) in its own pane, cumulative delta,
and markers for absorption (A), stacked imbalances (S) and delta divergence (D). Full footprint: Order Flow page.

SCORE (own "Order Flow" group, one item per check, on vote_tf, only closed bars):
  Delta        last 3 bars delta + cumulative delta agree        (always there when ticks exist)
  Absorption   latest absorption in the last `lookback` bars      (sellers absorbed = bullish ...)
  Imbalance    latest stacked imbalance in the last `lookback` bars, still holding (price not closed through)
  Divergence   latest delta divergence in the last `lookback` bars
Absorption / Imbalance / Divergence only count when they actually happened recently."""
import orderflow
from engine import TF_SECONDS
from plugin_api import Hist, Indicator, Line, Marker, Overlay, Vote, Zone


class OrderFlowDelta(Indicator):
    name = "Order Flow (tick delta)"
    description = ("Bar delta (buy − sell ticks) + cumulative delta · markers: A absorption, S stacked imbalance, "
                   "D delta divergence · MT5 ticks, tick rule · SCORE: own group with Delta, Absorption, "
                   "Imbalance, Divergence items")
    category = "Order Flow"
    purpose = "Buyers vs sellers (order flow)"
    vote_tf = "M5"
    params = {"show_cum": True, "markers": True, "vote_bars": 3, "event_bars": 150, "lookback": 6,
              "use_absorption": True, "use_imbalance": True, "use_divergence": True}

    def compute(self, bars, tf, ctx):
        of = orderflow.CURRENT
        if of is None or tf not in orderflow.TFS or not bars:
            return Overlay()
        st = {x[0]: x for x in of.bar_stats(tf)}
        delta, cols, cum_line = [], [], []
        cum, day = 0.0, None
        for b in bars:
            x = st.get(b.t)
            if b.t // 86400 != day:
                day, cum = b.t // 86400, 0.0
            if x is None:
                delta.append(None)
                cols.append("#64748b")
                cum_line.append(None)
                continue
            cum += x[3]
            delta.append(x[3])
            cols.append("#26d7a0" if x[3] >= 0 else "#ff5a78")
            cum_line.append(cum)
        ov = Overlay(hists=[Hist(delta, cols, "OF Δ", pane="Order flow Δ")])
        if self.params["show_cum"]:
            ov.lines.append(Line(cum_line, "#fbbf24", "CumΔ", pane="Cum Δ", width=2))
        if self.params["markers"]:
            _, events = of.events_for_chart(tf, int(self.params["event_bars"]))
            times = {b.t for b in bars}
            for e in events:
                if e["t"] not in times:
                    continue
                ch = {"absorption": "A", "stacked": "S", "divergence": "D"}[e["kind"]]
                pos = "below" if e["side"] >= 0 else "above"
                col = "bold #26d7a0" if e["side"] > 0 else "bold #ff5a78" if e["side"] < 0 else "bold #fbbf24"
                ov.markers.append(Marker(e["t"], pos, ch, col))
                if e["kind"] == "stacked":
                    ov.zones.append(Zone(e["lo"], e["hi"], e["t"], "#0f3d2a" if e["side"] > 0 else "#4a1420",
                                         t_to=e["t"] + TF_SECONDS[tf] * 12, label="", border=""))
        return ov

    def vote(self, ctx):
        of = orderflow.CURRENT
        if of is None or self.vote_tf not in orderflow.TFS:
            return None
        vb = self.vote_bars(ctx)
        if not vb:
            return None
        p = self.params
        sec = TF_SECONDS[self.vote_tf]
        t_end = vb[-1].t + sec                       # only bars closed by then (no look-ahead in the history)
        st = of.bar_stats(self.vote_tf, t_end=t_end)
        if len(st) < 3:
            return None
        votes = []
        # ---- delta
        n = int(p["vote_bars"])
        recent = sum(x[3] for x in st[-n:])
        day = st[-1][0] // 86400
        cum = sum(x[3] for x in st if x[0] // 86400 == day)
        if recent > 0 and cum > 0:
            votes.append(Vote(1, f"Δ last {n} {recent:+.0f}, CumΔ {cum:+.0f}", name="OF Delta"))
        elif recent < 0 and cum < 0:
            votes.append(Vote(-1, f"Δ last {n} {recent:+.0f}, CumΔ {cum:+.0f}", name="OF Delta"))
        else:
            votes.append(Vote(0, f"mixed: Δ {recent:+.0f} vs CumΔ {cum:+.0f}", name="OF Delta"))
        # ---- events of the last `lookback` closed bars
        lb = int(p["lookback"])
        fp = of.footprint(self.vote_tf, lb, t_end=t_end)
        bars = fp["bars"]
        if not bars:
            return votes
        last_t, last_c = bars[-1]["t"], bars[-1]["c"]

        def ago(t):
            k = (last_t - t) // sec
            return "this bar" if k == 0 else f"{k} bar{'s' if k > 1 else ''} ago"

        latest = {}
        for e in fp["events"]:                        # newest first
            latest.setdefault(e["kind"], e)
        a = latest.get("absorption")
        if p["use_absorption"] and a and a["side"] != 0:
            votes.append(Vote(a["side"], f"{a['text']} ({ago(a['t'])})", name="OF Absorption"))
        s_ev = latest.get("stacked")
        if p["use_imbalance"] and s_ev:
            holding = last_c >= s_ev["lo"] if s_ev["side"] > 0 else last_c <= s_ev["hi"]
            if holding:
                votes.append(Vote(s_ev["side"], f"stacked {'buy' if s_ev['side'] > 0 else 'sell'} imbalance "
                                                f"{s_ev['lo']:.2f}-{s_ev['hi']:.2f} holding ({ago(s_ev['t'])})",
                                  name="OF Imbalance"))
            else:
                votes.append(Vote(-s_ev["side"], f"stacked {'buy' if s_ev['side'] > 0 else 'sell'} imbalance "
                                                 f"{s_ev['lo']:.2f}-{s_ev['hi']:.2f} FAILED (closed through)",
                                  name="OF Imbalance"))
        d = latest.get("divergence")
        if p["use_divergence"] and d:
            votes.append(Vote(d["side"], f"{d['text']} ({ago(d['t'])})", name="OF Divergence"))
        return votes
