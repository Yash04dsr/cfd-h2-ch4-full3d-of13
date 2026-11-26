#!/usr/bin/env python3
"""
Physical Reality & Mixing Audit for Full-3D OpenFOAM v13 H2-CH4 T-Junction
(Eames, Austin & Wojcik 2022, Int. J. Hydrogen Energy 47(61):25745-25760).

Uses native OpenFOAM v13 `foamPostProcess` (zero PyVista dependency):
  1. Verifies NIST real-gas PengRobinsonGas densities (CH4 ~54 kg/m3, H2 ~5.5 kg/m3)
  2. Verifies H2 boundedness [0, 1], mass closure, and mass-flux-weighted Y_H2 & X_H2
  3. Computes axial Coefficient of Variation CoV(z/D) on 10 cross-sections (z/D = 1..10)
     for both mass fraction Y_H2 and mole/volume fraction X_H2
  4. Renders a 4-panel publication-grade physical reality & mixing audit figure:
     - (a) Conservation & Pressure time-history
     - (b) Axial CoV(z/D) decay curve (Y_H2 & X_H2) + Eames et al. (2022) 5% mixing threshold
     - (c) Vertical & Horizontal centerline H2 mole-fraction profiles at z/D = 2, 4, 6, 10
     - (d) 6 Full-3D circular cross-section X_H2 contours (z/D = 1, 2, 4, 6, 8, 10)
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

M_H2 = 2.01588
M_CH4 = 16.0425


def y_to_x_h2(y_h2: np.ndarray) -> np.ndarray:
    y = np.clip(y_h2, 0.0, 1.0)
    n_h2 = y / M_H2
    n_ch4 = (1.0 - y) / M_CH4
    return n_h2 / (n_h2 + n_ch4)


def load_sfv(path: Path) -> np.ndarray:
    return np.loadtxt(path, comments="#")


def write_sample_dicts(case_dir: Path, D: float, z_jct: float):
    R = D / 2.0
    # 10 axial cross-sections at z/D = 1..10
    surf_entries = []
    for k in range(1, 11):
        z = z_jct + k * D
        if k == 10:
            z = z_jct + 9.95 * D  # just inside outlet
        surf_entries.append(
            f"    z{k}D {{ type cuttingPlane; planeType pointAndNormal; point (0 0 {z:.6f}); normal (0 0 1); interpolate true; }}"
        )
    surf_dict = f"""
FoamFile
{{
    format      ascii;
    class       dictionary;
    object      sampleH2Slices;
}}
type surfaces;
libs ("libsampling.so");
surfaceFormat raw;
interpolationScheme cellPoint;
fields (H2Mean UMean rho);
surfaces
(
{chr(10).join(surf_entries)}
);
"""
    (case_dir / "system" / "sampleH2Slices").write_text(surf_dict.lstrip())

    # Vertical & horizontal centerline lines at z/D = 2, 4, 6, 9.95
    lines_dict = f"""
