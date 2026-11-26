#!/usr/bin/env python3
#!/usr/bin/env python3
"""
Post-process Full-3D OpenFOAM v13 OECD/NEA Vattenfall T-junction Validation Case
using native OpenFOAM `postProcess` (zero PyVista dependency!).
Compares directly against Official Report NEA/CSNI/R(2011)5 Figs. 15, 16, 30, 31, 32.
"""
import argparse
import json
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import griddata

# EXACT published OECD/NEA-Vattenfall T-junction benchmark data digitized directly
# from Official Report NEA/CSNI/R(2011)5 (Smith, Mahaffy & Angele, 2011 / NED 2013):
EXP_TC_Z_OVER_D = np.array([2.0, 4.0, 6.0, 8.0])
EXP_TSTAR_TOP = np.array([0.596, 0.528, 0.543, 0.473])     # Fig. 30 (0 deg, top/+y wall)
EXP_TSTAR_SIDE = np.array([0.110, 0.373, 0.435, 0.438])    # Fig. 31 (90 deg, side/±x wall)
EXP_TSTAR_BOTTOM = np.array([0.002, 0.078, 0.255, 0.288])  # Fig. 32 (180 deg, bottom/-y wall)
EXP_TSTAR_ERR = 0.03

EXP_PIV_Y_1P6 = np.array([-0.90, -0.75, -0.55, -0.35, -0.15, 0.00, 0.15, 0.30, 0.45, 0.60, 0.75, 0.85])
EXP_PIV_UZ_1P6 = np.array([1.31, 1.36, 1.39, 1.36, 1.15, 0.78, 0.44, 0.26, 0.19, 0.21, 0.29, 0.31])
EXP_PIV_Y_4P6 = np.array([-0.90, -0.75, -0.60, -0.40, -0.20, 0.00, 0.20, 0.40, 0.60, 0.75, 0.90])
EXP_PIV_UZ_4P6 = np.array([0.98, 1.15, 1.21, 1.17, 1.11, 1.06, 1.01, 1.00, 0.98, 0.97, 0.94])
EXP_PIV_ERR = 0.06


def load_sfv(path: Path) -> np.ndarray:
    return np.loadtxt(path, comments="#")


