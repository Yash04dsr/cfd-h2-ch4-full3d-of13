#!/usr/bin/env python3
"""
Watertight Full-3D T/Y-Junction CAD & STL Generator using Gmsh OpenCASCADE.

Guarantees:
- Full 3D domain (NO symmetry plane)
- Exact OpenCASCADE Boolean union of main + branch cylinders at any angle alpha
- Zero open edges, zero non-manifold edges, outward-consistent normals
- Automated watertightness + volume verification via trimesh
"""
import argparse
import json
import math
import os
from pathlib import Path

import gmsh
import numpy as np
import trimesh


def build_tjunction_stl(
    out_dir: Path,
    d1: float,
    d2: float,
    l_up: float,
    l_down: float,
    l_branch: float,
    alpha_deg: float = 90.0,
    mesh_size_factor: float = 0.04,
):
    """
    Main pipe along +z from z=0 to z=l_up+l_down, junction center at (0, 0, l_up).
    Branch axis points from (0, 0, l_up) toward inlet along S_HAT = (0, sin(alpha), -cos(alpha)).
    alpha_deg = 90 => perpendicular T-junction (+y branch).
    alpha_deg = 60 => 60-deg Y-junction tilted upstream.
    Total branch length from main pipe centerline = l_branch (so protrusion above wall is l_branch - R1/sin(alpha)).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    r1 = d1 / 2.0
    r2 = d2 / 2.0
    l_main = l_up + l_down
    z_jct = l_up
    alpha = math.radians(alpha_deg)
    sin_a = math.sin(alpha)
    cos_a = math.cos(alpha)
    s_hat = np.array([0.0, sin_a, -cos_a], dtype=float)
    branch_tip = np.array([0.0, 0.0, z_jct]) + l_branch * s_hat

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("tjunction_full3d")

    # 1. Exact OpenCASCADE cylinders
    main_tag = gmsh.model.occ.addCylinder(0.0, 0.0, 0.0, 0.0, 0.0, l_main, r1)
    branch_tag = gmsh.model.occ.addCylinder(
        0.0, 0.0, z_jct,
        l_branch * s_hat[0], l_branch * s_hat[1], l_branch * s_hat[2],
        r2,
    )

    # 2. Exact Boolean union (watertight single solid)
    fused, _ = gmsh.model.occ.fuse([(3, main_tag)], [(3, branch_tag)])
    gmsh.model.occ.synchronize()
    assert len(fused) == 1, f"Expected 1 fused solid, got {len(fused)}"
    cad_vol = gmsh.model.occ.getMass(3, fused[0][1])

    # 3. Classify boundary surfaces by centroid / geometry
    surfs = gmsh.model.getBoundary(fused, oriented=False, recursive=False)
    main_inlet_tags = []
    outlet_tags = []
    branch_inlet_tags = []
    wall_tags = []

    tol = 1e-4 * d1
    for dim, stag in surfs:
        com = np.array(gmsh.model.occ.getCenterOfMass(dim, stag))
        # Check main inlet plane (z == 0)
        if abs(com[2] - 0.0) < tol and np.hypot(com[0], com[1]) < tol:
            main_inlet_tags.append(stag)
        # Check outlet plane (z == l_main)
        elif abs(com[2] - l_main) < tol and np.hypot(com[0], com[1]) < tol:
            outlet_tags.append(stag)
        # Check branch inlet plane (center at branch_tip)
        elif np.linalg.norm(com - branch_tip) < tol:
            branch_inlet_tags.append(stag)
        else:
            wall_tags.append(stag)

    assert len(main_inlet_tags) == 1, f"main_inlet match failed: {main_inlet_tags}"
    assert len(outlet_tags) == 1, f"outlet match failed: {outlet_tags}"
    assert len(branch_inlet_tags) == 1, f"branch_inlet match failed: {branch_inlet_tags}"
    assert len(wall_tags) >= 2, f"wall match failed: {wall_tags}"

    # 4. Fine, curvature-adapted surface triangulation for snappyHexMesh
    lc_min = max(min(d1, d2) * mesh_size_factor * 0.5, 1e-4)
    lc_max = max(d1 * mesh_size_factor * 2.0, 2e-3)
    gmsh.option.setNumber("Mesh.CharacteristicLengthMin", lc_min)
    gmsh.option.setNumber("Mesh.CharacteristicLengthMax", lc_max)
    gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 1)
    gmsh.option.setNumber("Mesh.MinimumElementsPerTwoPi", 72)
    gmsh.option.setNumber("Mesh.Algorithm", 6)  # Frontal-Delaunay

    gmsh.model.mesh.generate(2)

    # Extract triangles per patch and write clean binary STLs
    def extract_patch_mesh(tags):
        tris_all = []
        for stag in tags:
            elem_types, _, elem_node_tags = gmsh.model.mesh.getElements(2, stag)
            for etype, ntags in zip(elem_types, elem_node_tags):
                if etype == 2:  # 3-node triangle
                    ntags = ntags.reshape(-1, 3)
                    # Build lookup for nodes
                    unique_nodes, inv = np.unique(ntags, return_inverse=True)
                    coords = np.empty((len(unique_nodes), 3), dtype=np.float64)
                    for idx, nid in enumerate(unique_nodes):
                        c, _, _, _ = gmsh.model.mesh.getNode(int(nid))
                        coords[idx] = c
                    tris_all.append(coords[inv.reshape(-1, 3)])
        return np.vstack(tris_all)

    patch_tris = {
        "wall": extract_patch_mesh(wall_tags),
        "main_inlet": extract_patch_mesh(main_inlet_tags),
        "outlet": extract_patch_mesh(outlet_tags),
        "branch_inlet": extract_patch_mesh(branch_inlet_tags),
    }

    gmsh.finalize()

    # Combine into a single trimesh, fix winding globally so ALL normals point outward,
    # then split back and save individual STLs + combined STL.
    combined_tris = np.vstack(list(patch_tris.values()))
    verts_flat = combined_tris.reshape(-1, 3)
    faces_raw = np.arange(len(verts_flat)).reshape(-1, 3)
    tm_all = trimesh.Trimesh(vertices=verts_flat, faces=faces_raw, process=True)
    trimesh.repair.fix_normals(tm_all)

    vol_ratio = abs(tm_all.volume) / cad_vol
    is_wt = bool(tm_all.is_watertight)
    is_vol = bool(tm_all.is_volume)

    # Save individual STLs (with consistent outward normals)
    offset = 0
    patch_counts = {}
    for name, tris in patch_tris.items():
        n_f = len(tris)
        # Re-export using fixed-normal triangles from tm_all? Better: preserve exact patch faces
        v_p = tris.reshape(-1, 3)
        f_p = np.arange(len(v_p)).reshape(-1, 3)
        tm_p = trimesh.Trimesh(vertices=v_p, faces=f_p, process=True)
        tm_p.export(str(out_dir / f"{name}.stl"))
        patch_counts[name] = int(n_f)
        offset += n_f

    tm_all.export(str(out_dir / "tJunction.stl"))

    report = {
        "d1": d1,
        "d2": d2,
        "l_up": l_up,
        "l_down": l_down,
        "l_main": l_main,
        "l_branch": l_branch,
        "alpha_deg": alpha_deg,
        "cad_volume_m3": float(cad_vol),
        "stl_volume_m3": float(abs(tm_all.volume)),
        "volume_ratio": float(vol_ratio),
        "is_watertight": is_wt,
        "is_volume": is_vol,
        "euler_number": int(tm_all.euler_number),
        "n_triangles_total": int(len(tm_all.faces)),
        "patch_triangles": patch_counts,
        "bounds_min": tm_all.bounds[0].tolist(),
        "bounds_max": tm_all.bounds[1].tolist(),
        "location_in_mesh_main": [0.0, 0.0, 0.5 * l_up],
        "branch_inlet_center": branch_tip.tolist(),
    }

    report_path = out_dir / "geometry_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

    # Hard Gate 1A checks
    if not is_wt:
        raise RuntimeError("GATE 1A FAILED: Combined STL is NOT watertight!")
    if not (0.99 < vol_ratio < 1.01):
        raise RuntimeError(f"GATE 1A FAILED: Volume ratio {vol_ratio:.5f} outside [0.99, 1.01]!")
    if int(tm_all.euler_number) != 2:
        raise RuntimeError(f"GATE 1A FAILED: Euler number {tm_all.euler_number} != 2 (genus-0 closed solid)!")

    print("GATE 1A PASSED: Watertight full-3D CAD STL verified (0 open edges, Euler=2, vol within 1%).")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="Output triSurface directory")
    p.add_argument("--d1", type=float, default=0.460)
    p.add_argument("--d2", type=float, default=0.115)
    p.add_argument("--l-up", type=float, default=2.300)
    p.add_argument("--l-down", type=float, default=4.600)
    p.add_argument("--l-branch", type=float, default=1.610)  # R1 + 12*D2 = 0.23 + 1.38 = 1.61 m
    p.add_argument("--alpha", type=float, default=90.0)
    p.add_argument("--mesh-factor", type=float, default=0.04)
    args = p.parse_args()

    build_tjunction_stl(
        out_dir=Path(args.out),
        d1=args.d1,
        d2=args.d2,
        l_up=args.l_up,
        l_down=args.l_down,
        l_branch=args.l_branch,
        alpha_deg=args.alpha,
        mesh_size_factor=args.mesh_factor,
    )


if __name__ == "__main__":
    main()