FoamFile
{{
    format      ascii;
    class       dictionary;
    object      sampleH2Lines;
}}
type sets;
libs ("libsampling.so");
interpolationScheme cellPoint;
setFormat raw;
fields (H2Mean UMean rho);
sets
(
    v_2D  {{ type lineUniform; axis y; start (0 {-0.97*R:.6f} {z_jct+2.0*D:.6f}); end (0 {0.97*R:.6f} {z_jct+2.0*D:.6f}); nPoints 120; }}
    v_4D  {{ type lineUniform; axis y; start (0 {-0.97*R:.6f} {z_jct+4.0*D:.6f}); end (0 {0.97*R:.6f} {z_jct+4.0*D:.6f}); nPoints 120; }}
    v_6D  {{ type lineUniform; axis y; start (0 {-0.97*R:.6f} {z_jct+6.0*D:.6f}); end (0 {0.97*R:.6f} {z_jct+6.0*D:.6f}); nPoints 120; }}
    v_10D {{ type lineUniform; axis y; start (0 {-0.97*R:.6f} {z_jct+9.95*D:.6f}); end (0 {0.97*R:.6f} {z_jct+9.95*D:.6f}); nPoints 120; }}
    h_2D  {{ type lineUniform; axis x; start ({-0.97*R:.6f} 0 {z_jct+2.0*D:.6f}); end ({0.97*R:.6f} 0 {z_jct+2.0*D:.6f}); nPoints 120; }}
    h_6D  {{ type lineUniform; axis x; start ({-0.97*R:.6f} 0 {z_jct+6.0*D:.6f}); end ({0.97*R:.6f} 0 {z_jct+6.0*D:.6f}); nPoints 120; }}
    h_10D {{ type lineUniform; axis x; start ({-0.97*R:.6f} 0 {z_jct+9.95*D:.6f}); end ({0.97*R:.6f} 0 {z_jct+9.95*D:.6f}); nPoints 120; }}
    inlet_main   {{ type lineUniform; axis y; start (0 {-0.95*R:.6f} {0.5*z_jct:.6f}); end (0 {0.95*R:.6f} {0.5*z_jct:.6f}); nPoints 60; }}
    inlet_branch {{ type lineUniform; axis z; start (0 {0.8*R + 0.8:.6f} {z_jct-0.04:.6f}); end (0 {0.8*R + 0.8:.6f} {z_jct+0.04:.6f}); nPoints 40; }}
);
"""
    (case_dir / "system" / "sampleH2Lines").write_text(lines_dict.lstrip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tag", default="90deg_GCI_Medium_1.34M")
    args = ap.parse_args()

    case_dir = Path(args.case)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = json.loads((case_dir / "casespec.json").read_text())
    D = spec["geometry"]["d1"]
    R = D / 2.0
    z_jct = spec["geometry"]["l_up"]
    avg_start = spec["numerics"]["avg_start"]

    pp = case_dir / "postProcessing"
    m_main = load_sfv(pp / "mainInletFlux/0/surfaceFieldValue.dat")
    m_br = load_sfv(pp / "branchInletFlux/0/surfaceFieldValue.dat")
    m_out = load_sfv(pp / "outletFlux/0/surfaceFieldValue.dat")
    y_out = load_sfv(pp / "outletScalarAvg/0/surfaceFieldValue.dat")
    p_in = load_sfv(pp / "pInletAvg/0/surfaceFieldValue.dat")
    p_out = load_sfv(pp / "pOutletAvg/0/surfaceFieldValue.dat")

    t_arr = m_out[:, 0]
    mask = t_arr >= avg_start
    mdot_in = -(m_main[mask, 1] + m_br[mask, 1])
    mdot_out = m_out[mask, 1]
    mass_err_pct = float(100.0 * (mdot_out.mean() - mdot_in.mean()) / mdot_in.mean())
    y_h2_out_mean = float(y_out[mask, 1].mean())
    y_h2_theory = float((-m_br[mask, 1].mean()) / mdot_in.mean())
    x_h2_out_mean = float(y_to_x_h2(np.array([y_h2_out_mean]))[0])
    x_h2_theory = float(y_to_x_h2(np.array([y_h2_theory]))[0])

    # Run native OpenFOAM v13 sampling (if not already present)
    if not (pp / "sampleH2Slices").exists():
        write_sample_dicts(case_dir, D, z_jct)
        subprocess.run(["foamPostProcess", "-func", "sampleH2Slices", "-latestTime"], cwd=case_dir, check=True)
    if not (pp / "sampleH2Lines").exists():
        write_sample_dicts(case_dir, D, z_jct)
        subprocess.run(["foamPostProcess", "-func", "sampleH2Lines", "-latestTime"], cwd=case_dir, check=True)

    ss_dir = sorted((pp / "sampleH2Slices").iterdir(), key=lambda p: float(p.name))[-1]
    sl_dir = sorted((pp / "sampleH2Lines").iterdir(), key=lambda p: float(p.name))[-1]

    # Check NIST real-gas densities on main_inlet & branch_inlet lines
    # OpenFOAM v13 raw line format: coord H2Mean rho UMean_x UMean_y UMean_z (verify header!)
    def read_line_cols(path: Path):
        hdr = ""
        for line in path.read_text().splitlines():
            if line.startswith("#"):
                hdr = line.lstrip("#").split()
            else:
                break
        arr = np.loadtxt(path, comments="#")
        return {name: arr[:, i] for i, name in enumerate(hdr)}

    in_main = read_line_cols(sl_dir / "inlet_main.xy")
    in_br = read_line_cols(sl_dir / "inlet_branch.xy")
    rho_ch4_cfd = float(np.mean(in_main["rho"]))
    rho_h2_cfd = float(np.mean(in_br["rho"]))

    # Parse 10 axial slices -> compute uniform-area-grid CoV(Y_H2) and CoV(X_H2)
    gx = np.linspace(-R, R, 90)
    gy = np.linspace(-R, R, 90)
    GX, GY = np.meshgrid(gx, gy)
    cmask = (GX ** 2 + GY ** 2) <= (0.99 * R) ** 2

    zd_list = list(range(1, 11))
    cov_y = []
    cov_x = []
    mean_x_list = []
    h2_min_all = 1e9
    h2_max_all = -1e9
    slices_X = {}

    for k in zd_list:
        sf = ss_dir / f"z{k}D.xy"
        cols = read_line_cols(sf)
        x_pts = cols.get("point_x", cols.get("x"))
        y_pts = cols.get("point_y", cols.get("y"))
        h2_pts = cols["H2Mean"]
        h2_min_all = min(h2_min_all, float(np.min(h2_pts)))
        h2_max_all = max(h2_max_all, float(np.max(h2_pts)))

        # Interpolate onto uniform circular grid for unbiased area-weighted CoV
        Y_grid = griddata(np.column_stack([x_pts, y_pts]), h2_pts, (GX, GY), method="linear")
        valid = cmask & np.isfinite(Y_grid)
        Yv = np.clip(Y_grid[valid], 0.0, 1.0)
        Xv = y_to_x_h2(Yv)

        cy = float(np.std(Yv) / np.mean(Yv)) if np.mean(Yv) > 1e-9 else float("nan")
        cx = float(np.std(Xv) / np.mean(Xv)) if np.mean(Xv) > 1e-9 else float("nan")
        cov_y.append(cy)
        cov_x.append(cx)
        mean_x_list.append(float(np.mean(Xv)))

        if k in (1, 2, 4, 6, 8, 10):
            X_grid = y_to_x_h2(np.where(np.isfinite(Y_grid), Y_grid, 0.0))
            X_grid[~cmask] = np.nan
            slices_X[k] = X_grid

    # Vertical & horizontal centerline X_H2 profiles
    v_prof = {}
    for k in (2, 4, 6, 10):
        c = read_line_cols(sl_dir / f"v_{k}D.xy")
        v_prof[k] = (c["y"] / R, 100.0 * y_to_x_h2(c["H2Mean"]))
    h_prof = {}
    for k in (2, 6, 10):
        c = read_line_cols(sl_dir / f"h_{k}D.xy")
        h_prof[k] = (c["x"] / R, 100.0 * y_to_x_h2(c["H2Mean"]))

    # Check left-right asymmetry in full 3D (x < 0 vs x > 0 at z/D = 2 & 6)
    x_h2_2, val_h2_2 = h_prof[2]
    asym_2D_pct = float(np.max(np.abs(val_h2_2 - val_h2_2[::-1])))

    # Build 4-panel Physical Reality & Mixing Audit figure
    plt.rcParams.update({"font.size": 10, "axes.titlesize": 11, "axes.labelsize": 10, "legend.fontsize": 8.5, "figure.dpi": 200})
    fig = plt.figure(figsize=(15.5, 11))
    gs = fig.add_gridspec(2, 2, hspace=0.30, wspace=0.30)

    # Panel (a): Mass Conservation & Pressure Stability
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(t_arr, -(m_main[:, 1] + m_br[:, 1]), "k-", lw=1.8, label="Total Inlet Mass Flow")
    ax1.plot(t_arr, m_out[:, 1], "c-", lw=1.3, alpha=0.85, label="Outlet Mass Flow (waveTransmissive)")
    ax1.axvline(avg_start, color="gray", ls=":", lw=1.2, label=f"Avg Start ({avg_start} s)")
    ax1.set_xlabel("Physical Time t [s]")
    ax1.set_ylabel("Mass Flow Rate [kg/s]")
    ax1.set_title(f"(a) Real-Gas Conservation & Pressure ({args.tag})")
    ax1.set_ylim(75, 115)
    ax1.grid(True, alpha=0.3)
    ax1_r = ax1.twinx()
    ax1_r.plot(y_out[:, 0], 100.0 * y_to_x_h2(y_out[:, 1]), "r-", lw=1.6, label="Outlet X_H2 [% mol]")
    ax1_r.axhline(100.0 * x_h2_theory, color="darkred", ls="--", lw=1.2, label=f"Theory X_H2 = {100*x_h2_theory:.2f}%")
    ax1_r.set_ylabel("Outlet H2 Mole Fraction [%]", color="r")
    ax1_r.tick_params(axis="y", labelcolor="r")
    ax1_r.set_ylim(0, 22)
    l1, lb1 = ax1.get_legend_handles_labels()
    l2, lb2 = ax1_r.get_legend_handles_labels()
    ax1.legend(l1 + l2, lb1 + lb2, loc="lower right", framealpha=0.9, fontsize=8)
    ax1.text(
        0.03, 0.95,
        f"ρ_CH4: {rho_ch4_cfd:.1f} kg/m³ (NIST: 55.1)\n"
        f"ρ_H2:  {rho_h2_cfd:.2f} kg/m³ (NIST: 5.45)\n"
        f"H2 bounds: [{h2_min_all:.4f}, {h2_max_all:.4f}]\n"
        f"Mass err: {mass_err_pct:+.2f}% | Out std: ±{mdot_out.std():.2f} kg/s",
        transform=ax1.transAxes, va="top", fontsize=8.2,
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.88),
    )

    # Panel (b): Axial CoV(z/D) Decay Curve
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.plot(zd_list, cov_x, "bo-", lw=2.2, ms=6, label="CoV of H₂ Mole Fraction $X_{\\mathrm{H2}}$ (Vol %)")
    ax2.plot(zd_list, cov_y, "rs--", lw=1.8, ms=5, label="CoV of H₂ Mass Fraction $Y_{\\mathrm{H2}}$")
    ax2.axhline(0.05, color="green", ls="--", lw=1.4, label="Industrial Mixing Target (CoV = 0.05)")
    ax2.set_xlabel("Downstream Distance z / D")
    ax2.set_ylabel("Coefficient of Variation  CoV = σ / μ")
    ax2.set_title(f"(b) Axial Mixing Decay (CoV at 10D: X={cov_x[-1]:.3f}, Y={cov_y[-1]:.3f})")
    ax2.set_xlim(0.8, 10.3)
    ax2.set_ylim(0.0, max(max(cov_y), max(cov_x)) * 1.15)
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper right", framealpha=0.9)

    # Panel (c): Vertical & Horizontal Centerline H2 Mole Fraction Profiles
    ax3 = fig.add_subplot(gs[1, 0])
    for k, col in [(2, "b"), (4, "g"), (6, "orange"), (10, "r")]:
        yr, xpct = v_prof[k]
        ax3.plot(xpct, yr, color=col, lw=2.0, label=f"Vertical (y/R) at z/D = {k}")
    ax3.axvline(100.0 * x_h2_theory, color="k", ls="--", lw=1.2, label=f"Fully Mixed X_H2 = {100*x_h2_theory:.1f}%")
    ax3.axhline(0.0, color="gray", ls=":", lw=0.8)
    ax3.set_xlabel("H₂ Mole Fraction $X_{\\mathrm{H2}}$ [mol %]")
    ax3.set_ylabel("Vertical Position y / R  (+1 = Top/Branch Wall)")
    ax3.set_title("(c) Vertical H₂ Stratification & Mixing Evolution")
    ax3.grid(True, alpha=0.3)
    ax3.legend(loc="lower right", framealpha=0.9, fontsize=8.5)

    # Panel (d): 6 Full-3D Cross-Sectional X_H2 Contours (2x3 subgrid)
    sub_gs = gs[1, 1].subgridspec(2, 3, wspace=0.14, hspace=0.28)
    im = None
    vmax_pct = max(35.0, float(np.nanpercentile(slices_X[1] * 100.0, 98)))
    for idx, k in enumerate([1, 2, 4, 6, 8, 10]):
        sax = fig.add_subplot(sub_gs[idx // 3, idx % 3])
        im = sax.imshow(100.0 * slices_X[k], extent=[-1, 1, -1, 1], origin="lower", cmap="turbo", vmin=0.0, vmax=vmax_pct)
        sax.add_patch(plt.Circle((0, 0), 1.0, color="k", fill=False, lw=1.1))
        sax.set_title(f"z/D={k} (CoV={cov_x[k-1]:.2f})", fontsize=8.5)
        sax.set_aspect("equal")
        sax.set_xticks([-1, 0, 1])
        sax.set_yticks([-1, 0, 1])
        if idx // 3 == 1:
            sax.set_xlabel("x/R", fontsize=8)
        if idx % 3 == 0:
            sax.set_ylabel("y/R", fontsize=8)
    cbar = fig.colorbar(im, ax=[fig.axes[-6], fig.axes[-5], fig.axes[-4], fig.axes[-3], fig.axes[-2], fig.axes[-1]], fraction=0.046, pad=0.03)
    cbar.set_label(r"$X_{\mathrm{H2}}$ [mol %]")

    fig.suptitle(
        f"Full-3D H₂–CH₄ T-Junction Physical Reality & Mixing Audit (OpenFOAM v13, PengRobinsonGas, {args.tag})",
        fontsize=12.5, fontweight="bold", y=0.98,
    )

    fig_path = out_dir / f"h2_ch4_audit_{args.tag}.png"
    fig.savefig(fig_path, dpi=200, bbox_inches="tight")
    plt.close(fig)

    summary = {
        "case": str(case_dir),
        "tag": args.tag,
        "end_time_s": float(t_arr[-1]),
        "rho_CH4_CFD_kg_m3": round(rho_ch4_cfd, 2),
        "rho_CH4_NIST_kg_m3": 55.1,
        "rho_H2_CFD_kg_m3": round(rho_h2_cfd, 2),
        "rho_H2_NIST_kg_m3": 5.45,
        "H2_min": round(h2_min_all, 6),
        "H2_max": round(h2_max_all, 6),
        "mass_in_kg_s": round(float(mdot_in.mean()), 3),
        "mass_out_kg_s": round(float(mdot_out.mean()), 3),
        "mass_out_std_kg_s": round(float(mdot_out.std()), 3),
        "mass_err_pct": round(mass_err_pct, 3),
        "Y_H2_out_mean": round(y_h2_out_mean, 5),
        "Y_H2_theory": round(y_h2_theory, 5),
        "X_H2_out_mol_pct": round(100.0 * x_h2_out_mean, 2),
        "X_H2_theory_mol_pct": round(100.0 * x_h2_theory, 2),
        "CoV_X_H2_by_zD": {str(k): round(v, 4) for k, v in zip(zd_list, cov_x)},
        "CoV_Y_H2_by_zD": {str(k): round(v, 4) for k, v in zip(zd_list, cov_y)},
        "lateral_asymmetry_2D_mol_pct": round(asym_2D_pct, 3),
        "figure": str(fig_path),
    }
    (out_dir / f"h2_ch4_audit_{args.tag}.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
