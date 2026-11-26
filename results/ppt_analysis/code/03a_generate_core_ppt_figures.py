#!/usr/bin/env python3
"""
Generate publication-grade 300-DPI PPT analysis figures & master CSV table
from ~/CFD_Analysis_PPT/all_cases_summary.json.
"""
import csv, json, math
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent.parent
FIGS = OUT / "figures"
FIGS.mkdir(parents=True, exist_ok=True)

data = json.loads((OUT / "all_cases_summary.json").read_text())

# 1. Write clean master CSV for PPT tables / Excel
csv_path = OUT / "all_cases_summary.csv"
cols = [
    "case", "angle_deg", "orientation", "d_ratio", "vr", "turbulence", "sct",
    "cov_x_5D", "cov_x_10D", "cov_y_5D", "cov_y_10D", "Lmix_D_CoV05",
    "dp_kPa", "mass_err_pct", "x_h2_out_pct", "x_h2_theory_pct", "yplus_avg"
]
with csv_path.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(cols)
    for cname in sorted(data):
        d = data[cname]
        s = d["spec"]
        w.writerow([
            cname, s.get("angle_deg"), s.get("orientation"), s.get("d_ratio"),
            s.get("vr"), s.get("turbulence"), s.get("sct"),
            d.get("cov_x_5D"), d.get("cov_x_10D"), d.get("cov_y_5D"), d.get("cov_y_10D"),
            d.get("Lmix_D_CoV05"), d.get("dp_kPa"), d.get("mass_err_pct"),
            d.get("x_h2_out_pct"), d.get("x_h2_theory_pct"), d.get("yplus_avg")
        ])

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.labelsize": 11.5,
    "legend.fontsize": 9.5,
    "figure.dpi": 300,
    "axes.grid": True,
    "grid.alpha": 0.28,
})

# Helper
def get(c, k, default=None):
    return data.get(c, {}).get(k, default)

# ==============================================================================
# FIG 1: Angle Sweep (Top vs Bottom Injection) — CoV_X(10D) & dP vs Angle
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

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.4))

at = [a for a, c in top_angles if c in data]
ct = [get(c, "cov_x_10D") for a, c in top_angles if c in data]
ab = [a for a, c in bot_angles if c in data]
cb = [get(c, "cov_x_10D") for a, c in bot_angles if c in data]

ax1.plot(at, ct, "o-", color="#ef4444", lw=2.6, ms=8, label="Top Injection (URANS k-ω SST)")
ax1.plot(ab, cb, "s-", color="#2563eb", lw=2.6, ms=8, label="Bottom Injection (URANS k-ω SST)")
# Overlay SST-IDDES points
for a, c, lbl in [(90, "h2_ch4_90deg_bottom_iddes", "Bottom SST-IDDES"), (120, "h2_ch4_120deg_bot_iddes", None)]:
    if c in data:
        ax1.scatter([a], [get(c, "cov_x_10D")], marker="D", s=95, color="#059669", zorder=5, label=lbl)

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

# Panel (b): Pressure drop vs Angle
dpt = [get(c, "dp_kPa") for a, c in top_angles if c in data]
dpb = [get(c, "dp_kPa") for a, c in bot_angles if c in data]
ax2.plot(at, dpt, "o-", color="#ef4444", lw=2.6, ms=8, label="Top Injection")
ax2.plot(ab, dpb, "s-", color="#2563eb", lw=2.6, ms=8, label="Bottom Injection")
ax2.set_xlabel("Injection Angle θ [deg]")
ax2.set_ylabel("Time-Averaged Pressure Drop  ΔP [kPa]")
ax2.set_title("(b) Pressure Drop Penalty vs Injection Angle")
ax2.set_xticks([30, 60, 90, 120, 150])
ax2.set_ylim(0, 2.2)
ax2.legend(loc="lower right", framealpha=0.92)

fig.suptitle("Full-3D OpenFOAM v13 Angle & Buoyancy Sweep (6.9 MPa Peng–Robinson Real Gas)", fontsize=14, fontweight="bold", y=0.99)
fig.tight_layout(rect=[0, 0, 1, 0.95])
fig.savefig(FIGS / "fig1_angle_sweep_top_vs_bottom.png")
plt.close(fig)

# ==============================================================================
# FIG 2: 4-Panel Axial Mixing Decay CoV_X(z/D)
# ==============================================================================
zd = np.arange(1, 11)
fig, axes = plt.subplots(2, 2, figsize=(14.5, 10.2))

