"""Synthetic price generators.

1) random_walk: zero-drift geometric RW. Ground truth for validating the renko
   engine and the no-arbitrage identities (p must be 0.50, costless rho=1 grid
   must be a martingale).
2) regime_mix: Markov regime-switching generator (QUIET / CHOP(OU) / TREND_UP /
   TREND_DN). This is the Phase-3 harness: a controlled world where the *true*
   regime is known, so we can measure exactly how much of the available edge each
   control layer (governor / trail / basket) captures.
"""
import numpy as np

QUIET, CHOP, TREND_UP, TREND_DN = 0, 1, 2, 3
REGIME_NAMES = {QUIET: "QUIET", CHOP: "CHOP", TREND_UP: "TREND_UP", TREND_DN: "TREND_DN"}


def random_walk(n_ticks, sigma=2e-4, x0=100.0, seed=1):
    rng = np.random.default_rng(seed)
    logp = np.cumsum(rng.standard_normal(n_ticks) * sigma)
    return x0 * np.exp(logp - logp[0]), np.zeros(n_ticks, dtype=int)


WORLDS = {
    # Harsh anti-persistent world: chop is strongly mean-reverting, trends weak.
    # No control stack can be net positive here -> tests damage containment.
    "world1": dict(trend_theta=0.55, chop_kappa=0.015,
                   dwell=dict(quiet=40_000, chop=30_000, trend=12_000)),
    # Trend-persistent world: chop milder, trends carry real momentum.
    # Unconditional edge is still ~negative; conditional (regime-gated) edge is
    # positive -> tests whether the controls CAPTURE it.
    "world2": dict(trend_theta=0.62, chop_kappa=0.004,
                   dwell=dict(quiet=35_000, chop=25_000, trend=20_000)),
}


def regime_mix(n_ticks, d_log=2e-3, x0=100.0, seed=7,
               dwell=dict(quiet=40_000, chop=30_000, trend=12_000),
               sig=dict(quiet=None, chop=None, trend=None),
               trend_theta=0.55, chop_kappa=0.015):
    """d_log: the log-size of one brick at d_bps=20 (0.002). Vol per regime is set
    relative to it so brick rates are realistic.
    trend_theta: target log-odds drift strength (p_trend ~ 1/(1+exp(-theta))).
    chop_kappa: OU pull (anti-persistence inside CHOP).
    """
    rng = np.random.default_rng(seed)
    s_quiet = sig["quiet"] or d_log / 40
    s_chop = sig["chop"] or d_log / 12
    s_trend = sig["trend"] or d_log / 12
    # drift giving P(hit +d before -d) ~ 1/(1+e^-theta):  theta = 2*mu*d/sigma^2
    mu_t = trend_theta * s_trend ** 2 / (2 * d_log)

    # transition matrix between regimes (on regime exit)
    nxt = {
        QUIET: [(TREND_UP, 0.225), (TREND_DN, 0.225), (CHOP, 0.55)],
        CHOP: [(QUIET, 0.5), (TREND_UP, 0.25), (TREND_DN, 0.25)],
        TREND_UP: [(CHOP, 0.55), (QUIET, 0.3), (TREND_DN, 0.15)],
        TREND_DN: [(CHOP, 0.55), (QUIET, 0.3), (TREND_UP, 0.15)],
    }
    mean_dwell = {QUIET: dwell["quiet"], CHOP: dwell["chop"],
                  TREND_UP: dwell["trend"], TREND_DN: dwell["trend"]}

    logp = np.empty(n_ticks)
    regs = np.empty(n_ticks, dtype=int)
    x = 0.0
    reg = QUIET
    t_left = rng.geometric(1.0 / mean_dwell[reg])
    ou_anchor = 0.0
    eps = rng.standard_normal(n_ticks)
    for i in range(n_ticks):
        if t_left <= 0:
            opts, probs = zip(*nxt[reg])
            reg = rng.choice(opts, p=probs)
            t_left = rng.geometric(1.0 / mean_dwell[reg])
            if reg == CHOP:
                ou_anchor = x
        if reg == QUIET:
            x += s_quiet * eps[i]
        elif reg == CHOP:
            x += -chop_kappa * (x - ou_anchor) + s_chop * eps[i]
        elif reg == TREND_UP:
            x += mu_t + s_trend * eps[i]
        else:
            x += -mu_t + s_trend * eps[i]
        logp[i] = x
        regs[i] = reg
        t_left -= 1
    return x0 * np.exp(logp - logp[0]), regs
