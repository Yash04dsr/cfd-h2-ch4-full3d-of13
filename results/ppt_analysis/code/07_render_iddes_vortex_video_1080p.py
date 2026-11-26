#!/usr/bin/env python3
"""
101-Frame Live Turbulent Vortex Video Renderer (SST-IDDES, 1080p H.264 MP4).
Reads OpenFOAM `postProcessing/vortexSlices/<time>/*.vtk` (centerplane + +1D + +5D),
computes instantaneous area-weighted CoV on each cross-section, renders 4-panel 1080p
frames in parallel, and encodes a 20 fps MP4 via ffmpeg.

Usage:
  python3 07_render_iddes_vortex_video_1080p.py \
    --case /path/to/case \
    --title "90° Bottom SST-IDDES Live 3D Turbulent Vortex Mixing" \
    --out h2_ch4_90deg_bot_iddes_vortex_1080p.mp4 \
    --flip-y
"""
import argparse, concurrent.futures, glob, os, re, subprocess
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.tri import Triangulation
from matplotlib.patches import Circle

D1 = 0.460
R1 = D1 / 2.0
ZJ = 2.30


def read_vtk_poly(path: str):
    toks = open(path).read().split()
    i = 0
    pts = None
    polys = []
    vals = None
    is_cell = False
    while i < len(toks):
        t = toks[i]
        if t == "POINTS":
            n = int(toks[i + 1]); i += 3
            pts = np.array(toks[i : i + 3 * n], dtype=np.float64).reshape(n, 3)
            i += 3 * n; continue
        elif t == "POLYGONS":
            n_ints = int(toks[i + 2]); i += 3
            end = i + n_ints
            while i < end:
                k = int(toks[i]); i += 1
                polys.append([int(x) for x in toks[i : i + k]])
                i += k
            continue
        elif t == "CELL_DATA":
            is_cell = True; i += 2; continue
        elif t == "POINT_DATA":
            is_cell = False; i += 2; continue
        elif t == "H2":
            n = int(toks[i + 2]); i += 4
            vals = np.array(toks[i : i + n], dtype=np.float64)
            break
        i += 1
    tris = np.array([(p[0], p[j], p[j + 1]) for p in polys for j in range(1, len(p) - 1)], dtype=np.int32)
    cvals = np.array([vals[idx] for idx, p in enumerate(polys) for _ in range(len(p) - 2)]) if is_cell else vals
    return pts, tris, cvals, is_cell


def compute_cov(pts, tris, vals, is_cell):
    p0, p1, p2 = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    areas = 0.5 * np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)
    tvals = vals if is_cell else vals[tris].mean(axis=1)
    A = areas.sum()
    mu = (areas * tvals).sum() / A
    return float(np.sqrt((areas * (tvals - mu) ** 2).sum() / A) / mu) if mu > 1e-12 else 0.0