panels = [
    (axes[0, 0], "(a) Angle Sweep — Top Injection (VR=3.2)", [
        ("h2_ch4_30deg_top_verified",    "30° Top",             "#10b981", "p-"),
        ("h2_ch4_60deg_verified",        "60° Top",             "#f59e0b", "o-"),
        ("h2_ch4_90deg_base_verified",   "90° Top (URANS)",     "#ef4444", "s-"),
        ("h2_ch4_90deg_iddes_verified",  "90° Top (SST-IDDES)", "#991b1b", "D--"),
        ("h2_ch4_120deg_top_verified",   "120° Top",            "#8b5cf6", "^-"),
        ("h2_ch4_150deg_top_verified",   "150° Top",            "#ec4899", "v-"),
    ]),
    (axes[0, 1], "(b) Angle Sweep — Bottom Injection (VR=3.2)", [
        ("h2_ch4_30deg_bottom_verified",  "30° Bottom (Best Angle)", "#059669", "o-"),
        ("h2_ch4_60deg_bottom_verified",  "60° Bottom",              "#0284c7", "s-"),
        ("h2_ch4_90deg_bottom_urans",     "90° Bottom (URANS)",      "#2563eb", "^-"),
        ("h2_ch4_90deg_bottom_iddes",     "90° Bottom (SST-IDDES)",  "#1e3a8a", "D--"),
        ("h2_ch4_120deg_bottom_verified", "120° Bottom",             "#7c3aed", "v-"),
        ("h2_ch4_150deg_bottom_verified", "150° Bottom",             "#db2777", "p-"),
    ]),
    (axes[1, 0], "(c) Velocity Ratio Sweep (90°, d/D=0.25)", [
        ("h2_ch4_90deg_top_vr16_urans",  "Top VR = 1.6",            "#fca5a5", "o--"),
        ("h2_ch4_90deg_base_verified",   "Top VR = 3.2",            "#ef4444", "s-"),
        ("h2_ch4_90deg_top_vr48_urans",  "Top VR = 4.8 (URANS)",    "#991b1b", "^-"),
        ("h2_ch4_90deg_top_vr48_iddes",  "Top VR = 4.8 (IDDES)",    "#059669", "D-"),
        ("h2_ch4_90deg_bot_vr16_urans",  "Bot VR = 1.6",            "#93c5fd", "o:"),
        ("h2_ch4_90deg_bot_vr24_urans",  "Bot VR = 2.4",            "#3b82f6", "s:"),
        ("h2_ch4_90deg_bottom_urans",    "Bot VR = 3.2",            "#1d4ed8", "^-"),
        ("h2_ch4_90deg_bot_vr48_urans",  "Bot VR = 4.8 (URANS)",    "#1e3a8a", "v-"),
    ]),
    (axes[1, 1], "(d) Nozzle Diameter Ratio Sweep (90° Bottom & Top)", [
        ("h2_ch4_90deg_bot_dd018_urans", "Bot d/D = 0.18 (URANS)",  "#059669", "o-"),
        ("h2_ch4_90deg_top_dd018_iddes", "Top d/D = 0.18 (IDDES)",  "#0f766e", "D--"),
        ("h2_ch4_90deg_bottom_urans",    "Bot d/D = 0.25",          "#2563eb", "s-"),
        ("h2_ch4_90deg_bot_dd030_urans", "Bot d/D = 0.30",          "#6366f1", "^-"),
        ("h2_ch4_90deg_bot_dd035_urans", "Bot d/D = 0.35",          "#8b5cf6", "v-"),
        ("h2_ch4_90deg_base_verified",   "Top d/D = 0.25",          "#ef4444", "s--"),
        ("h2_ch4_90deg_top_dd035_urans", "Top d/D = 0.35",          "#f97316", "v--"),
    ]),
]

for ax, title, series in panels:
    for cname, lbl, col, sty in series:
        arr = get(cname, "cov_x_mean_1to10D")
        if arr:
            ax.plot(zd, arr, sty, color=col, lw=2.2, ms=5.5, label=lbl)
    ax.axhline(0.05, color="#16a34a", ls="--", lw=1.4, label="Target CoV = 0.05")
    ax.set_title(title)
    ax.set_xlabel("Downstream Distance  z / D₁")
    ax.set_ylabel("CoV of H₂ Mole Fraction  CoV_X")
    ax.set_xlim(0.8, 10.2)
    ax.set_ylim(0, None)
    ax.legend(loc="upper right", fontsize=8.5, framealpha=0.9)

