"""Phase-1/Phase-3 study driver.

1. Validates the renko engine + simulator against closed-form theory on a
   random walk (martingale consistency, pair identity, overshoot=slippage).
2. Demonstrates the "grid illusion" (variant A on a random walk).
3. Two regime-switching Monte Carlo worlds:
     world1 = harsh anti-persistent (no positive parameterization exists)
              -> tests damage CONTAINMENT of the control stack.
     world2 = trend-persistent (conditional edge exists in TREND regime)
              -> tests edge CAPTURE of the control stack.
4. d x rho heatmap (world2) and p* vs chi design charts.

Outputs: analysis/out/*.png, analysis/out/stats.json, stdout summary.
"""
import json
import os
import time

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from renko import RenkoBuilder, run_stats, chop_episodes, q_of_rho, p_by_tag
from synthetic import random_walk, regime_mix, REGIME_NAMES, WORLDS
from simulate import simulate, theory_p_star, theory_per_brick

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)
R = {}
T0 = time.time()


def log(*a):
    print(f"[{time.time()-T0:7.1f}s]", *a, flush=True)


def build(prices, tags=None, d_bps=20.0, r_mult=1.0):
    rb = RenkoBuilder(d_bps=d_bps, r_mult=r_mult)
    rb.update_path(prices, tags=tags)
    return rb.bricks()


def pk_by_tag(dirs, tags, k_max=6):
    cur, out = 1, {}
    for i in range(1, len(dirs)):
        key = (int(tags[i - 1]), min(cur, k_max))
        rec = out.setdefault(key, [0, 0])
        rec[1] += 1
        if dirs[i] == dirs[i - 1]:
            rec[0] += 1
            cur += 1
        else:
            cur = 1
    return {k: (v[0] / v[1], v[1]) for k, v in out.items() if v[1] >= 30}


def slim(sim):
    return {k: v for k, v in sim.items() if not isinstance(v, np.ndarray)}


# ===================================================== 1. RANDOM-WALK VALIDATION
log("=== 1. random-walk validation ===")
D_BPS = 20.0
d_log = D_BPS * 1e-4
sigma = d_log / 12          # ~144 ticks per brick
px, _ = random_walk(4_000_000, sigma=sigma, seed=11)
bk_rw = build(px, d_bps=D_BPS)
st_rw = run_stats(bk_rw["dir"])
p_hat = st_rw["p"]
p_pred = 0.5 + 0.5826 * sigma / (2 * d_log)   # Siegmund overshoot bias
q_rw = q_of_rho(bk_rw, [0.5, 0.7, 0.9, 1.0])
log(f"RW bricks={len(bk_rw['dir'])}  p̂={p_hat:.4f}  E[L]={st_rw['E_L']:.3f}  "
    f"p_pred(overshoot)={p_pred:.4f}")
log("  RW q(rho) rescue:", {k: round(v, 3) for k, v in q_rw.items()})

val = {}
for rho, chi in [(1.0, 0.10), (1.0, 0.0), (0.7, 0.10)]:
    sim = simulate(bk_rw, rho=rho, c_rt_frac=chi, policy="P")
    th = theory_per_brick(p_hat, chi, rho)
    pair_avg = sim["locked_total"] / sim["n_pair"] if sim["n_pair"] else float("nan")
    val[f"rho{rho}_chi{chi}"] = dict(sim=sim["per_brick"], theory=th,
                                     pair_avg=pair_avg, n_pair=sim["n_pair"],
                                     max_open=sim["max_open"])
    log(f"  P rho={rho} chi={chi}: sim/brick={sim['per_brick']:+.5f}d "
        f"theory={th:+.5f}d  pair_avg={pair_avg:+.4f}d (ident -{1+2*chi:.2f}) "
        f"max_open={sim['max_open']}")
R["validation"] = dict(p_hat=p_hat, p_pred=p_pred, E_L=st_rw["E_L"],
                       n_bricks=len(bk_rw["dir"]),
                       q_rho_rw={str(k): v for k, v in q_rw.items()}, cases=val)

# ------------------------------------------------ 2. grid illusion (A on RW)
log("=== 2. grid illusion: variant A on RW ===")
simA_rw = simulate(bk_rw, rho=0.7, c_rt_frac=0.10, policy="A")
R["illusion"] = {k: simA_rw[k] for k in
                 ("final_balance", "final_equity", "min_equity", "max_open")}