def render_frame(args):
    idx, t_str, n_tot, base_dir, frame_dir, title, flip_y = args
    t_val = float(t_str)
    pts_c, tris_c, h2_c, ic_c = read_vtk_poly(glob.glob(f"{base_dir}/{t_str}/*centerPlane*.vtk")[0])
    pts_1, tris_1, h2_1, ic_1 = read_vtk_poly(glob.glob(f"{base_dir}/{t_str}/*1D*.vtk")[0])
    pts_5, tris_5, h2_5, ic_5 = read_vtk_poly(glob.glob(f"{base_dir}/{t_str}/*5D*.vtk")[0])
    cov1 = compute_cov(pts_1, tris_1, h2_1, ic_1)
    cov5 = compute_cov(pts_5, tris_5, h2_5, ic_5)

    sgn = -1.0 if flip_y else 1.0
    z_nd = (pts_c[:, 2] - ZJ) / D1
    y_nd = sgn * pts_c[:, 1] / R1

    fig = plt.figure(figsize=(19.2, 10.8), dpi=100, facecolor="#0b1020")
    gs = fig.add_gridspec(2, 3, height_ratios=[1.15, 1.0], width_ratios=[1.35, 1.0, 1.0],
                          left=0.045, right=0.955, top=0.89, bottom=0.07, hspace=0.28, wspace=0.20)
    ax_top = fig.add_subplot(gs[0, :])
    ax_zm = fig.add_subplot(gs[1, 0])
    ax_s1 = fig.add_subplot(gs[1, 1])
    ax_s5 = fig.add_subplot(gs[1, 2])

    def plot_tri(ax, x, y, tris, vals, is_cell):
        tri_obj = Triangulation(x, y, tris)
        if is_cell:
            return ax.tripcolor(tri_obj, facecolors=vals, cmap="inferno", vmin=0.0, vmax=0.25, shading="flat")
        return ax.tripcolor(tri_obj, vals, cmap="inferno", vmin=0.0, vmax=0.25, shading="gouraud")

    for ax in [ax_top, ax_zm, ax_s1, ax_s5]:
        ax.set_facecolor("#0f172a")
        ax.tick_params(colors="#cbd5e1", labelsize=10)
        for sp in ax.spines.values():
            sp.set_color("#334155")

    im = plot_tri(ax_top, z_nd, y_nd, tris_c, h2_c, ic_c)
    ax_top.set_xlim(-1.5, 10.2)
    ax_top.set_ylim(-2.6, 1.15) if flip_y else ax_top.set_ylim(-1.15, 2.6)
    ax_top.axhline(1.0, color="#64748b", lw=1.0, alpha=0.6)
    ax_top.axhline(-1.0, color="#64748b", lw=1.0, alpha=0.6)
    ax_top.axvline(1.0, color="#38bdf8", ls="--", lw=1.2, alpha=0.7)
    ax_top.axvline(5.0, color="#34d399", ls="--", lw=1.2, alpha=0.7)
    ax_top.set_xlabel("Downstream Distance  (z − z_junc) / D₁", color="#e2e8f0", fontsize=11.5)
    ax_top.set_ylabel("Vertical  y / R₁  (↑ Buoyancy)", color="#e2e8f0", fontsize=11.5)
    ax_top.set_title("Full Center-Plane Instantaneous H₂ Mass Fraction", color="#f8fafc", fontsize=13, fontweight="bold", pad=8)

    plot_tri(ax_zm, z_nd, y_nd, tris_c, h2_c, ic_c)
    ax_zm.set_xlim(-1.2, 5.2)
    ax_zm.set_ylim(-2.4, 1.15) if flip_y else ax_zm.set_ylim(-1.15, 2.4)
    ax_zm.set_xlabel("(z − z_junc) / D₁", color="#e2e8f0", fontsize=11)
    ax_zm.set_ylabel("y / R₁  (↑ Buoyancy)", color="#e2e8f0", fontsize=11)
    ax_zm.set_title("Near-Field Kelvin–Helmholtz & Vortex Zoom", color="#f8fafc", fontsize=12, fontweight="bold")

    for ax, pts_s, tris_s, h2_s, ic_s, lbl, cov_v, col in [
        (ax_s1, pts_1, tris_1, h2_1, ic_1, "z = +1D Cross-Section", cov1, "#38bdf8"),
        (ax_s5, pts_5, tris_5, h2_5, ic_5, "z = +5D Cross-Section", cov5, "#34d399"),
    ]:
        plot_tri(ax, pts_s[:, 0] / R1, sgn * pts_s[:, 1] / R1, tris_s, h2_s, ic_s)
        ax.add_patch(Circle((0, 0), 1.0, fill=False, ec="#94a3b8", lw=1.6))
        ax.set_aspect("equal"); ax.set_xlim(-1.12, 1.12); ax.set_ylim(-1.12, 1.12)
        ax.set_xlabel("x / R₁", color="#e2e8f0", fontsize=11)
        ax.set_ylabel("y / R₁", color="#e2e8f0", fontsize=11)
        ax.set_title(f"{lbl}   |   CoV = {cov_v:.3f}", color=col, fontsize=12, fontweight="bold")

    cbar = fig.colorbar(im, ax=[ax_top, ax_s5], fraction=0.018, pad=0.015)
    cbar.set_label("H₂ Mass Fraction  Y_H₂  (clipped at 0.25)", color="#e2e8f0", fontsize=11)
    cbar.ax.tick_params(colors="#cbd5e1")
    fig.suptitle(f"{title}  |  6.9 MPa Peng–Robinson  |  t = {t_val:.3f} s  (Frame {idx+1:03d}/{n_tot})",
                 color="#ffffff", fontsize=15.5, fontweight="bold", y=0.955)
    fig.savefig(f"{frame_dir}/frame_{idx:04d}.png", dpi=100, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--flip-y", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    base_dir = str(Path(args.case) / "postProcessing" / "vortexSlices")
    frame_dir = "/tmp/vid_frames"
    os.makedirs(frame_dir, exist_ok=True)
    times = sorted([d for d in os.listdir(base_dir) if re.match(r"^[0-9]", d)], key=float)
    tasks = [(i, t, len(times), base_dir, frame_dir, args.title, args.flip_y) for i, t in enumerate(times)]
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(render_frame, tasks))
    subprocess.run([
        "ffmpeg", "-y", "-framerate", "20", "-i", f"{frame_dir}/frame_%04d.png",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", args.out
    ], check=True)
    print(f"Encoded {args.out} ({len(times)} frames)")


if __name__ == "__main__":
    main()
