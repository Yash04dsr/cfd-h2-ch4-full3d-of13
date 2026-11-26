#!/usr/bin/env python3
"""
Merge Herwig & Schmandt (2011) dissipation-based irreversible pressure drop dP_irr
into all_cases_summary.json / .csv and regenerate fig1 & fig5 (300 DPI).
"""
import csv, json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent.parent
FIGS = OUT / "figures"
data = json.loads((OUT / "all_cases_summary.json").read_text())
herwig = json.loads((OUT / "herwig_dissipation.json").read_text())

A_out = np.pi * (0.460 / 2.0) ** 2

for cname, h in herwig.items():
    if cname not in data:
        continue
    d = data[cname]
    W = h["W_turb_W"]
    mdot = d.get("mdot_out_kg_s") or 93.7
    y_h2 = d.get("y_h2_out_mean") or 0.0195
    rho_out = 1.0 / (y_h2 / 5.56 + (1.0 - y_h2) / 55.65)
    Q_out = mdot / rho_out
    U_out = Q_out / A_out
    dP_irr_Pa = W / Q_out
    K_loss = 2.0 * W / (mdot * U_out ** 2)
    d["W_turb_W"] = round(W, 1)
    d["dP_irr_Pa"] = round(dP_irr_Pa, 1)
    d["dP_irr_kPa"] = round(dP_irr_Pa / 1000.0, 4)
    d["K_loss"] = round(K_loss, 4)
    d["n_cells"] = h["n_cells"]

(OUT / "all_cases_summary.json").write_text(json.dumps(data, indent=2))

# Rewrite clean master CSV with Herwig dP_irr_kPa, W_turb_W, K_loss
cols = [
    "case", "angle_deg", "orientation", "d_ratio", "vr", "turbulence", "sct",
    "cov_x_5D", "cov_x_10D", "cov_y_5D", "cov_y_10D", "Lmix_D_CoV05",
    "W_turb_W", "dP_irr_kPa", "K_loss", "mass_err_pct", "x_h2_out_pct", "yplus_avg"
]
with (OUT / "all_cases_summary.csv").open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(cols)
    for cname in sorted(data):
        d = data[cname]
        s = d["spec"]
        w.writerow([
            cname, s.get("angle_deg"), s.get("orientation"), s.get("d_ratio"),
            s.get("vr"), s.get("turbulence"), s.get("sct"),
            d.get("cov_x_5D"), d.get("cov_x_10D"), d.get("cov_y_5D"), d.get("cov_y_10D"),
            d.get("Lmix_D_CoV05"), d.get("W_turb_W"), d.get("dP_irr_kPa"), d.get("K_loss"),
            d.get("mass_err_pct"), d.get("x_h2_out_pct"), d.get("yplus_avg")
        ])

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.titlesize": 12.5,
    "axes.titleweight": "bold",
    "axes.labelsize": 11,
    "legend.fontsize": 9.3,
    "figure.dpi": 300,
    "axes.grid": True,
    "grid.alpha": 0.28,
})

def get(c, k, default=None):
    return data.get(c, {}).get(k, default)

# ==============================================================================
# UPDATED FIG 1: Angle Sweep (Top vs Bottom) — CoV_X(10D) & Herwig dP_irr
# ==============================================================================
top_angles = [
    (30,  "h2_ch4_30deg_top_verified"),
    (60,  "h2_ch4_60deg_verified"),
    (90,  "h2_ch4_90deg_base_verified"),
    (120, "h2_ch4_120deg_top_verified"),
    (150, "h2_ch4_150deg_top_verified"),
]
bot_angles = [
    (30,  "h2_ch4_30deg_bottom_verified"),
    (60,  "h2_ch4_60deg_bottom_verified"),
    (90,  "h2_ch4_90deg_bottom_urans"),
    (120, "h2_ch4_120deg_bottom_verified"),
    (150, "h2_ch4_150deg_bottom_verified"),
]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.5))

at = [a for a, c in top_angles if c in data]
ct = [get(c, "cov_x_10D") for a, c in top_angles if c in data]
ab = [a for a, c in bot_angles if c in data]
cb = [get(c, "cov_x_10D") for a, c in bot_angles if c in data]

ax1.plot(at, ct, "o-", color="#ef4444", lw=2.6, ms=8, label="Top Injection (URANS k-ω SST)")
ax1.plot(ab, cb, "s-", color="#2563eb", lw=2.6, ms=8, label="Bottom Injection (URANS k-ω SST)")
for a, c, lbl, col in [
    (90, "h2_ch4_90deg_bottom_iddes", "Bottom SST-IDDES", "#059669"),
    (120, "h2_ch4_120deg_bot_iddes", None, "#059669"),
    (90, "h2_ch4_90deg_iddes_verified", "Top SST-IDDES", "#b91c1c"),
]:
    if c in data:
        ax1.scatter([a], [get(c, "cov_x_10D")], marker="D", s=95, color=col, zorder=5, label=lbl)
for a, v in zip(at, ct):
    ax1.annotate(f"{v:.2f}", (a, v), textcoords="offset points", xytext=(0, 9), ha="center", fontsize=9.5, color="#b91c1c", fontweight="bold")
for a, v in zip(ab, cb):
    ax1.annotate(f"{v:.2f}", (a, v), textcoords="offset points", xytext=(0, -16), ha="center", fontsize=9.5, color="#1d4ed8", fontweight="bold")

