#!/usr/bin/env python3
import concurrent.futures, json, re, shutil, subprocess
from pathlib import Path
import numpy as np

UNPACKED_URANS = [
    "h2_ch4_90deg_top_dd035_urans",
    "h2_ch4_90deg_bot_dd035_urans",
    "h2_ch4_30deg_bottom_verified",
    "h2_ch4_60deg_verified",
    "h2_ch4_60deg_bottom_verified",
    "h2_ch4_90deg_base_verified",
    "h2_ch4_90deg_bottom_urans",
    "h2_ch4_120deg_top_verified",
    "h2_ch4_120deg_bottom_verified",
    "h2_ch4_150deg_top_verified",
    "h2_ch4_150deg_bottom_verified",
    "h2_ch4_90deg_top_vr16_urans",
    "h2_ch4_90deg_top_vr48_urans",
    "h2_ch4_90deg_bot_vr16_urans",
    "h2_ch4_90deg_bot_vr24_urans",
    "h2_ch4_90deg_gci_coarse_verified",
    "h2_ch4_90deg_gci_fine_verified",
    "h2_ch4_90deg_sct09_verified",
]

def read_of_scalar(path: Path) -> np.ndarray:
    raw = path.read_bytes()
    m = re.search(rb'internalField\s+nonuniform\s+List<scalar>\s*\n(\d+)\s*\n\(', raw)
    if m:
        n = int(m.group(1))
        if re.search(rb'format\s+binary\s*;', raw[:600]):
            return np.frombuffer(raw, dtype=np.float64, count=n, offset=m.end())
        txt = raw[m.end():].decode("ascii", errors="ignore").split(")")[0]
        return np.fromstring(txt, sep="\n", count=n)
    m2 = re.search(rb'internalField\s+uniform\s+([0-9eE.+\-]+)\s*;', raw)
    if m2:
        return np.array([float(m2.group(1))], dtype=np.float64)
    raise ValueError(f"Cannot parse {path}")

def run_one(cname: str):
    local_case = Path.home() / "cfd_full3d_of13" / "cases" / cname
    work = Path(f"/tmp/hw_{cname}")
    try:
        if local_case.exists() and (local_case / "3" / "k").exists():
            cdir = local_case
            cleanup = False
        else:
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(parents=True)
            src = f"gdrive:CFD_H2_CH4_Full3D_OF13/cases_unpacked/{cname}"
            # Single rclone call pulling ONLY polyMesh, system/controlDict, 3/{k,omega,rhoMean,rho}
            subprocess.run([
                "rclone", "copy", src, str(work),
                "--include", "constant/polyMesh/**",
                "--include", "system/controlDict",
                "--include", "system/fvSchemes",
                "--include", "system/fvSolution",
                "--include", "3/k",
                "--include", "3/omega",
                "--include", "3/rhoMean",
                "--include", "3/rho",
                "--transfers", "6", "--checkers", "6", "--retries", "3"
            ], check=True, timeout=240)
            cdir = work
            cleanup = True

        if not (cdir / "3" / "k").exists():
            return cname, None, "MISSING_3_k_IN_UNPACKED"

        if not (cdir / "3" / "Vc").exists():
            cmd = f". /opt/openfoam13/etc/bashrc >/dev/null 2>&1; cd '{cdir}'; foamPostProcess -func writeCellVolumes -time 3 >/dev/null 2>&1"
            subprocess.run(["bash", "-lc", cmd], check=True, timeout=120)

        d = cdir / "3"
        V = read_of_scalar(d / "Vc")
        rho = read_of_scalar(d / ("rhoMean" if (d / "rhoMean").exists() else "rho"))
        k = read_of_scalar(d / "k")
        omg = read_of_scalar(d / "omega")

        W_turb = float(np.sum(V * rho * 0.09 * k * omg))
        V_tot = float(np.sum(V))
        n_cells = len(V)

        if cleanup and work.exists():
            shutil.rmtree(work)
        return cname, {"W_turb_W": round(W_turb, 1), "V_tot_m3": round(V_tot, 4), "n_cells": int(n_cells)}, None
    except Exception as e:
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        return cname, None, str(e)[:180]

results = {}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
    for cname, res, err in ex.map(run_one, UNPACKED_URANS):
        if res:
            results[cname] = res
            print(f"[OK] {cname:<34} W_turb={res['W_turb_W']:8.1f} W  n={res['n_cells']:,}", flush=True)
        else:
            print(f"[ERR] {cname:<34} {err}", flush=True)

Path("/tmp/herwig_results.json").write_text(json.dumps(results, indent=2))
print(f"HERWIG_FAST_DONE: {len(results)}/{len(UNPACKED_URANS)}")
