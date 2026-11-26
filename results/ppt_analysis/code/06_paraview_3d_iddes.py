"""
ParaView 3D CFD-Post-style visualization for ~/Downloads/h2_ch4_90deg_bottom_iddes (SST-IDDES).
"""
from paraview.simple import *

paraview.simple._DisableFirstRenderCameraReset()

CASE_FILE = "/Users/yash/Downloads/h2_ch4_90deg_bottom_iddes/case.foam"

# 1. Load OpenFOAM case (Reconstructed)
case = OpenFOAMReader(registrationName="case.foam", FileName=CASE_FILE)
case.CaseType = "Reconstructed Case"
case.MeshRegions = ["internalMesh", "patch/wall", "patch/branch_inlet"]
case.CellArrays = ["H2", "H2Mean", "U", "UMean", "p_rgh"]
case.UpdatePipeline()

# Jump to latest time (T = 3.0 s)
scene = GetAnimationScene()
scene.UpdateAnimationUsingDataTimeSteps()
times = case.TimestepValues
if times:
    t_latest = times[-1]
    scene.AnimationTime = t_latest
    case.UpdatePipeline(time=t_latest)

view = GetActiveViewOrCreate("RenderView")
view.UseColorPaletteForBackground = 0
view.Background = [0.06, 0.09, 0.16]

Hide(case, view)

# 2. Translucent 3D Pipe Wall
pipe_wall = ExtractBlock(registrationName="1_PipeWall_Translucent", Input=case)
pipe_wall.Selectors = ["/Root/boundary/wall"]
wall_disp = Show(pipe_wall, view)
wall_disp.Representation = "Surface"
ColorBy(wall_disp, ("POINTS", ""))
wall_disp.DiffuseColor = [0.78, 0.82, 0.88]
wall_disp.Opacity = 0.15

# 3. Cell -> Point data for smooth 3D contours, slices, streamlines, and Q-criterion
c2p = CellDatatoPointData(registrationName="CellToPoint", Input=case)
c2p.CellDataArraytoprocess = ["H2", "H2Mean", "U", "UMean"]

# 4. Instantaneous 3D Turbulent H2 Plume Isosurfaces (SST-IDDES)
plume = Contour(registrationName="2_H2_Instantaneous_3D_Plume", Input=c2p)
plume.ContourBy = ["POINTS", "H2"]
plume.Isosurfaces = [0.025, 0.07, 0.16]
plume_disp = Show(plume, view)
plume_disp.Representation = "Surface"
plume_disp.Opacity = 0.72
ColorBy(plume_disp, ("POINTS", "H2"))

# 5. 3D Q-Criterion Vortex Tubes (hidden by default — click eye icon to toggle on!)
grad = Gradient(registrationName="VelocityGradient_Q", Input=c2p)
grad.ScalarArray = ["POINTS", "U"]
grad.ComputeGradient = 0
grad.ComputeQCriterion = 1

q_vortex = Contour(registrationName="3_Vortices_QCriterion_3D", Input=grad)
q_vortex.ContourBy = ["POINTS", "Q Criterion"]
q_vortex.Isosurfaces = [5000.0]
q_disp = Show(q_vortex, view)
q_disp.Representation = "Surface"
q_disp.Opacity = 0.85
ColorBy(q_disp, ("POINTS", "H2"))
Hide(q_vortex, view)

# 6. Longitudinal Centerplane Slice (X = 0) — hidden by default; click eye icon to toggle
slice_center = Slice(registrationName="4_Slice_CenterPlane_X0", Input=c2p)
slice_center.SliceType = "Plane"
slice_center.SliceType.Origin = [0.0, 0.0, 2.3]
slice_center.SliceType.Normal = [1.0, 0.0, 0.0]
slice_center_disp = Show(slice_center, view)
ColorBy(slice_center_disp, ("POINTS", "H2"))
Hide(slice_center, view)

# 7. Cross-Section Slices at +1D (z = 2.76 m) and +5D (z = 4.60 m)
for label, z_pos in [("5_Slice_Plus1D", 2.76), ("6_Slice_Plus5D", 4.60)]:
    sl = Slice(registrationName=label, Input=c2p)
    sl.SliceType = "Plane"
    sl.SliceType.Origin = [0.0, 0.0, z_pos]
    sl.SliceType.Normal = [0.0, 0.0, 1.0]
    sl_disp = Show(sl, view)
    ColorBy(sl_disp, ("POINTS", "H2"))

# 8. 3D Streamlines seeded from branch_inlet + glossy Tubes (hidden by default to keep turbulent plume super clear)
branch_seed = ExtractBlock(registrationName="BranchInlet_Seed", Input=case)
branch_seed.Selectors = ["/Root/boundary/branch_inlet"]

stream = StreamTracerWithCustomSource(
    registrationName="7_H2_Streamlines",
    Input=c2p,
    SeedSource=branch_seed,
)
stream.Vectors = ["POINTS", "U"]
stream.MaximumStreamlineLength = 5.5
stream.IntegrationDirection = "FORWARD"

tubes = Tube(registrationName="7_H2_Streamlines_3DTubes", Input=stream)
tubes.Radius = 0.006
tubes_disp = Show(tubes, view)
ColorBy(tubes_disp, ("POINTS", "H2"))
Hide(tubes, view)

# 9. Lock clean colormap range [0.00, 0.25]
h2_lut = GetColorTransferFunction("H2")
for preset in ("Turbo", "Inferno", "Plasma (matplotlib)", "Cool to Warm"):
    try:
        h2_lut.ApplyPreset(preset, True)
        break
    except RuntimeError:
        pass
h2_lut.RescaleTransferFunction(0.0, 0.25)
h2_lut.AutomaticRescaleRangeMode = "Never"

plume_disp.SetScalarBarVisibility(view, True)
sb = GetScalarBar(h2_lut, view)
sb.Title = "Instantaneous H2 Mass Fraction (SST-IDDES)"
sb.ComponentTitle = ""
sb.TitleFontSize = 13
sb.LabelFontSize = 11
sb.ScalarBarLength = 0.35

# 10. Isometric 3D Camera (bottom-injection view: y points up or down depending on gravity; let's frame cleanly)
view.CameraPosition = [3.6, -1.2, 1.0]
view.CameraFocalPoint = [0.0, 0.0, 3.4]
view.CameraViewUp = [0.0, -1.0, 0.0]
Render()