fig.suptitle("Axial Mixing Decay Along Pipeline (z/D₁ = 1 to 10) — Full-3D OpenFOAM v13", fontsize=14.5, fontweight="bold", y=0.99)
fig.tight_layout(rect=[0, 0, 1, 0.96])
fig.savefig(FIGS / "fig2_axial_CoV_decay_4panel.png")
plt.close(fig)

# ==============================================================================
# FIG 3: Velocity Ratio Power-Law Scaling (CoV vs VR) + Diameter Ratio (d/D)
# ==============================================================================
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.4))

vr_top = [(1.6, "h2_ch4_90deg_top_vr16_urans"), (3.2, "h2_ch4_90deg_base_verified"), (4.8, "h2_ch4_90deg_top_vr48_urans")]
vr_bot = [(1.6, "h2_ch4_90deg_bot_vr16_urans"), (2.4, "h2_ch4_90deg_bot_vr24_urans"), (3.2, "h2_ch4_90deg_bottom_urans"), (4.8, "h2_ch4_90deg_bot_vr48_urans")]

vt_x = [v for v, c in vr_top if c in data]
vt_y = [get(c, "cov_x_10D") for v, c in vr_top if c in data]
vb_x = [v for v, c in vr_bot if c in data]
vb_y = [get(c, "cov_x_10D") for v, c in vr_bot if c in data]

ax1.plot(vt_x, vt_y, "o-", color="#ef4444", lw=2.6, ms=8, label="90° Top (URANS)")
ax1.plot(vb_x, vb_y, "s-", color="#2563eb", lw=2.6, ms=8, label="90° Bottom (URANS)")
if "h2_ch4_90deg_iddes_verified" in data:
    ax1.scatter([3.2], [get("h2_ch4_90deg_iddes_verified", "cov_x_10D")], marker="D", s=95, color="#b91c1c", zorder=5, label="90° Top VR=3.2 (SST-IDDES)")
if "h2_ch4_90deg_top_vr48_iddes" in data:
    ax1.scatter([4.8], [get("h2_ch4_90deg_top_vr48_iddes", "cov_x_10D")], marker="D", s=95, color="#059669", zorder=5, label="90° Top VR=4.8 (SST-IDDES)")
if "h2_ch4_90deg_bottom_iddes" in data:
    ax1.scatter([3.2], [get("h2_ch4_90deg_bottom_iddes", "cov_x_10D")], marker="D", s=95, color="#0d9488", zorder=5, label="90° Bot VR=3.2 (SST-IDDES)")

ax1.axhline(0.05, color="#16a34a", ls="--", lw=1.5, label="Industrial Target (CoV = 0.05)")
ax1.set_yscale("log")
ax1.set_xlabel("Velocity Ratio  VR = U_branch / U_main")
ax1.set_ylabel("Outlet CoV_X(10D)  [log scale]")
ax1.set_title("(a) Velocity Ratio Effect (90°, d/D = 0.25)")
ax1.set_xticks([1.6, 2.4, 3.2, 4.8])
ax1.legend(loc="lower left", framealpha=0.92)

# Panel (b): Diameter Ratio d/D
dd_bot = [
    (0.18, "h2_ch4_90deg_bot_dd018_urans"),
    (0.25, "h2_ch4_90deg_bottom_urans"),
    (0.30, "h2_ch4_90deg_bot_dd030_urans"),
    (0.35, "h2_ch4_90deg_bot_dd035_urans"),
]
dd_top = [
    (0.25, "h2_ch4_90deg_base_verified"),
    (0.35, "h2_ch4_90deg_top_dd035_urans"),
]
db_x = [d for d, c in dd_bot if c in data]
db_y = [get(c, "cov_x_10D") for d, c in dd_bot if c in data]
dt_x = [d for d, c in dd_top if c in data]
dt_y = [get(c, "cov_x_10D") for d, c in dd_top if c in data]

ax2.plot(db_x, db_y, "s-", color="#2563eb", lw=2.6, ms=8, label="90° Bottom (URANS)")
ax2.plot(dt_x, dt_y, "o-", color="#ef4444", lw=2.6, ms=8, label="90° Top (URANS)")
if "h2_ch4_90deg_top_dd018_iddes" in data:
    v_id = get("h2_ch4_90deg_top_dd018_iddes", "cov_x_10D")
    ax2.scatter([0.18], [v_id], marker="D", s=105, color="#059669", zorder=6, label="90° Top d/D=0.18 (SST-IDDES)")
    ax2.annotate(f"{v_id:.4f} (IDDES)", (0.18, v_id), textcoords="offset points", xytext=(8, -14), ha="left", fontsize=9.0, color="#047857", fontweight="bold")
