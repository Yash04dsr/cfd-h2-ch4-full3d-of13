# Complete Codebase — Full-3D OpenFOAM v13 H₂–CH₄ T-Junction CFD Project

All scripts used to generate the geometry, mesh, OpenFOAM v13 simulations, mixing (`CoV`), irreversible pressure drop (`ΔP_irr`), 3D ParaView visualizations, 1080p SST-IDDES vortex videos, and PPT figures are organized here in execution order.

---

## 0. Geometry, Meshing, Solver & Verification (`00_geometry_mesh_solver/`)
| Script | What it does |
| :--- | :--- |
| `generate_geometry_gmsh.py` | Watertight full-3D CAD/OpenCASCADE T-junction geometry (no symmetry plane) via Gmsh |
| `build_case.py` | Generates OpenFOAM v13 `multicomponentFluid` cases (`PengRobinsonGas` EOS at 6.9 MPa, `kOmegaSST` / `SpalartAllmarasIDDES`, 2nd-order schemes, function objects) |
| `verify_gate.py` | Automated EasyFoam-style mesh & physics verification gates (`checkMesh`, `y+`, mass closure, NIST density check) |
| `render_mesh_audit.py` | Renders mesh quality & cross-section audit figure |
| `render_3d_orbit_video.py` | Renders 360° 3D orbit video from VTK isosurfaces |

---

## 1. Quantitative Mixing & Conservation Extraction (`CoV`, `L_mix`, `y+`)
- **`01_extract_cov_and_metrics.py`**
  - Runs OpenFOAM v13 `foamPostProcess -func sampleH2Slices -time 3` on 10 axial cross-sections (`z/D₁ = 1..10`).
  - Interpolates `H2Mean` and `H2` onto a uniform circular grid (`80×80`) to compute unbiased area-weighted **`CoV_Y(z/D)`** (mass fraction) and **`CoV_X(z/D)`** (mole/volume fraction).
  - Computes mixing length **`L_mix / D`** (`CoV_X ≤ 0.05`), mass conservation error (`%`), outlet bulk `X_H2` vs theory, and wall `y+`.
  - **Output:** `../all_cases_summary.json`

---

## 2. Acoustic-Immune Pressure Drop — Herwig & Schmandt (2011)
- **`02_compute_pressure_drop_herwig2011.py`**
  - Runs `foamPostProcess -func writeCellVolumes -time 3` to extract exact finite-volume cell volumes `Vc`.
  - Reads OpenFOAM binary fields `Vc`, `rhoMean`, `k`, and `omega` directly in Python and integrates total irreversible turbulent mechanical-energy dissipation:
    $$\dot{\Phi}_{\text{loss}} = \int_V \rho\,\beta^*\,k\,\omega\,dV, \qquad \Delta P_{\text{irr}} = \frac{\dot{\Phi}_{\text{loss}}}{Q_{\text{out}}}, \qquad K = \frac{2\,\dot{\Phi}_{\text{loss}}}{\dot{m}_{\text{out}}\,U_{\text{out}}^2}$$
  - **References:** Herwig & Schmandt (*ASME J. Fluids Eng.*, 2011); Schmandt & Herwig (*Int. J. Heat Fluid Flow*, 2015); Kock & Herwig (*Heat Mass Transf.*, 2006).
  - **Output:** `../herwig_dissipation.json`

---

## 3. PPT Publication Figures (300 DPI) & Master CSV Table
- **`03a_generate_core_ppt_figures.py`** — Generates `fig2_axial_CoV_decay_4panel.png`, `fig3_VR_and_dD_scaling.png`, and `fig4_verification_IDDES_GCI_Sct.png`.
- **`03b_update_herwig_dP_and_pareto.py`** — Merges `herwig_dissipation.json` into `../all_cases_summary.csv` and generates `fig1_angle_sweep_top_vs_bottom.png` and `fig5_pareto_CoV_vs_dP.png` (Pareto Frontier).
- **`04_postprocess_single_case_of13.py`** — Generates a 4-panel physical reality & mixing audit figure for any individual OpenFOAM v13 case.

---

## 4. 3D ParaView Visualization (ANSYS CFD-Post Equivalent)
- **`05_paraview_3d_urans.py`** — Pre-configures ParaView 6.x for URANS cases (translucent pipe shell, 3D `H2Mean` plume isosurfaces, `+1D`/`+5D` slices, centerplane slice, 3D streamlines with tubes).
- **`06_paraview_3d_iddes.py`** — Pre-configures ParaView 6.x for SST-IDDES cases (instantaneous 3D turbulent `H2` isosurfaces + **3D Q-Criterion vortex tubes** + slices + streamlines).

---

## 5. 1080p Live Turbulent Vortex Videos (SST-IDDES)
- **`07_render_iddes_vortex_video_1080p.py`**
  - Reads 101 high-frequency VTK snapshots (`t = 3.00 → 3.30 s`, `dt = 0.003 s`) written by OpenFOAM's `vortexSlices` function object.
  - Renders 4-panel 1080p frames (full centerplane, near-field Kelvin–Helmholtz zoom, `+1D` & `+5D` cross-sections with live `CoV`) in parallel and encodes H.264 MP4 via `ffmpeg`.