ax1.axhline(0.05, color="#16a34a", ls="--", lw=1.5, label="Industrial Target (CoV = 0.05)")
ax1.set_xlabel("Injection Angle θ [deg]")
ax1.set_ylabel("Outlet CoV of H₂ Mole Fraction  CoV_X(10D)")
ax1.set_title("(a) Mixing Quality vs Injection Angle (VR = 3.2, d/D = 0.25)")
ax1.set_xticks([30, 60, 90, 120, 150])
ax1.set_ylim(0, 1.32)
ax1.legend(loc="upper right", framealpha=0.92)

# Panel (b): Herwig & Schmandt (2011) irreversible pressure drop dP_irr
dpt = [get(c, "dP_irr_kPa") for a, c in top_angles if get(c, "dP_irr_kPa") is not None]
dpb = [get(c, "dP_irr_kPa") for a, c in bot_angles if get(c, "dP_irr_kPa") is not None]
ax2.plot(at, dpt, "o-", color="#ef4444", lw=2.6, ms=8, label="Top Injection (ΔP_irr)")
ax2.plot(ab, dpb, "s-", color="#2563eb", lw=2.6, ms=8, label="Bottom Injection (ΔP_irr)")
for a, v in zip(ab, dpb):
    ax2.annotate(f"{v:.2f}", (a, v), textcoords="offset points", xytext=(0, 9), ha="center", fontsize=9.2, color="#1d4ed8", fontweight="bold")
ax2.set_xlabel("Injection Angle θ [deg]")
ax2.set_ylabel("Irreversible Pressure Drop  ΔP_irr [kPa]")
ax2.set_title("(b) Mechanical-Energy Loss ΔP_irr (Herwig & Schmandt, 2011)")
ax2.set_xticks([30, 60, 90, 120, 150])
ax2.set_ylim(0, 1.55)
ax2.legend(loc="lower right", framealpha=0.92)

fig.suptitle("Full-3D OpenFOAM v13 Angle & Buoyancy Sweep (6.9 MPa Peng–Robinson Real Gas)", fontsize=14, fontweight="bold", y=0.99)
fig.tight_layout(rect=[0, 0, 1, 0.95])
fig.savefig(FIGS / "fig1_angle_sweep_top_vs_bottom.png")
plt.close(fig)

# ==============================================================================
# UPDATED FIG 5: Pareto Front (CoV_X(10D) vs Herwig & Schmandt dP_irr [kPa])
# ==============================================================================
fig, ax = plt.subplots(figsize=(11.0, 6.4))
pts = []
for cname, d in data.items():
    dp = d.get("dP_irr_kPa")
    cov = d.get("cov_x_10D")
    if dp is None or cov is None:
        continue
    # Exclude GCI coarse / Sc_t sensitivity from design Pareto map
    if "gci_" in cname or "sct09" in cname:
        continue
    ori = d["spec"].get("orientation", "top")
    pts.append((dp, cov, cname, ori))

for dp, cov, cname, ori in pts:
    col = "#2563eb" if ori == "bottom" else "#ef4444"
    mk = "s" if ori == "bottom" else "o"
    ax.scatter([dp], [cov], c=col, marker=mk, s=95, edgecolors="white", lw=0.9, zorder=4)
    short = cname.replace("h2_ch4_", "").replace("_verified", "").replace("_urans", "")
    dx, dy = (7, 4)
    if "90deg_top_vr48" in cname:
        dx, dy = (-95, 10)
    elif "90deg_base" in cname:
        dx, dy = (7, 6)
    elif "90deg_bottom" in cname:
        dx, dy = (7, -12)
    ax.annotate(short, (dp, cov), textcoords="offset points", xytext=(dx, dy), fontsize=8.8, alpha=0.92)

# True Pareto frontier (minimize both dP_irr and CoV_X)
pts_sorted = sorted(pts, key=lambda x: x[0])
pareto = []
best_cov = 1e9
for p in pts_sorted:
    if p[1] < best_cov:
        pareto.append(p)
        best_cov = p[1]
if len(pareto) >= 2:
    ax.plot([p[0] for p in pareto], [p[1] for p in pareto], "k--", lw=2.2, alpha=0.8, label="Pareto Frontier (Min ΔP_irr & Min CoV)")

ax.scatter([], [], c="#2563eb", marker="s", s=85, label="Bottom Injection (URANS k-ω SST)")
ax.scatter([], [], c="#ef4444", marker="o", s=85, label="Top Injection (URANS k-ω SST)")
ax.axhline(0.05, color="#16a34a", ls="--", lw=1.5, label="Industrial Target (CoV = 0.05)")
ax.set_xscale("log")
ax.set_xlabel("Irreversible Pressure Drop  ΔP_irr = Φ_loss / Q_out  [kPa, log scale]  (Herwig & Schmandt, 2011)")
ax.set_ylabel("Outlet CoV of H₂ Mole Fraction  CoV_X(10D)")
ax.set_title("Multi-Objective Mixing vs Irreversible Pumping Loss Trade-Off (Full-3D OpenFOAM v13)")
ax.set_ylim(0, 1.68)
ax.legend(loc="upper right", framealpha=0.94)
fig.tight_layout()
fig.savefig(FIGS / "fig5_pareto_CoV_vs_dP.png")
plt.close(fig)

print("UPDATED_FIG1_AND_FIG5_OK")
for cname in sorted(herwig):
    d = data.get(cname, {})
    print(f"  {cname:<34} CoV_X={str(d.get('cov_x_10D')):<7} W={d.get('W_turb_W',0):7.1f} W  dP_irr={d.get('dP_irr_kPa',0):.3f} kPa  K={d.get('K_loss',0):.3f}")