log(f"  A on RW: balance={simA_rw['final_balance']:+.0f}d  "
    f"equity={simA_rw['final_equity']:+.0f}d  min_eq={simA_rw['min_equity']:+.0f}d  "
    f"max_open={simA_rw['max_open']}")

fig, ax = plt.subplots(2, 1, figsize=(10, 7), sharex=True,
                       gridspec_kw=dict(height_ratios=[2, 1]))
ax[0].plot(simA_rw["balance"], lw=1.0, label="balance (realized) — looks great")
ax[0].plot(simA_rw["equity"], lw=1.0, label="equity (incl. open marks) — the truth")
ax[0].axhline(0, color="k", lw=0.5)
ax[0].set_ylabel("PnL (d-units)")
ax[0].legend()
ax[0].set_title("Variant A (spec-faithful, no controls) on a pure random walk\n"
                "every TP eventually hits, yet equity decays: the grid illusion")
ax[1].plot(simA_rw["open"], lw=0.8, color="tab:red")
ax[1].set_ylabel("open positions")
ax[1].set_xlabel("brick #")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig_rw_illusion.png"), dpi=130)
plt.close(fig)

# ===================================================== 3. TWO REGIME WORLDS
CHI = 0.10
worlds = {}
for wname, wkw in WORLDS.items():
    log(f"=== 3. {wname}: generate + renko ===")
    pxm, regs = regime_mix(8_000_000, d_log=d_log, seed=7, **wkw)
    bk = build(pxm, tags=regs, d_bps=D_BPS)
    st = run_stats(bk["dir"])
    preg = p_by_tag(bk["dir"], bk["tag"])
    qs = q_of_rho(bk, [0.5, 0.7, 0.9, 1.0])
    eps = chop_episodes(bk["dir"], max_run=2, min_bricks=6)
    changes = np.array([c for _, c in eps]) if eps else np.array([0])
    qq = {q: float(np.percentile(changes, q)) for q in (50, 90, 95, 99)}
    log(f"  bricks={len(bk['dir'])}  p={st['p']:.4f}  E[L]={st['E_L']:.3f}")
    for tag, v in sorted(preg.items()):
        log(f"    p({REGIME_NAMES[tag]:8s}) = {v['p']:.4f}  (n={v['n']})")
    log(f"  q(rho)={ {k: round(v,3) for k,v in qs.items()} }")
    log(f"  chop episodes={len(eps)} changes quantiles={qq}")

    variants = {}
    rho_list_v = [0.7, 0.9] if wname == "world2" else [0.7]
    for rho_v in rho_list_v:
        for pol in ["A", "B", "C", "D", "E", "P"]:
            s = simulate(bk, rho=rho_v, c_rt_frac=CHI, policy=pol,
                         gov_window=30, gov_base=2, gov_alpha=1.0,
                         trail_after=3, theta_b=3.0, budget_d=20.0)
            variants[f"{pol}_rho{rho_v}"] = s
            log(f"  [{wname} rho={rho_v}] {pol}: eq={s['final_equity']:+8.0f}d "
                f"bal={s['final_balance']:+8.0f}d min={s['min_equity']:+7.0f}d "
                f"dd={s['max_dd']:6.0f}d open<={s['max_open']:4d} "
                f"gated={s['gated_frac']:.0%} tp={s['n_tp']} tr={s['n_trail']} "
                f"pr={s['n_pair']} bTP={s['n_basket_tp']} stp={s['n_budget_stop']}")

    worlds[wname] = dict(bk=bk, px=pxm, regs=regs, st=st, preg=preg,
                         variants=variants)
    R[wname] = dict(n_bricks=len(bk["dir"]), p=st["p"], E_L=st["E_L"],
                    p_regime={REGIME_NAMES[t]: v for t, v in preg.items()},
                    q_rho={str(k): v for k, v in qs.items()},
                    pk={str(k): v for k, v in st["pk"].items()},
                    chop=dict(n_episodes=len(eps), changes_quantiles=qq),
                    variants={k: slim(s) for k, s in variants.items()})

    if wname == "world1":
        fig, ax = plt.subplots(figsize=(8, 5))
        xs = np.sort(changes)
        ax.step(xs, 1 - np.arange(len(xs)) / len(xs), where="post")
        ax.set_yscale("log")
        ax.set_xlabel("direction changes per chop episode (= locked pairs)")
        ax.set_ylabel("survival P(X >= x)")
        ax.set_title("Chop persistence (world1, harsh) — each change locks "
                     "(1+2chi)*d\nbudget = n95 * (1+2chi) * d * lot_value")
        for q, v in qq.items():
            ax.axvline(v, color="tab:red", lw=0.8, alpha=0.6)
            ax.text(v, 0.5, f"p{q}={v:.0f}", rotation=90, fontsize=8, va="center")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(OUT, "fig_chop.png"), dpi=130)
        plt.close(fig)