for x, y in zip(db_x, db_y):
    ax2.annotate(f"{y:.3f}", (x, y), textcoords="offset points", xytext=(0, 10), ha="center", fontsize=9.5, color="#1d4ed8", fontweight="bold")
for x, y in zip(dt_x, dt_y):
    ax2.annotate(f"{y:.2f}", (x, y), textcoords="offset points", xytext=(0, 9), ha="center", fontsize=9.5, color="#b91c1c", fontweight="bold")
ax2.axhline(0.05, color="#16a34a", ls="--", lw=1.5, label="Industrial Target (CoV = 0.05)")
ax2.set_xlabel("Nozzle Diameter Ratio  d₂ / D₁  (constant H₂ mass flow)")
ax2.set_ylabel("Outlet CoV_X(10D)")
ax2.set_title("(b) Nozzle Diameter Ratio Effect (Smaller d/D → Higher Jet Momentum)")
ax2.set_xticks([0.18, 0.25, 0.30, 0.35])
ax2.set_ylim(0, 1.50)
ax2.legend(loc="upper left", framealpha=0.92)

fig.suptitle("Parametric Momentum & Geometry Scaling — Full-3D OpenFOAM v13", fontsize=14, fontweight="bold", y=0.99)
fig.tight_layout(rect=[0, 0, 1, 0.95])
fig.savefig(FIGS / "fig3_VR_and_dD_scaling.png")
plt.close(fig)

# ==============================================================================
# FIG 4: Turbulence Model Verification (URANS vs SST-IDDES) + GCI + Sc_t
# ==============================================================================
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.4))

# Panel (a): URANS vs SST-IDDES direct pairs (ALL 4 matched pairs!)
pairs = [
    ("90° Top\n(VR=3.2, d/D=0.25)",    "h2_ch4_90deg_base_verified",     "h2_ch4_90deg_iddes_verified"),
    ("90° Bottom\n(VR=3.2, d/D=0.25)", "h2_ch4_90deg_bottom_urans",      "h2_ch4_90deg_bottom_iddes"),
    ("120° Bottom\n(VR=3.2, d/D=0.25)","h2_ch4_120deg_bottom_verified",  "h2_ch4_120deg_bot_iddes"),
    ("90° Top\n(VR=4.8, d/D=0.25)",    "h2_ch4_90deg_top_vr48_urans",    "h2_ch4_90deg_top_vr48_iddes"),
]
x = np.arange(len(pairs))
w = 0.35
u_vals = [get(u, "cov_x_10D") for _, u, _ in pairs]
i_vals = [get(i, "cov_x_10D") for _, _, i in pairs]

b1 = ax1.bar(x - w/2, u_vals, w, color="#3b82f6", label="URANS (k-ω SST)")
b2 = ax1.bar(x + w/2, i_vals, w, color="#059669", label="Scale-Resolving (SST-IDDES)")
for bar in list(b1) + list(b2):
    h = bar.get_height()
    ax1.annotate(f"{h:.3f}", (bar.get_x() + bar.get_width()/2, h), xytext=(0, 4),
                 textcoords="offset points", ha="center", fontsize=9.0, fontweight="bold")
ax1.axhline(0.05, color="#16a34a", ls="--", lw=1.4, label="Target CoV = 0.05")
ax1.set_xticks(x)
ax1.set_xticklabels([p[0] for p in pairs], fontsize=9.2)
ax1.set_ylabel("Outlet CoV_X(10D)")
ax1.set_title("(a) URANS vs SST-IDDES Verification (+ 90° Top d/D=0.18 IDDES: 0.0155)")
ax1.set_ylim(0, 1.35)
ax1.legend(loc="upper right", framealpha=0.92)

