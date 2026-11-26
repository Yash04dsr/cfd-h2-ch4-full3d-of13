#!/usr/bin/env python3
"""
Fast, self-contained metric extractor for one completed OpenFOAM v13 H2-CH4 T-junction case.
Computes:
  - Mass conservation & H2 mole-fraction balance over [avg_start, 3.0]
  - Time-averaged pressure drop dP (Pa, kPa)
  - Wall y+ (mean, max)
  - Exact 10-station axial CoV_Y(z/D) and CoV_X(z/D) at z/D = 1..10 via foamPostProcess
Outputs a single JSON line prefixed with METRICS_JSON:
"""
import glob, json, math, os, subprocess, sys
from pathlib import Path
import numpy as np

M_H2 = 2.01588
M_CH4 = 16.0425

def y_to_x(y):
    y = np.clip(y, 0.0, 1.0)
    nh = y / M_H2
    nc = (1.0 - y) / M_CH4
    return nh / (nh + nc)

def load_merged_sfv(pp_dir: Path, func_name: str):
    """Merge surfaceFieldValue.dat across all start-time subdirs (handles restarts cleanly)."""
    base = pp_dir / func_name
    if not base.exists():
        return None
    files = sorted(base.glob("*/surfaceFieldValue.dat"), key=lambda p: float(p.parent.name))
    if not files:
        return None
    rows = {}
    for f in files:
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            try:
                t = float(parts[0])
                vals = [float(x) for x in parts[1:]]
                rows[t] = vals
            except Exception:
                continue
    if not rows:
        return None
    ts = sorted(rows.keys())
    return np.array([[t] + rows[t] for t in ts], dtype=float)

def load_yplus(pp_dir: Path):
    base = pp_dir / "yPlusAudit"
    if not base.exists():
        return None, None
    files = sorted(base.glob("*/yPlus.dat"), key=lambda p: float(p.parent.name))
    if not files:
        return None, None
    last_line = None
    for line in files[-1].read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            last_line = line
    if not last_line:
        return None, None
    parts = last_line.split()
    # Time patch min max average
    try:
        return float(parts[4]), float(parts[3])  # avg, max
    except Exception:
        return None, None

def write_slices_dict(case_dir: Path, D: float, z_jct: float):
    entries = []
    for k in range(1, 11):
        z = z_jct + (9.95 * D if k == 10 else k * D)
        entries.append(
            f"    z{k}D {{ type cuttingPlane; planeType pointAndNormal; point (0 0 {z:.6f}); normal (0 0 1); interpolate true; }}"
        )
    txt = f"""FoamFile
{{
    format      ascii;
    class       dictionary;
    object      sampleH2Slices;
}}
type surfaces;
libs ("libsampling.so");
surfaceFormat raw;
interpolationScheme cellPoint;
fields (H2Mean H2);
surfaces
(
{chr(10).join(entries)}
);
"""
    (case_dir / "system" / "sampleH2Slices").write_text(txt)

def read_raw_slice(path: Path):
    hdr = None
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            hdr = line.lstrip("#").split()
        else:
            break
    arr = np.loadtxt(path, comments="#")
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    return {name: arr[:, i] for i, name in enumerate(hdr)}

def compute_cov_from_slice(path: Path, R: float, field_name: str):
    from scipy.interpolate import griddata
    cols = read_raw_slice(path)
    if field_name not in cols:
        return float("nan"), float("nan"), float("nan")
    x = cols.get("point_x", cols.get("x"))
    y = cols.get("point_y", cols.get("y"))
    v = cols[field_name]
    gx = np.linspace(-R, R, 80)
    gy = np.linspace(-R, R, 80)
    GX, GY = np.meshgrid(gx, gy)
    cmask = (GX**2 + GY**2) <= (0.985 * R)**2
    Yg = griddata(np.column_stack([x, y]), v, (GX, GY), method="linear")
    valid = cmask & np.isfinite(Yg)
    if valid.sum() < 20:
        return float("nan"), float("nan"), float("nan")
    Yv = np.clip(Yg[valid], 0.0, 1.0)
    Xv = y_to_x(Yv)
    cy = float(np.std(Yv) / np.mean(Yv)) if np.mean(Yv) > 1e-9 else float("nan")
    cx = float(np.std(Xv) / np.mean(Xv)) if np.mean(Xv) > 1e-9 else float("nan")
    return cy, cx, float(np.mean(Xv))

