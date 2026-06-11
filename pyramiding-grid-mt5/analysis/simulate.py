"""Brick-level event simulator for the pyramiding stop-grid strategy.

Because the strategy is renko-equivalent, simulating on the brick stream (with
inter-brick extremes) is EXACT for this strategy class and ~1000x faster than
tick replay.

Cost model: c_rt = full round-trip cost per position (spread + 2*commission,
price units), charged once when a position closes (by any route).

Policy stack (cumulative variants):
  A BASE   : spec-faithful. Every brick fills one position, TP = entry +/- rho*d,
             positions never closed otherwise (orphans live forever).
  B GOV    : + governor. Fill allowed only while recent fills <= base + alpha *
             recent TPs (window in brick-time -> volatility-invariant).
  C TRAIL  : + trend trail. Fill born while run length >= trail_after has no TP;
             exits at the first opposing brick (rides the run, gives back ~1d).
  D FULL   : + pair CloseBy on reversal (locks the nearest orphan at -gap-2c_rt,
             consuming the new fill) when an opposite orphan with gap >= d exists
             + basket TP (close everything at +theta_b*d since reset)
             + budget stop (forced full reset at -budget_d*d since reset).
  P PAIR   : TP + pair-CloseBy ONLY (no governor/trail/basket). This is exactly
             the closed-form state machine of theory_per_brick(); used to validate
             the simulator against theory.
  E MODE   : the design doc's mode state machine, on top of D's exits:
             TREND (run >= trail_after): fill every brick, trail, governor bypassed.
             CHOP  (>=3 consecutive alternations, i.e. BSBS): no fills at all;
                    offside orphans are closed directly at market (-gap - c_rt).
             NORMAL: governor-gated TP fills (as B).
"""
import numpy as np
from collections import deque


class Position:
    __slots__ = ("dir", "entry", "tp", "trail", "born")

    def __init__(self, dir_, entry, tp, trail, born):
        self.dir = dir_
        self.entry = entry
        self.tp = tp
        self.trail = trail
        self.born = born