# Panel (b): GCI Mesh Independence & Sc_t Sensitivity (90° Top Base)
ver_cases = [
    ("GCI Coarse\n(~0.7M)",     "h2_ch4_90deg_gci_coarse_verified", "#94a3b8"),
    ("GCI Medium\n(~1.34M)",    "h2_ch4_90deg_base_verified",       "#2563eb"),
    ("GCI Fine\n(~2.6M)",       "h2_ch4_90deg_gci_fine_verified",   "#1e3a8a"),
    ("Sc_t = 0.9\n(vs 0.7)",    "h2_ch4_90deg_sct09_verified",      "#f59e0b"),
]
vx = np.arange(len(ver_cases))
vy = [get(c, "cov_x_10D") for _, c, _ in ver_cases]
vc = [col for _, _, col in ver_cases]
bars = ax2.bar(vx, vy, width=0.52, color=vc)
for bar in bars:
    h = bar.get_height()
    ax2.annotate(f"{h:.3f}", (bar.get_x() + bar.get_width()/2, h), xytext=(0, 5),
                 textcoords="offset points", ha="center", fontsize=10, fontweight="bold")
ax2.set_xticks(vx)
ax2.set_xticklabels([v[0] for v in ver_cases])
ax2.set_ylabel("Outlet CoV_X(10D)")
ax2.set_title("(b) Grid Independence (Celik GCI) & Sc_t Sensitivity (90° Top)")
ax2.set_ylim(0, 1.35)

fig.suptitle("Verification & Credibility Audit — Scale-Resolving IDDES, Mesh GCI & Sc_t", fontsize=14, fontweight="bold", y=0.99)
fig.tight_layout(rect=[0, 0, 1, 0.95])
fig.savefig(FIGS / "fig4_verification_IDDES_GCI_Sct.png")
plt.close(fig)

# ==============================================================================
# FIG 5: Multi-Objective Trade-Off Map (CoV_X(10D) vs dP) for Positive-dP Cases
# ==============================================================================
fig, ax = plt.subplots(figsize=(10.5, 6.2))
pts = []
for cname, d in data.items():
    dp = d.get("dp_kPa")
    cov = d.get("cov_x_10D")
    if dp is None or cov is None or dp <= 0:
        continue
    ori = d["spec"].get("orientation", "top")
    turb = d["spec"].get("turbulence", "kOmegaSST")
    pts.append((dp, cov, cname, ori, turb))

for dp, cov, cname, ori, turb in pts:
    is_iddes = "IDDES" in str(turb).upper() or "iddes" in cname
    col = "#059669" if is_iddes else ("#2563eb" if ori == "bottom" else "#ef4444")
    mk = "D" if is_iddes else ("s" if ori == "bottom" else "o")
    ax.scatter([dp], [cov], c=col, marker=mk, s=90, edgecolors="white", lw=0.8, zorder=4)
    short = cname.replace("h2_ch4_", "").replace("_verified", "").replace("_urans", "")
    ax.annotate(short, (dp, cov), textcoords="offset points", xytext=(6, 4), fontsize=8.2, alpha=0.88)

# Compute Pareto frontier (minimize both dP and CoV)
pts_sorted = sorted(pts, key=lambda x: x[0])
pareto = []
best_cov = 1e9
for p in pts_sorted:
    if p[1] < best_cov:
        pareto.append(p)
        best_cov = p[1]
if len(pareto) >= 2:
    ax.plot([p[0] for p in pareto], [p[1] for p in pareto], "k--", lw=2.0, alpha=0.75, label="Pareto Frontier (Min ΔP & Min CoV)")

ax.scatter([], [], c="#2563eb", marker="s", s=80, label="Bottom Injection (URANS)")
ax.scatter([], [], c="#ef4444", marker="o", s=80, label="Top Injection (URANS)")
ax.scatter([], [], c="#059669", marker="D", s=80, label="SST-IDDES (Turbulence-Resolving)")
ax.axhline(0.05, color="#16a34a", ls="--", lw=1.4, label="Industrial Target (CoV = 0.05)")
ax.set_xlabel("Time-Averaged Pressure Drop  ΔP [kPa]")
ax.set_ylabel("Outlet CoV of H₂ Mole Fraction  CoV_X(10D)")
ax.set_title("Multi-Objective Mixing vs Pressure Drop Trade-Off (Full-3D OpenFOAM v13)")
ax.legend(loc="upper right", framealpha=0.92)
fig.tight_layout()
fig.savefig(FIGS / "fig5_pareto_CoV_vs_dP.png")
plt.close(fig)

print("ALL_FIGURES_GENERATED_OK")
for p in sorted(FIGS.glob("*.png")):
    print(f"  {p} ({p.stat().st_size/1024:.0f} KB)")
print(f"  {csv_path}")