# equity comparison figure per world
for wname in worlds:
    variants = worlds[wname]["variants"]
    rho_show = 0.9 if wname == "world2" else 0.7
    fig, ax = plt.subplots(2, 1, figsize=(11, 8), sharex=True,
                           gridspec_kw=dict(height_ratios=[2, 1]))
    colors = dict(A="tab:gray", B="tab:orange", C="tab:blue", D="tab:green",
                  E="tab:purple")
    for pol in ["A", "B", "C", "D", "E"]:
        s = variants[f"{pol}_rho{rho_show}"]
        ax[0].plot(s["equity"], lw=1.0, color=colors[pol],
                   label=f"{pol}  (eq {s['final_equity']:+.0f}d)")
    ax[0].axhline(0, color="k", lw=0.5)
    ax[0].set_ylabel("equity (d-units)")
    ax[0].set_title(f"Policy stack on {wname} (chi={CHI}, rho={rho_show})\n"
                    "A=spec-only B=+governor C=+trail D=+pairs/basket/budget "
                    "E=mode machine")
    ax[0].legend()
    for pol in ["A", "E"]:
        s = variants[f"{pol}_rho{rho_show}"]
        ax[1].plot(s["open"], lw=0.8, color=colors[pol], label=pol)
    ax[1].set_ylabel("open positions")
    ax[1].set_xlabel("brick #")
    ax[1].legend()
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, f"fig_variants_{wname}.png"), dpi=130)
    plt.close(fig)

# p_k curves figure (world2 headline + world1 + RW)
fig, ax = plt.subplots(figsize=(9, 6))
pk2 = worlds["world2"]["st"]["pk"]
ks = sorted(pk2)
ax.errorbar(ks, [pk2[k]["p"] for k in ks],
            yerr=[[pk2[k]["p"] - pk2[k]["lo"] for k in ks],
                  [pk2[k]["hi"] - pk2[k]["p"] for k in ks]],
            marker="o", color="tab:blue", label="world2 all", capsize=3)
pk1 = worlds["world1"]["st"]["pk"]
ks1 = sorted(pk1)
ax.plot(ks1, [pk1[k]["p"] for k in ks1], marker="d", ls="-.",
        color="tab:brown", label="world1 all")
ks0 = sorted(st_rw["pk"])
ax.plot(ks0, [st_rw["pk"][k]["p"] for k in ks0], marker="s", ls="--",
        color="tab:gray", label="random walk (overshoot incl.)")
pk_tag2 = pk_by_tag(worlds["world2"]["bk"]["dir"], worlds["world2"]["bk"]["tag"])
for tag, nm, mk, cl in [(1, "world2 CHOP", "v", "tab:red"),
                        (2, "world2 TREND_UP", "^", "tab:green")]:
    pts = {k: v for (t, k), (v, n) in pk_tag2.items() if t == tag}
    if pts:
        ax.plot(sorted(pts), [pts[k] for k in sorted(pts)], marker=mk,
                ls=":", color=cl, label=nm)
for rho, c in [(0.7, "tab:red"), (1.0, "tab:green")]:
    ax.axhline(theory_p_star(CHI, rho), color=c, lw=1, alpha=0.7,
               label=f"breakeven p* (chi={CHI}, rho={rho})")
ax.set_xlabel("current run length k (bricks)")
ax.set_ylabel("P(continue | run length = k)")
ax.set_title("Continuation structure p_k — the strategy exists iff some\n"
             "reachable, detectable state sits above p*")