def write_sample_dicts(case_dir: Path, D: float, z_jct: float):
    R = D / 2.0
    r_tc = R - 0.001
    sets_dict = f"""
FoamFile
{{
    format      ascii;
    class       dictionary;
    object      sampleLines;
}}
type sets;
libs ("libsampling.so");
interpolationScheme cellPoint;
setFormat raw;
fields (UMean TMean TPrime2Mean);
sets
(
    vel_1p6 {{ type lineUniform; axis y; start (0 {-0.96*R:.6f} {z_jct+1.6*D:.6f}); end (0 {0.96*R:.6f} {z_jct+1.6*D:.6f}); nPoints 120; }}
    vel_2p6 {{ type lineUniform; axis y; start (0 {-0.96*R:.6f} {z_jct+2.6*D:.6f}); end (0 {0.96*R:.6f} {z_jct+2.6*D:.6f}); nPoints 120; }}
    vel_3p6 {{ type lineUniform; axis y; start (0 {-0.96*R:.6f} {z_jct+3.6*D:.6f}); end (0 {0.96*R:.6f} {z_jct+3.6*D:.6f}); nPoints 120; }}
    vel_4p6 {{ type lineUniform; axis y; start (0 {-0.96*R:.6f} {z_jct+4.6*D:.6f}); end (0 {0.96*R:.6f} {z_jct+4.6*D:.6f}); nPoints 120; }}
    tc_top   {{ type lineUniform; axis z; start (0 {r_tc:.6f} {z_jct+1.0*D:.6f}); end (0 {r_tc:.6f} {z_jct+9.5*D:.6f}); nPoints 100; }}
    tc_bot   {{ type lineUniform; axis z; start (0 {-r_tc:.6f} {z_jct+1.0*D:.6f}); end (0 {-r_tc:.6f} {z_jct+9.5*D:.6f}); nPoints 100; }}
    tc_right {{ type lineUniform; axis z; start ({r_tc:.6f} 0 {z_jct+1.0*D:.6f}); end ({r_tc:.6f} 0 {z_jct+9.5*D:.6f}); nPoints 100; }}
    tc_left  {{ type lineUniform; axis z; start ({-r_tc:.6f} 0 {z_jct+1.0*D:.6f}); end ({-r_tc:.6f} 0 {z_jct+9.5*D:.6f}); nPoints 100; }}
);
"""
    (case_dir / "system" / "sampleLines").write_text(sets_dict.lstrip())

    surf_dict = f"""
FoamFile
{{
    format      ascii;
    class       dictionary;
    object      sampleSlices;
}}
type surfaces;
libs ("libsampling.so");
surfaceFormat raw;
interpolationScheme cellPoint;
fields (TMean);
surfaces
(
    z1D {{ type cuttingPlane; planeType pointAndNormal; point (0 0 {z_jct+1.0*D:.6f}); normal (0 0 1); interpolate true; }}
    z2D {{ type cuttingPlane; planeType pointAndNormal; point (0 0 {z_jct+2.0*D:.6f}); normal (0 0 1); interpolate true; }}
    z4D {{ type cuttingPlane; planeType pointAndNormal; point (0 0 {z_jct+4.0*D:.6f}); normal (0 0 1); interpolate true; }}
    z6D {{ type cuttingPlane; planeType pointAndNormal; point (0 0 {z_jct+6.0*D:.6f}); normal (0 0 1); interpolate true; }}
);
"""
    (case_dir / "system" / "sampleSlices").write_text(surf_dict.lstrip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--title-tag", default="SpalartAllmarasIDDES, 1.25M cells")
    args = ap.parse_args()

    case_dir = Path(args.case)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = json.loads((case_dir / "casespec.json").read_text())
    D = spec["geometry"]["d1"]
    R = D / 2.0
    z_jct = spec["geometry"]["l_up"]
    T_cold = spec["operating"]["t_main_K"]
    T_hot = spec["operating"]["t_branch_K"]
    dT = T_hot - T_cold

    # 1. Load postProcessing time series
    pp = case_dir / "postProcessing"
    m_main = load_sfv(pp / "mainInletFlux/0/surfaceFieldValue.dat")
    m_br = load_sfv(pp / "branchInletFlux/0/surfaceFieldValue.dat")
    m_out = load_sfv(pp / "outletFlux/0/surfaceFieldValue.dat")
    t_out = load_sfv(pp / "outletScalarAvg/0/surfaceFieldValue.dat")
    p_in = load_sfv(pp / "pInletAvg/0/surfaceFieldValue.dat")
    p_out = load_sfv(pp / "pOutletAvg/0/surfaceFieldValue.dat")

    t_arr = m_out[:, 0]
    mask = t_arr >= spec["numerics"]["avg_start"]
    mdot_in = -(m_main[mask, 1] + m_br[mask, 1])
    mdot_out = m_out[mask, 1]
    mass_err_pct = float(100.0 * (mdot_out.mean() - mdot_in.mean()) / mdot_in.mean())
    T_out_mean = float(t_out[mask, 1].mean())
    T_out_theory = float(
        (-m_main[mask, 1].mean() * T_cold + (-m_br[mask, 1].mean()) * T_hot) / mdot_in.mean()
    )
    T_err_K = T_out_mean - T_out_theory
    dp_mean = float((p_in[mask, 1] - p_out[mask, 1]).mean())
    rho_ref = 997.0
    U_mix = float(mdot_out.mean() / (rho_ref * np.pi * R ** 2))

    # 2. Run native OpenFOAM v13 postProcess to sample lines & slices (if not already sampled)
    if not (pp / "sampleLines").exists():
        write_sample_dicts(case_dir, D, z_jct)
        subprocess.run(["foamPostProcess", "-func", "sampleLines", "-latestTime"], cwd=case_dir, check=True)
    if not (pp / "sampleSlices").exists():
        write_sample_dicts(case_dir, D, z_jct)
        subprocess.run(["foamPostProcess", "-func", "sampleSlices", "-latestTime"], cwd=case_dir, check=True)

    # Locate latest time directory in postProcessing/sampleLines
    sl_times = sorted((pp / "sampleLines").iterdir(), key=lambda p: float(p.name))
    sl_dir = sl_times[-1]
    ss_dir = sorted((pp / "sampleSlices").iterdir(), key=lambda p: float(p.name))[-1]

    # 3. Parse velocity lines (OpenFOAM v13 raw format: y  UMean_x UMean_y UMean_z TMean TPrime2Mean)
    vel_profiles = {}
    y_norm = None
    for tag, zd in [("vel_1p6", 1.6), ("vel_2p6", 2.6), ("vel_3p6", 3.6), ("vel_4p6", 4.6)]:
        arr = np.loadtxt(sl_dir / f"{tag}.xy", comments="#")
        y_norm = arr[:, 0] / R
        vel_profiles[zd] = arr[:, 3] / U_mix

    # 4. Parse thermocouple axial lines (OpenFOAM v13 raw format: z  UMean_x UMean_y UMean_z TMean TPrime2Mean)
    def load_tc(tag):
        arr = np.loadtxt(sl_dir / f"{tag}.xy", comments="#")
        return (arr[:, 0] - z_jct) / D, (arr[:, 4] - T_cold) / dT

    zd_axial, tstar_top = load_tc("tc_top")
    _, tstar_bot = load_tc("tc_bot")
    _, tstar_r = load_tc("tc_right")
    _, tstar_l = load_tc("tc_left")
    tstar_side = 0.5 * (tstar_r + tstar_l)

    cfd_top_exp = np.interp(EXP_TC_Z_OVER_D, zd_axial, tstar_top)
    cfd_side_exp = np.interp(EXP_TC_Z_OVER_D, zd_axial, tstar_side)
    cfd_bot_exp = np.interp(EXP_TC_Z_OVER_D, zd_axial, tstar_bot)
    rmse_tstar = float(np.sqrt(np.mean(np.concatenate([
        (cfd_top_exp - EXP_TSTAR_TOP) ** 2,
        (cfd_side_exp - EXP_TSTAR_SIDE) ** 2,
        (cfd_bot_exp - EXP_TSTAR_BOTTOM) ** 2,
    ]))))

    cfd_uz_1p6_exp = np.interp(EXP_PIV_Y_1P6, y_norm, vel_profiles[1.6])
    cfd_uz_4p6_exp = np.interp(EXP_PIV_Y_4P6, y_norm, vel_profiles[4.6])
    rmse_uz = float(np.sqrt(np.mean(np.concatenate([
        (cfd_uz_1p6_exp - EXP_PIV_UZ_1P6) ** 2,
        (cfd_uz_4p6_exp - EXP_PIV_UZ_4P6) ** 2,
    ]))))

    # 5. Parse cross-section raw slices (x y z TMean) -> 80x80 grid
    gx = np.linspace(-R, R, 90)
    gy = np.linspace(-R, R, 90)
    GX, GY = np.meshgrid(gx, gy)
    circle_mask = (GX ** 2 + GY ** 2) <= (0.995 * R) ** 2
    slices_T = {}
    for tag, zd in [("z1D", 1.0), ("z2D", 2.0), ("z4D", 4.0), ("z6D", 6.0)]:
        sf = ss_dir / f"{tag}.xy"
        raw = np.loadtxt(sf, comments="#")
        ts_grid = griddata(raw[:, :2], (raw[:, 3] - T_cold) / dT, (GX, GY), method="linear")
        ts_grid[~circle_mask] = np.nan
        slices_T[zd] = ts_grid

    # 6. Plot 4-panel figure
    plt.rcParams.update({"font.size": 10, "axes.titlesize": 11, "axes.labelsize": 10, "legend.fontsize": 8.5, "figure.dpi": 200})
    fig = plt.figure(figsize=(15, 10.5))
    gs = fig.add_gridspec(2, 2, hspace=0.30, wspace=0.36)

    # Panel (a)
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(t_arr, -(m_main[:, 1] + m_br[:, 1]), "k-", lw=2.0, label="Total Inlet Mass Flow")
    ax1.plot(t_arr, m_out[:, 1], "c--", lw=1.8, label="Outlet Mass Flow")
    ax1.axvline(spec["numerics"]["avg_start"], color="gray", ls=":", lw=1.2, label=f"Avg Start ({spec['numerics']['avg_start']} s)")
    ax1.set_xlabel("Physical Time t [s]")
    ax1.set_ylabel("Mass Flow Rate [kg/s]")
    ax1.set_title(f"(a) Mass & Energy Closure ({args.title_tag})")
    ax1.set_ylim(14.5, 15.4)
    ax1.grid(True, alpha=0.3)
    ax1_r = ax1.twinx()
    ax1_r.plot(t_out[:, 0], t_out[:, 1], "r-", lw=1.6, label="Outlet T [K]")
    ax1_r.axhline(T_out_theory, color="darkred", ls="--", lw=1.2, label=f"Analytical T = {T_out_theory:.2f} K")
    ax1_r.set_ylabel("Outlet Temperature [K]", color="r")
    ax1_r.tick_params(axis="y", labelcolor="r")
    ax1_r.set_ylim(292.0, 302.0)
    l1, lb1 = ax1.get_legend_handles_labels()
    l2, lb2 = ax1_r.get_legend_handles_labels()
    ax1.legend(l1 + l2, lb1 + lb2, loc="lower right", framealpha=0.9)
    ax1.text(
        0.03, 0.94,
        f"Mass err: {mass_err_pct:+.4f}%\n"
        f"T_out err: {T_err_K:+.4f} K ({100*abs(T_err_K)/T_out_theory:.3f}%)\n"
        f"Δp: {dp_mean:.1f} Pa",
        transform=ax1.transAxes, va="top", fontsize=8.5,
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.85),
    )

    # Panel (b)
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.plot(vel_profiles[1.6], y_norm, "b-", lw=2.0, label="CFD IDDES (z/D = 1.6)")
    ax2.errorbar(EXP_PIV_UZ_1P6, EXP_PIV_Y_1P6, xerr=EXP_PIV_ERR, fmt="bo", ms=5, capsize=2.5, zorder=5, label="NEA/CSNI PIV (z/D = 1.6, Fig. 15)")
    ax2.plot(vel_profiles[4.6], y_norm, "r-", lw=2.0, label="CFD IDDES (z/D = 4.6)")
    ax2.errorbar(EXP_PIV_UZ_4P6, EXP_PIV_Y_4P6, xerr=EXP_PIV_ERR, fmt="rs", ms=5, capsize=2.5, zorder=5, label="NEA/CSNI PIV (z/D = 4.6, Fig. 16)")
    ax2.plot(vel_profiles[2.6], y_norm, "g--", lw=1.2, alpha=0.65, label="CFD IDDES (z/D = 2.6)")
    ax2.plot(vel_profiles[3.6], y_norm, "m:", lw=1.3, alpha=0.75, label="CFD IDDES (z/D = 3.6)")
    ax2.axvline(1.0, color="gray", ls="--", lw=0.9)
    ax2.axhline(0.0, color="gray", ls=":", lw=0.8)
    ax2.set_xlabel(r"Normalized Axial Velocity $U_z / U_{\mathrm{bulk}}$")
    ax2.set_ylabel("Vertical Position y / R  (+1 = Top/Branch Wall)")
    ax2.set_title(f"(b) Vertical Velocity vs. NEA/CSNI PIV (RMSE = {rmse_uz:.3f})")
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="lower left", framealpha=0.9, fontsize=8)

    # Panel (c)
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.plot(zd_axial, tstar_top, "r-", lw=2.0, label="IDDES Top Wall (+y, 0°)")
    ax3.errorbar(EXP_TC_Z_OVER_D, EXP_TSTAR_TOP, yerr=EXP_TSTAR_ERR, fmt="ro", capsize=4, label="Exp. Top 0° (NEA/CSNI Fig. 30)")
    ax3.plot(zd_axial, tstar_side, "g-", lw=2.0, label="IDDES Side Walls (±x, 90°/270°)")
    ax3.errorbar(EXP_TC_Z_OVER_D, EXP_TSTAR_SIDE, yerr=EXP_TSTAR_ERR, fmt="gs", capsize=4, label="Exp. Side 90° (NEA/CSNI Fig. 31)")
    ax3.plot(zd_axial, tstar_bot, "b-", lw=2.0, label="IDDES Bottom Wall (-y, 180°)")
    ax3.errorbar(EXP_TC_Z_OVER_D, EXP_TSTAR_BOTTOM, yerr=EXP_TSTAR_ERR, fmt="b^", capsize=4, label="Exp. Bottom 180° (NEA/CSNI Fig. 32)")
    ax3.axhline(0.40, color="k", ls="--", lw=1.1, label="Mixed Equilibrium T* = 0.40")
    ax3.set_xlabel("Downstream Distance z / D")
    ax3.set_ylabel(r"Normalized Mean Temperature $T^* = (T - T_c)/(T_h - T_c)$")
    ax3.set_title(f"(c) Near-Wall Thermal Mixing vs. Thermocouples (RMSE = {rmse_tstar:.3f})")
    ax3.set_xlim(1.0, 9.5)
    ax3.set_ylim(-0.02, 1.02)
    ax3.grid(True, alpha=0.3)
    ax3.legend(loc="upper right", framealpha=0.9, fontsize=8)

    # Panel (d)
    sub_gs = gs[1, 1].subgridspec(2, 2, wspace=0.15, hspace=0.25)
    im = None
    for idx, zd in enumerate([1.0, 2.0, 4.0, 6.0]):
        sax = fig.add_subplot(sub_gs[idx // 2, idx % 2])
        im = sax.imshow(slices_T[zd], extent=[-1, 1, -1, 1], origin="lower", cmap="RdYlBu_r", vmin=0.0, vmax=1.0)
        sax.add_patch(plt.Circle((0, 0), 1.0, color="k", fill=False, lw=1.2))
        sax.set_title(f"z/D = {zd:.0f}", fontsize=9.5)
        sax.set_aspect("equal")
        sax.set_xticks([-1, 0, 1])
        sax.set_yticks([-1, 0, 1])
        if idx // 2 == 1:
            sax.set_xlabel("x/R", fontsize=8.5)
        if idx % 2 == 0:
            sax.set_ylabel("y/R", fontsize=8.5)
    cbar = fig.colorbar(im, ax=[fig.axes[-4], fig.axes[-3], fig.axes[-2], fig.axes[-1]], fraction=0.046, pad=0.04)
    cbar.set_label(r"$T^*$")

    fig.suptitle(
        f"Tier-1 Experimental Validation: OECD/NEA Vattenfall Full-3D T-Junction (OpenFOAM v13, {args.title_tag})",
        fontsize=13, fontweight="bold", y=0.98,
    )

    fig_path = out_dir / "vattenfall_iddes_validation_4panel.png"
    fig.savefig(fig_path, dpi=200, bbox_inches="tight")
    plt.close(fig)

    summary = {
        "case": str(case_dir),
        "turbulence": args.title_tag,
        "end_time_s": float(t_arr[-1]),
        "avg_start_s": float(spec["numerics"]["avg_start"]),
        "mass_in_kg_s": float(mdot_in.mean()),
        "mass_out_kg_s": float(mdot_out.mean()),
        "mass_closure_err_pct": mass_err_pct,
        "T_out_CFD_K": T_out_mean,
        "T_out_analytical_K": T_out_theory,
        "T_out_err_K": T_err_K,
        "deltaP_Pa": dp_mean,
        "U_mix_m_s": U_mix,
        "PIV_Uz_RMSE": rmse_uz,
        "Thermocouple_Tstar_RMSE": rmse_tstar,
        "cfd_Tstar_at_exp_stations": {
            "z_over_D": EXP_TC_Z_OVER_D.tolist(),
            "top": [round(float(v), 4) for v in cfd_top_exp],
            "side": [round(float(v), 4) for v in cfd_side_exp],
            "bottom": [round(float(v), 4) for v in cfd_bot_exp],
        },
        "figure": str(fig_path),
    }
    (out_dir / "vattenfall_iddes_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