def simulate(bricks, rho=0.7, c_rt_frac=0.10, policy="A",
             gov_window=30, gov_base=2, gov_alpha=1.0,
             trail_after=3, theta_b=2.0, budget_d=15.0,
             lot_value_of_d=1.0):
    """c_rt_frac: round-trip cost as fraction of d (chi). All PnL is reported in
    units of d (multiply by lot_value_of_d for currency).
    Returns dict of metrics + curves."""
    lvl = bricks["level"]
    dirs = bricks["dir"]
    pre_hi = bricks["pre_hi"]
    pre_lo = bricks["pre_lo"]
    d_arr = lvl * bricks["d_bps"] * 1e-4
    n = len(lvl)

    use_gov = policy in ("B", "C", "D", "E")
    use_trail = policy in ("C", "D", "E")
    use_pair = policy in ("P", "D", "E")
    use_basket = policy in ("D", "E")
    mode_machine = policy == "E"

    open_pos = []
    alt_chain = 0           # consecutive length-1 runs (BSBS detector)
    realized = 0.0          # cumulative, in d-units
    realized_since_reset = 0.0
    locked_total = 0.0      # realized via pair-locks (subset of realized)
    balance_curve = np.empty(n)
    equity_curve = np.empty(n)
    open_curve = np.empty(n, dtype=int)
    fills = np.zeros(n, dtype=bool)
    gated = np.zeros(n, dtype=bool)
    tp_events = deque()     # brick indices of recent TPs
    fill_events = deque()
    run_len = 0
    last_dir = 0
    n_tp = n_trail = n_pair = n_basket_tp = n_budget_stop = 0
    max_open = 0

    for k in range(n):
        P = lvl[k]
        b = dirs[k]
        d = d_arr[k]
        c = c_rt_frac  # cost in d-units (chi)

        # --- 1) TP fills during the interval before this brick (exact via extremes)
        still = []
        for pos in open_pos:
            done = False
            if pos.tp is not None:
                if pos.dir > 0 and pre_hi[k] >= pos.tp:
                    realized_since_reset += (pos.tp - pos.entry) / d - c
                    n_tp += 1
                    tp_events.append(k)
                    done = True
                elif pos.dir < 0 and pre_lo[k] <= pos.tp:
                    realized_since_reset += (pos.entry - pos.tp) / d - c
                    n_tp += 1
                    tp_events.append(k)
                    done = True
            if not done:
                still.append(pos)
        open_pos = still

        # --- 2) trail exits on opposing brick
        if use_trail:
            still = []
            for pos in open_pos:
                if pos.trail and pos.dir != b:
                    realized_since_reset += pos.dir * (P - pos.entry) / d - c
                    n_trail += 1
                else:
                    still.append(pos)
            open_pos = still

        # --- run-length / alternation bookkeeping (uses brick stream itself)
        run_len = run_len + 1 if b == last_dir else 1
        last_dir = b
        alt_chain = alt_chain + 1 if run_len == 1 else 0

        # --- 3) fill permission: governor (B/C/D) or mode machine (E)
        allow = True
        if use_gov:
            while tp_events and tp_events[0] <= k - gov_window:
                tp_events.popleft()
            while fill_events and fill_events[0] <= k - gov_window:
                fill_events.popleft()
            gov_ok = len(fill_events) < gov_base + gov_alpha * len(tp_events)
            if mode_machine:
                if run_len >= trail_after:    # TREND: full participation
                    allow = True
                elif alt_chain >= 3:          # CHOP: abstain completely
                    allow = False
                else:                         # NORMAL: governor-gated
                    allow = gov_ok
            else:
                allow = gov_ok

        # --- 4) pair CloseBy: reversal brick consumed to lock the worst orphan
        consumed = False
        if use_pair and run_len == 1:
            worst, worst_gap = None, 0.0
            for pos in open_pos:
                if pos.dir != b:
                    gap = pos.dir * (pos.entry - P)  # >0 when orphan is offside
                    # 0.9 tolerance: with the adaptive (bps-of-price) step, the
                    # current d can exceed the orphan's entry-time step by the
                    # step growth rate; a tight 0.999 tolerance silently skips
                    # short-orphan pairing on up-reversals (asymmetric leak).
                    if gap >= d * 0.9 and gap > worst_gap:
                        worst, worst_gap = pos, gap
            if worst is not None:
                if allow:
                    # joint value of (orphan + this fill) is frozen: -gap - 2c
                    realized_since_reset += -(worst_gap / d) - 2 * c
                    locked_total += -(worst_gap / d) - 2 * c
                    open_pos.remove(worst)
                    n_pair += 1
                    consumed = True
                elif mode_machine:
                    # CHOP mode: no fill exists; close the orphan directly at
                    # market instead (one position -> only one c_rt).
                    realized_since_reset += -(worst_gap / d) - c
                    locked_total += -(worst_gap / d) - c
                    open_pos.remove(worst)
                    n_pair += 1

        # --- 5) new fill
        if allow and not consumed:
            trail = use_trail and run_len >= trail_after
            tp = None if trail else P + b * rho * d
            open_pos.append(Position(b, P, tp, trail, k))
            fills[k] = True
            fill_events.append(k)
        elif not allow:
            gated[k] = True

        # --- 6) basket TP / budget stop (marks at brick price)
        mark = sum(pos.dir * (P - pos.entry) / d - c for pos in open_pos)
        basket = realized_since_reset + mark
        if use_basket and open_pos:
            if basket >= theta_b:
                realized_since_reset += mark
                open_pos.clear()
                n_basket_tp += 1
                realized += realized_since_reset
                realized_since_reset = 0.0
            elif basket <= -budget_d:
                realized_since_reset += mark
                open_pos.clear()
                n_budget_stop += 1
                realized += realized_since_reset
                realized_since_reset = 0.0

        bal = realized + realized_since_reset
        balance_curve[k] = bal
        equity_curve[k] = bal + sum(
            pos.dir * (P - pos.entry) / d - c for pos in open_pos)
        open_curve[k] = len(open_pos)
        max_open = max(max_open, len(open_pos))

    eq = equity_curve
    peak = np.maximum.accumulate(eq)
    max_dd = float((peak - eq).max()) if n else 0.0
    return dict(
        policy=policy, rho=rho, chi=c_rt_frac,
        balance=balance_curve, equity=equity_curve, open=open_curve,
        final_balance=float(balance_curve[-1]), final_equity=float(eq[-1]),
        min_equity=float(eq.min()), max_dd=max_dd, max_open=int(max_open),
        per_brick=float(eq[-1] / n), n_bricks=n,
        n_tp=n_tp, n_trail=n_trail, n_pair=n_pair,
        n_basket_tp=n_basket_tp, n_budget_stop=n_budget_stop,
        locked_total=float(locked_total),
        gated_frac=float(gated.mean()),
    )


def theory_p_star(chi, rho):
    """Breakeven continuation probability under the pair-close accounting:
    p* = (1 + 2*chi) / ((1 + rho) + chi)   [all in d-units]"""
    return (1 + 2 * chi) / ((1 + rho) + chi)


def theory_per_brick(p, chi, rho):
    """E[PnL]/brick (d-units) for the pair-close state machine on i.i.d. bricks.
    pi_open = 1/(2-p): fraction of bricks arriving with a live position."""
    return (p * (rho - chi) - (1 - p) * (1 + 2 * chi)) / (2 - p)
