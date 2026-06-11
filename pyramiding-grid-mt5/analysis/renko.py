"""d-Renko engine: converts a price path into the exact event stream of the
pyramiding stop-grid strategy (anchor-following, 1-brick or r-multiple reversal).

Strategy equivalence: every brick == one stop-order fill. The strategy's PnL is a
function of the brick sequence plus inter-brick extremes (for intra-brick TP fills).
"""
import numpy as np


class RenkoBuilder:
    """Resumable anchor-grid renko with inter-brick extreme tracking.

    d_bps    : grid step as basis points of current anchor (adaptive step).
    r_mult   : reversal multiple. 1.0 = the user's spec (stop at anchor -/+ d both
               sides); 2.0 = standard renko (reversal needs 2d against run direction).
    Emits per brick: time, level, dir (+1/-1), pre_hi, pre_lo
      pre_hi/pre_lo = path extremes since the PREVIOUS brick (includes this brick's
      trigger level). Used to evaluate TP fills of the position opened at the
      previous brick, exactly.
    """

    def __init__(self, d_bps=20.0, r_mult=1.0):
        self.d_bps = float(d_bps)
        self.r_mult = float(r_mult)
        self.anchor = None
        self.dir = 0          # direction of last brick
        self.run_hi = None    # extremes since last brick
        self.run_lo = None
        # output buffers
        self.t = []
        self.level = []
        self.dirs = []
        self.pre_hi = []
        self.pre_lo = []
        self.tag = []         # optional per-brick tag (e.g. regime id at emission)

    def _d(self):
        return self.anchor * self.d_bps * 1e-4

    def update(self, x, t, tag=0):
        if self.anchor is None:
            self.anchor = x
            self.run_hi = self.run_lo = x
            return
        d = self._d()
        # thresholds depend on last direction
        if self.dir >= 0:
            up_th = self.anchor + d
        else:
            up_th = self.anchor + self.r_mult * d
        if self.dir <= 0:
            dn_th = self.anchor - d
        else:
            dn_th = self.anchor - self.r_mult * d

        while x >= up_th or x <= dn_th:
            if x >= up_th:
                lvl, b = up_th, +1
            else:
                lvl, b = dn_th, -1
            # close out the inter-brick interval (path reached lvl)
            self.pre_hi.append(max(self.run_hi, lvl))
            self.pre_lo.append(min(self.run_lo, lvl))
            self.t.append(t)
            self.level.append(lvl)
            self.dirs.append(b)
            self.tag.append(tag)
            # new state
            self.anchor = lvl
            self.dir = b
            self.run_hi = self.run_lo = lvl
            d = self._d()
            if self.dir >= 0:
                up_th = self.anchor + d
            else:
                up_th = self.anchor + self.r_mult * d
            if self.dir <= 0:
                dn_th = self.anchor - d
            else:
                dn_th = self.anchor - self.r_mult * d
        # remainder of this tick belongs to the next interval
        if x > self.run_hi:
            self.run_hi = x
        if x < self.run_lo:
            self.run_lo = x

    def update_path(self, prices, times=None, tags=None):
        n = len(prices)
        if times is None:
            times = np.arange(n)
        if tags is None:
            for i in range(n):
                self.update(float(prices[i]), times[i], 0)
        else:
            for i in range(n):
                self.update(float(prices[i]), times[i], int(tags[i]))

    def bricks(self):
        return {
            "t": np.asarray(self.t),
            "level": np.asarray(self.level, dtype=float),
            "dir": np.asarray(self.dirs, dtype=int),
            "pre_hi": np.asarray(self.pre_hi, dtype=float),
            "pre_lo": np.asarray(self.pre_lo, dtype=float),
            "tag": np.asarray(self.tag, dtype=int),
            "d_bps": self.d_bps,
            "r_mult": self.r_mult,
        }


# ---------------------------------------------------------------- statistics

def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    ctr = (p + z * z / (2 * n)) / den
    hw = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (ctr - hw, ctr + hw)


def run_stats(dirs):
    """Overall continuation probability, run-length distribution, p_k curve."""
    d = np.asarray(dirs)
    same = d[1:] == d[:-1]
    p = same.mean() if len(same) else np.nan

    # run lengths
    runs = []
    cur = 1
    for s in same:
        if s:
            cur += 1
        else:
            runs.append(cur)
            cur = 1
    runs.append(cur)
    runs = np.asarray(runs)

    # p_k: P(continue | current run length == k)
    pk = {}
    cur = 1
    for s in same:
        rec = pk.setdefault(min(cur, 12), [0, 0])
        rec[1] += 1
        if s:
            rec[0] += 1
        cur = cur + 1 if s else 1
    pk_tab = {}
    for k, (kk, nn) in sorted(pk.items()):
        lo, hi = wilson_ci(kk, nn)
        pk_tab[k] = dict(p=kk / nn, n=nn, lo=lo, hi=hi)

    return dict(p=p, n_trans=len(same), runs=runs, E_L=runs.mean(),
                pk=pk_tab)


def chop_episodes(dirs, max_run=2, min_bricks=6):
    """Maximal windows where every run <= max_run. Returns per-episode
    (#bricks, #direction-changes). Direction changes are what bleed money."""
    d = np.asarray(dirs)
    same = d[1:] == d[:-1]
    # build run-length sequence aligned to bricks
    episodes = []
    i = 0
    n = len(d)
    # compute run length at each brick
    rl = np.ones(n, dtype=int)
    for j in range(1, n):
        rl[j] = rl[j - 1] + 1 if d[j] == d[j - 1] else 1
    j = 0
    while j < n:
        if rl[j] <= max_run:
            start = j
            while j < n and rl[j] <= max_run:
                j += 1
            n_b = j - start
            if n_b >= min_bricks:
                ch = int((d[start + 1:j] != d[start:j - 1]).sum())
                episodes.append((n_b, ch))
        else:
            j += 1
    return episodes


def q_of_rho(bricks, rhos):
    """P(run-final position still hits TP=rho*d via interim excursion before the
    reversal brick). Free alpha not captured by the run-length formula."""
    lvl, dirs = bricks["level"], bricks["dir"]
    pre_hi, pre_lo = bricks["pre_hi"], bricks["pre_lo"]
    d_of = lvl * bricks["d_bps"] * 1e-4
    out = {}
    rev = np.where(dirs[1:] != dirs[:-1])[0]  # k: brick k is final of its run
    for rho in rhos:
        hit = 0
        for k in rev:
            if dirs[k] > 0:
                if pre_hi[k + 1] >= lvl[k] + rho * d_of[k]:
                    hit += 1
            else:
                if pre_lo[k + 1] <= lvl[k] - rho * d_of[k]:
                    hit += 1
        out[rho] = hit / max(len(rev), 1)
    return out


def p_by_tag(dirs, tags):
    """Continuation probability conditioned on the regime tag of the SOURCE brick
    (the state in which the position was opened — no look-ahead)."""
    d, g = np.asarray(dirs), np.asarray(tags)
    res = {}
    for tag in np.unique(g):
        idx = np.where(g[:-1] == tag)[0]
        if len(idx) < 10:
            continue
        same = d[1:][idx] == d[:-1][idx]
        res[int(tag)] = dict(p=float(same.mean()), n=int(len(idx)))
    return res
