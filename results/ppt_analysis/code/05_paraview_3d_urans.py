"""
ParaView 3D CFD-Post-style visualization setup for OpenFOAM H2-CH4 T-junction cases.
Launch via:
  /Users/yash/CFD_3D_View/open_in_paraview.command
Or inside ParaView: File -> Load State -> /Users/yash/CFD_3D_View/h2_ch4_90deg_base_3d.pvsm
"""
from paraview.simple import *

paraview.simple._DisableFirstRenderCameraReset()

CASE_FILE = "/Users/yash/CFD_3D_View/h2_ch4_90deg_base_verified/case.foam"

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
view.Background = [0.06, 0.09, 0.16]  # Dark navy CFD-Post style background

# Hide raw solid mesh so we can layer translucent wall + 3D features cleanly
Hide(case, view)

# 2. Translucent 3D Pipe Wall
pipe_wall = ExtractBlock(registrationName="1_PipeWall_Translucent", Input=case)
pipe_wall.Selectors = ["/Root/boundary/wall"]
wall_disp = Show(pipe_wall, view)
wall_disp.Representation = "Surface"
ColorBy(wall_disp, ("POINTS", ""))
wall_disp.DiffuseColor = [0.78, 0.82, 0.88]
wall_disp.Opacity = 0.16

# 3. Convert Cell Data -> Point Data for smooth 3D contours, slices, and streamlines
c2p = CellDatatoPointData(registrationName="CellToPoint", Input=case)
c2p.CellDataArraytoprocess = ["H2", "H2Mean", "U", "UMean"]

# 4. 3D H2 Plume Isosurfaces (H2Mean)
plume = Contour(registrationName="2_H2_3D_Plume_Isosurfaces", Input=c2p)
plume.ContourBy = ["POINTS", "H2Mean"]
plume.Isosurfaces = [0.02, 0.06, 0.14]
plume_disp = Show(plume, view)
plume_disp.Representation = "Surface"
plume_disp.Opacity = 0.65
ColorBy(plume_disp, ("POINTS", "H2Mean"))

# 5. Longitudinal Centerplane Slice (X = 0) — hidden by default; click eye icon to toggle
slice_center = Slice(registrationName="3_Slice_CenterPlane_X0", Input=c2p)
slice_center.SliceType = "Plane"
slice_center.SliceType.Origin = [0.0, 0.0, 2.3]
slice_center.SliceType.Normal = [1.0, 0.0, 0.0]
slice_center_disp = Show(slice_center, view)
ColorBy(slice_center_disp, ("POINTS", "H2Mean"))
Hide(slice_center, view)

# 6. Cross-Section Slices at +1D (z = 2.76 m) and +5D (z = 4.60 m)
for label, z_pos in [("4_Slice_Plus1D", 2.76), ("5_Slice_Plus5D", 4.60)]:
    sl = Slice(registrationName=label, Input=c2p)
    sl.SliceType = "Plane"
    sl.SliceType.Origin = [0.0, 0.0, z_pos]
    sl.SliceType.Normal = [0.0, 0.0, 1.0]
    sl_disp = Show(sl, view)
    ColorBy(sl_disp, ("POINTS", "H2Mean"))

# 7. 3D Streamlines seeded from the H2 nozzle (branch_inlet) + glossy 3D Tubes
branch_seed = ExtractBlock(registrationName="BranchInlet_Seed", Input=case)
branch_seed.Selectors = ["/Root/boundary/branch_inlet"]

stream = StreamTracerWithCustomSource(
    registrationName="6_H2_Streamlines",
    Input=c2p,
    SeedSource=branch_seed,
)
stream.Vectors = ["POINTS", "UMean"]
stream.MaximumStreamlineLength = 5.5
stream.IntegrationDirection = "FORWARD"

tubes = Tube(registrationName="6_H2_Streamlines_3DTubes", Input=stream)
tubes.Radius = 0.007
tubes_disp = Show(tubes, view)
ColorBy(tubes_disp, ("POINTS", "H2Mean"))

# 8. Lock clean colormap range [0.00, 0.25] and format scalar bar
h2_lut = GetColorTransferFunction("H2Mean")
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
sb.Title = "H2 Mass Fraction (Mean)"
sb.ComponentTitle = ""
sb.TitleFontSize = 14
sb.LabelFontSize = 12
sb.ScalarBarLength = 0.35

# 9. Isometric 3D Camera focused on T-junction + downstream plume
view.CameraPosition = [3.6, 1.5, 1.0]
view.CameraFocalPoint = [0.0, 0.1, 3.4]
view.CameraViewUp = [0.0, 1.0, 0.0]
view.CameraParallelScale = 1.6
Render()