ax.legend(fontsize=8)
ax.grid(alpha=0.3)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig_pk.png"), dpi=130)
plt.close(fig)

# ===================================================== 4. d x rho heatmap (world2)
log("=== 4. d x rho heatmap on world2 (c_rt fixed at 2.0 bps) ===")
C_BPS = 2.0
d_list = [10.0, 20.0, 30.0, 50.0]
rho_list = [0.5, 0.7, 0.9, 1.0]
heat = np.zeros((len(d_list), len(rho_list)))
px2, regs2 = worlds["world2"]["px"], worlds["world2"]["regs"]
cache = {D_BPS: worlds["world2"]["bk"]}
for i, db in enumerate(d_list):
    if db not in cache:
        log(f"  building renko d_bps={db} ...")
        cache[db] = build(px2, tags=regs2, d_bps=db)
    for j, rho in enumerate(rho_list):
        s = simulate(cache[db], rho=rho, c_rt_frac=C_BPS / db, policy="D",
                     theta_b=3.0, budget_d=20.0)
        heat[i, j] = s["final_equity"] * db  # total bps captured on same path
R["heatmap_world2"] = dict(d_bps=d_list, rho=rho_list, total_bps=heat.tolist())
log("  heatmap (rows d, cols rho):")
for i, db in enumerate(d_list):
    log("   ", db, "bps:", [f"{heat[i,j]:+8.0f}" for j in range(len(rho_list))])

fig, ax = plt.subplots(figsize=(8.5, 5.5))
vmax = np.abs(heat).max()
im = ax.imshow(heat, cmap="RdYlGn", aspect="auto", vmin=-vmax, vmax=vmax)
ax.set_xticks(range(len(rho_list)), [f"{r}" for r in rho_list])
ax.set_yticks(range(len(d_list)), [f"{d:.0f} (chi={C_BPS/d:.2f})" for d in d_list])
ax.set_xlabel("rho = TP/d")
ax.set_ylabel("d (bps of price)")
ax.set_title(f"Variant D, world2: total captured bps on same path "
             f"(c_rt={C_BPS}bps fixed)")
for i in range(len(d_list)):
    for j in range(len(rho_list)):
        ax.text(j, i, f"{heat[i,j]:+.0f}", ha="center", va="center", fontsize=9)
fig.colorbar(im, label="total bps")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig_heatmap.png"), dpi=130)
plt.close(fig)

# ===================================================== 5. p* vs chi design chart
fig, ax = plt.subplots(figsize=(8.5, 5.5))
chis = np.linspace(0.0, 0.4, 200)
for rho in [0.5, 0.7, 0.9, 1.0]:
    ax.plot(chis, [theory_p_star(c, rho) for c in chis], label=f"rho={rho}")
for wname, ls in [("world1", ":"), ("world2", "--")]:
    preg = worlds[wname]["preg"]
    if 2 in preg:
        ax.axhline(preg[2]["p"], color="tab:green", ls=ls, lw=1,
                   label=f"{wname} TREND p={preg[2]['p']:.3f}")
    if 1 in preg:
        ax.axhline(preg[1]["p"], color="tab:red", ls=ls, lw=1,
                   label=f"{wname} CHOP p={preg[1]['p']:.3f}")
ax.set_xlabel("chi = c_rt / d  (round-trip cost / grid step)")
ax.set_ylabel("breakeven continuation p*")
ax.set_title("Design rule: keep chi <= 0.10 and operate only where p > p*(chi, rho)")
ax.legend(fontsize=7)
ax.grid(alpha=0.3)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig_pstar.png"), dpi=130)
plt.close(fig)

