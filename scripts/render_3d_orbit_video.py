#!/usr/bin/env python3
"""
Render a 1080p 3D 360-degree Orbit MP4 Video + Keyframe Preview PNG
of the Full-3D OpenFOAM v13 H2-CH4 T-Junction Plume (1.34M cells, t = 3.0 s).
"""
import math
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pyvista as pv

M_H2 = 2.01588
M_CH4 = 16.0425


def add_x_h2_pct(mesh: pv.DataSet, field: str = "H2Mean") -> pv.DataSet:
    if field in mesh.point_data:
        y = np.clip(mesh.point_data[field], 0.0, 1.0)
        n_h2 = y / M_H2
        n_ch4 = (1.0 - y) / M_CH4
        mesh.point_data["X_H2_pct"] = 100.0 * n_h2 / (n_h2 + n_ch4)
    elif field in mesh.cell_data:
        y = np.clip(mesh.cell_data[field], 0.0, 1.0)
        n_h2 = y / M_H2
        n_ch4 = (1.0 - y) / M_CH4
        mesh.cell_data["X_H2_pct"] = 100.0 * n_h2 / (n_h2 + n_ch4)
    return mesh


def main():
    vtk_dir = Path("/Users/yash/cfd_full3d_of13_backup/results/h2_3d_orbit_vtk")
    out_dir = Path("/Users/yash/cfd_full3d_of13_backup/results/videos")
    out_dir.mkdir(parents=True, exist_ok=True)

    wall = pv.read(vtk_dir / "wall.stl")
    center = add_x_h2_pct(pv.read(vtk_dir / "centerSlice.vtk"))
    iso15 = add_x_h2_pct(pv.read(vtk_dir / "iso_Y015.vtk"))
    iso30 = add_x_h2_pct(pv.read(vtk_dir / "iso_Y030.vtk"))
    iso55 = add_x_h2_pct(pv.read(vtk_dir / "iso_Y055.vtk"))
    s2d = add_x_h2_pct(pv.read(vtk_dir / "slice_2D.vtk"))
    s6d = add_x_h2_pct(pv.read(vtk_dir / "slice_6D.vtk"))

    pl = pv.Plotter(off_screen=True, window_size=(1920, 1080))
    pl.set_background("#0b1020", top="#18233c")

    # Translucent full-3D pipe wall (glass-like)
    pl.add_mesh(wall, color="#a8c0e0", opacity=0.14, smooth_shading=True, specular=0.4)

    # Centerline longitudinal slice colored by H2 mole fraction [%]
    pl.add_mesh(
        center,
        scalars="X_H2_pct",
        cmap="turbo",
        clim=[0.0, 45.0],
        opacity=0.82,
        scalar_bar_args={
            "title": "H2 Mole Fraction X_H2 [mol %]\n",
            "color": "white",
            "title_font_size": 20,
            "label_font_size": 16,
            "width": 0.35,
            "position_x": 0.325,
            "position_y": 0.04,
            "n_labels": 6,
        },
    )

    # 3D H2 iso-surfaces showing the horseshoe CVP & turbulent plume
    pl.add_mesh(iso15, color="#38bdf8", opacity=0.18, smooth_shading=True)   # ~10.8% mol H2
    pl.add_mesh(iso30, color="#facc15", opacity=0.35, smooth_shading=True)   # ~19.8% mol H2
    pl.add_mesh(iso55, color="#ef4444", opacity=0.65, smooth_shading=True)   # ~31.7% mol H2

    # Downstream circular cross-sections at z/D = 2 and z/D = 6
    pl.add_mesh(s2d, scalars="X_H2_pct", cmap="turbo", clim=[0.0, 45.0], opacity=0.95, show_scalar_bar=False)
    pl.add_mesh(s6d, scalars="X_H2_pct", cmap="turbo", clim=[0.0, 45.0], opacity=0.95, show_scalar_bar=False)

    pl.add_text(
        "Full-3D H2-CH4 T-Junction Mixing (6.9 MPa Peng-Robinson | OpenFOAM v13 | 1.34M cells)\n"
        "Iso-surfaces: Blue = 11% mol | Yellow = 20% mol | Red = 32% mol H2",
        position="upper_left",
        color="white",
        font_size=14,
    )
    pl.add_axes(color="white")

    # Focal point centered on the junction + downstream mixing zone
    focal = (0.0, 0.18, 3.35)
    radius = 5.4
    n_frames = 180  # 6 seconds at 30 fps (smooth 360-deg orbit + slight elevation wave)

    mp4_path = out_dir / "h2_ch4_3d_orbit_1.34M.mp4"
    preview_path = out_dir / "h2_ch4_3d_orbit_preview.png"

    writer = imageio.get_writer(str(mp4_path), fps=30, codec="libx264", quality=8, macro_block_size=1)
    try:
        for i in range(n_frames):
            ang = 2.0 * math.pi * (i / n_frames) - math.pi / 4.0
            elev = 0.25 + 0.18 * math.sin(2.0 * math.pi * i / n_frames)
            cx = focal[0] + radius * math.cos(ang) * math.cos(elev)
            cz = focal[2] + radius * math.sin(ang) * math.cos(elev)
            cy = focal[1] + radius * math.sin(elev) + 0.4
            pl.camera_position = [(cx, cy, cz), focal, (0.0, 1.0, 0.0)]
            img = pl.screenshot(return_img=True)
            writer.append_data(img)
            if i == 15:
                imageio.imwrite(str(preview_path), img)
    finally:
        writer.close()
        pl.close()

    print(f"RENDERED_OK: {mp4_path} ({mp4_path.stat().st_size / 1e6:.2f} MB)")
    print(f"PREVIEW_OK:  {preview_path}")


if __name__ == "__main__":
    main()