def main():
    case_dir = Path(sys.argv[1]).resolve()
    cname = case_dir.name
    spec_path = case_dir / "casespec.json"
    spec = json.loads(spec_path.read_text()) if spec_path.exists() else {}

    geom = spec.get("geometry", {})
    flow = spec.get("flow", {})
    num = spec.get("numerics", {})
    D = float(geom.get("d1", 0.460))
    R = D / 2.0
    z_jct = float(geom.get("l_up", 2.30))
    avg_start = float(num.get("avg_start", 1.5))

    pp = case_dir / "postProcessing"
    m_main = load_merged_sfv(pp, "mainInletFlux")
    m_br = load_merged_sfv(pp, "branchInletFlux")
    m_out = load_merged_sfv(pp, "outletFlux")
    y_out = load_merged_sfv(pp, "outletScalarAvg")
    p_in = load_merged_sfv(pp, "pInletAvg")
    p_out = load_merged_sfv(pp, "pOutletAvg")

    res = {
        "case": cname,
        "spec": {
            "angle_deg": geom.get("angle_deg"),
            "orientation": geom.get("orientation", "bottom" if "bot" in cname else "top"),
            "d1": D,
            "d2": geom.get("d2"),
            "d_ratio": round(geom["d2"] / D, 3) if geom.get("d2") else None,
            "vr": flow.get("vr"),
            "u_main": flow.get("u_main"),
            "u_branch": flow.get("u_branch"),
            "turbulence": spec.get("physics", {}).get("turbulence_model", "SST-IDDES" if "iddes" in cname else "kOmegaSST"),
            "sct": spec.get("physics", {}).get("sct", 0.9 if "sct09" in cname else 0.7),
        }
    }

    if m_out is not None and m_main is not None and m_br is not None:
        t_max = float(m_out[-1, 0])
        res["t_end"] = round(t_max, 4)
        mask_o = m_out[:, 0] >= avg_start
        mask_m = m_main[:, 0] >= avg_start
        mask_b = m_br[:, 0] >= avg_start
        mdot_main = float(-np.mean(m_main[mask_m, 1]))
        mdot_br = float(-np.mean(m_br[mask_b, 1]))
        mdot_in = mdot_main + mdot_br
        mdot_out = float(np.mean(m_out[mask_o, 1]))
        res["mdot_in_kg_s"] = round(mdot_in, 4)
        res["mdot_out_kg_s"] = round(mdot_out, 4)
        res["mass_err_pct"] = round(100.0 * (mdot_out - mdot_in) / mdot_in, 3)
        y_th = mdot_br / mdot_in
        res["x_h2_theory_pct"] = round(100.0 * float(y_to_x(y_th)), 3)

    if y_out is not None:
        mask_y = y_out[:, 0] >= avg_start
        y_m = float(np.mean(y_out[mask_y, 1]))
        res["y_h2_out_mean"] = round(y_m, 5)
        res["x_h2_out_pct"] = round(100.0 * float(y_to_x(y_m)), 3)

    if p_in is not None and p_out is not None:
        mask_pi = p_in[:, 0] >= avg_start
        mask_po = p_out[:, 0] >= avg_start
        dp_pa = float(np.mean(p_in[mask_pi, 1]) - np.mean(p_out[mask_po, 1]))
        res["dp_Pa"] = round(dp_pa, 1)
        res["dp_kPa"] = round(dp_pa / 1000.0, 4)

    yp_avg, yp_max = load_yplus(pp)
    res["yplus_avg"] = round(yp_avg, 2) if yp_avg is not None else None
    res["yplus_max"] = round(yp_max, 2) if yp_max is not None else None

    # Ensure sampleH2Slices at T=3 exists (run foamPostProcess if missing)
    ss_t3 = pp / "sampleH2Slices" / "3"
    if not (ss_t3.exists() and (ss_t3 / "z10D.xy").exists()):
        write_slices_dict(case_dir, D, z_jct)
        if (case_dir / "3").exists():
            cmd = ["bash", "-lc", f". /opt/openfoam13/etc/bashrc >/dev/null 2>&1; cd '{case_dir}'; foamPostProcess -func sampleH2Slices -time 3 >/dev/null 2>&1"]
        elif (case_dir / "processor0" / "3").exists():
            nprocs = len(list(case_dir.glob("processor*")))
            cmd = ["bash", "-lc", f". /opt/openfoam13/etc/bashrc >/dev/null 2>&1; cd '{case_dir}'; mpirun -np {nprocs} --bind-to none --oversubscribe foamPostProcess -parallel -func sampleH2Slices -time 3 >/dev/null 2>&1"]
        else:
            cmd = None
        if cmd:
            subprocess.run(cmd, timeout=180)

    if ss_t3.exists() and (ss_t3 / "z10D.xy").exists():
        cov_y, cov_x, cov_y_inst, cov_x_inst = [], [], [], []
        for k in range(1, 11):
            sf = ss_t3 / f"z{k}D.xy"
            if sf.exists():
                cy, cx, _ = compute_cov_from_slice(sf, R, "H2Mean")
                cyi, cxi, _ = compute_cov_from_slice(sf, R, "H2")
            else:
                cy = cx = cyi = cxi = float("nan")
            cov_y.append(round(cy, 4) if math.isfinite(cy) else None)
            cov_x.append(round(cx, 4) if math.isfinite(cx) else None)
            cov_y_inst.append(round(cyi, 4) if math.isfinite(cyi) else None)
            cov_x_inst.append(round(cxi, 4) if math.isfinite(cxi) else None)
        res["cov_y_mean_1to10D"] = cov_y
        res["cov_x_mean_1to10D"] = cov_x
        res["cov_y_inst_1to10D"] = cov_y_inst
        res["cov_x_inst_1to10D"] = cov_x_inst
        res["cov_x_5D"] = cov_x[4]
        res["cov_x_10D"] = cov_x[9]
        res["cov_y_5D"] = cov_y[4]
        res["cov_y_10D"] = cov_y[9]
        # Interpolate mixing length L_mix / D where CoV_X <= 0.05
        lmix = None
        for idx in range(len(cov_x)):
            v = cov_x[idx]
            if v is not None and v <= 0.05:
                if idx == 0:
                    lmix = 1.0
                else:
                    vprev = cov_x[idx - 1]
                    if vprev is not None and vprev > v:
                        lmix = round(idx + (vprev - 0.05) / (vprev - v), 2)
                    else:
                        lmix = float(idx + 1)
                break
        res["Lmix_D_CoV05"] = lmix

    print("METRICS_JSON:" + json.dumps(res))

if __name__ == "__main__":
    main()