# ============================================ 6. capture frontier (policy D)
log("=== 6. capture frontier: required p_trend vs chi (D, rho=0.9, d=20bps) ===")
thetas = [0.62, 0.85, 1.10, 1.39]      # true trend log-odds -> p ~ .65/.70/.75/.80
chis_f = [0.04, 0.07, 0.10]
frontier = []
for th in thetas:
    pxf, regf = regime_mix(8_000_000, d_log=d_log, seed=13, trend_theta=th,
                           chop_kappa=0.004,
                           dwell=dict(quiet=35_000, chop=25_000, trend=20_000))
    bkf = build(pxf, tags=regf, d_bps=D_BPS)
    stf = run_stats(bkf["dir"])
    pregf = p_by_tag(bkf["dir"], bkf["tag"])
    n_up, n_dn = pregf[2]["n"], pregf[3]["n"]
    p_tr = (pregf[2]["p"] * n_up + pregf[3]["p"] * n_dn) / (n_up + n_dn)
    row = dict(theta=th, p_trend=p_tr, p_all=stf["p"],
               n_bricks=len(bkf["dir"]), res={})
    for pol in ["D", "E"]:
        for cf in chis_f:
            s = simulate(bkf, rho=0.9, c_rt_frac=cf, policy=pol,
                         theta_b=3.0, budget_d=20.0)
            row["res"][f"{pol}_{cf}"] = dict(eq=s["final_equity"],
                                             per1k=1000 * s["per_brick"],
                                             dd=s["max_dd"],
                                             stops=s["n_budget_stop"])
            log(f"  theta={th} p_trend={p_tr:.3f} p_all={stf['p']:.3f} "
                f"{pol} chi={cf}: eq={s['final_equity']:+8.0f}d "
                f"per1k={1000*s['per_brick']:+7.1f}d dd={s['max_dd']:.0f}d "
                f"stops={s['n_budget_stop']}")
    frontier.append(row)
R["frontier"] = frontier

fig, ax = plt.subplots(figsize=(9.5, 6))
for cf, cl in zip(chis_f, ["tab:green", "tab:orange", "tab:red"]):
    xs = [r["p_trend"] for r in frontier]
    ax.plot(xs, [r["res"][f"E_{cf}"]["per1k"] for r in frontier],
            marker="o", color=cl, label=f"E (mode machine) chi={cf}")
    ax.plot(xs, [r["res"][f"D_{cf}"]["per1k"] for r in frontier],
            marker="s", ls="--", color=cl, alpha=0.6, label=f"D chi={cf}")
    ax.axvline(theory_p_star(cf, 0.9), color=cl, ls=":", lw=1, alpha=0.8)
ax.axhline(0, color="k", lw=0.8)
ax.set_xlabel("measured p_trend (continuation prob inside trend regime)")
ax.set_ylabel("equity per 1000 bricks (d-units)")
ax.set_title("Capture frontier — dotted verticals: theoretical p*(chi, rho=0.9);\n"
             "gap between p* and each zero-crossing = implementation tax")
ax.legend(fontsize=8)
ax.grid(alpha=0.3)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig_frontier.png"), dpi=130)
plt.close(fig)

# ============================================ 7. reversal width 1d vs 2d
log("=== 7. reversal width r_mult 1 vs 2 (world2, rho=0.9, chi=0.10) ===")
bk_r2 = build(px2, tags=regs2, d_bps=D_BPS, r_mult=2.0)
st_r2 = run_stats(bk_r2["dir"])
log(f"  r2 bricks={len(bk_r2['dir'])} p={st_r2['p']:.4f} E[L]={st_r2['E_L']:.3f} "
    f"(r1: bricks={len(worlds['world2']['bk']['dir'])} "
    f"p={worlds['world2']['st']['p']:.4f})")
rcmp = dict(r2_p=st_r2["p"], r2_bricks=len(bk_r2["dir"]))
for pol in ["P", "D", "E"]:
    s2 = simulate(bk_r2, rho=0.9, c_rt_frac=CHI, policy=pol,
                  theta_b=3.0, budget_d=20.0)
    s1 = worlds["world2"]["variants"][f"{pol}_rho0.9"]
    rcmp[pol] = dict(r1_eq=s1["final_equity"], r2_eq=s2["final_equity"],
                     r1_dd=s1["max_dd"], r2_dd=s2["max_dd"],
                     r1_pairs=s1["n_pair"], r2_pairs=s2["n_pair"])
    log(f"  {pol}: r1 eq={s1['final_equity']:+8.0f}d dd={s1['max_dd']:6.0f}d | "
        f"r2 eq={s2['final_equity']:+8.0f}d dd={s2['max_dd']:6.0f}d")
R["reversal_width"] = rcmp

# ===================================================== save
with open(os.path.join(OUT, "stats.json"), "w") as f:
    json.dump(R, f, indent=1, default=float)
log("done. outputs in", OUT)
