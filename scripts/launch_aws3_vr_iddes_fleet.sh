#!/usr/bin/env bash
set -euo pipefail

SSH_KEY="$HOME/.ssh/id_ed25519"
RCLONE_CONF="$HOME/.config/rclone/rclone.conf"
SPOT_GUARD="/Users/yash/cfd_full3d_of13_backup/scripts/spot_guard.sh"

deploy_run() {
  local profile="$1"
  local region="$2"
  local itype="$3"        # c6i.32xlarge | c7i.16xlarge
  local nprocs="$4"       # 128 | 64
  local ami="$5"
  local sg="$6"
  local cname="$7"
  local mesh_remote="$8"
  local orientation="$9"  # top | bottom
  local turb="${10}"      # urans | iddes
  local u_branch="${11}"  # 16 | 32 | 48
  local max_co="${12}"    # 3.0 | 4.0 | 5.0

  local inst_id
  inst_id=$(AWS_PROFILE="$profile" aws ec2 run-instances --region "$region" \
    --image-id "$ami" --instance-type "$itype" --key-name cfd-of13-key \
    --security-group-ids "$sg" \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":150,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=cfd-${nprocs}c-${cname}},{Key=CaseName,Value=${cname}},{Key=ManagedBy,Value=cfd-aws3-vr-iddes}]" \
    --query 'Instances[0].InstanceId' --output text)

  echo "[$(date -u +%H:%M:%S)] LAUNCHED $cname -> $inst_id ($itype OD in $profile/$region)"
  AWS_PROFILE="$profile" aws ec2 wait instance-running --region "$region" --instance-ids "$inst_id"
  local ip
  ip=$(AWS_PROFILE="$profile" aws ec2 describe-instances --region "$region" --instance-ids "$inst_id" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

  for _ in $(seq 1 40); do
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=5 ubuntu@"$ip" "echo ok" >/dev/null 2>&1 && break
    sleep 3
  done

  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@"$ip" "mkdir -p ~/.config/rclone ~/cfd_full3d_of13/cases ~/bin"
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=no -q "$RCLONE_CONF" ubuntu@"$ip":~/.config/rclone/rclone.conf
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=no -q "$SPOT_GUARD" ubuntu@"$ip":~/bin/spot_guard.sh

  local tname="of13_${cname#h2_ch4_}"
  tname="${tname:0:18}"

  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@"$ip" "bash -s" << EOF
set -e
chmod +x ~/bin/spot_guard.sh
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq curl gnupg lsb-release tmux rclone zstd >/dev/null
if [ ! -f /opt/openfoam13/etc/bashrc ]; then
  sudo sh -c "wget -q -O - https://dl.openfoam.org/gpg.key > /etc/apt/trusted.gpg.d/openfoam.asc"
  sudo add-apt-repository -y http://dl.openfoam.org/ubuntu >/dev/null 2>&1
  sudo apt-get update -qq
  sudo apt-get install -y -qq openfoam13 >/dev/null
fi
. /opt/openfoam13/etc/bashrc || true

CASE_DIR=~/cfd_full3d_of13/cases/${cname}
rm -rf "\$CASE_DIR" && mkdir -p "\$CASE_DIR"
rclone copyto "${mesh_remote}" /tmp/mesh_setup.tar.gz --transfers 4 --retries 3
tar -xzf /tmp/mesh_setup.tar.gz -C "\$CASE_DIR"
rm -f /tmp/mesh_setup.tar.gz

cd "\$CASE_DIR"
rm -rf dynamicCode

# Orientation (bottom flips gravity +y)
if [ "${orientation}" = "bottom" ]; then
  sed -i 's/(0 -9.81 0)/(0 9.81 0)/g' constant/g
fi

# Update U_branch for VR sweep (exact 1/7th power-law + k/omega scaling)
python3 - "${u_branch}" "${turb}" "${nprocs}" << 'PYEOF'
import sys, re
from pathlib import Path

u_br = float(sys.argv[1])
turb = sys.argv[2]
nprocs = int(sys.argv[3])

# 1. Scale branch_inlet velocity in 0/U if != 32
if abs(u_br - 32.0) > 1e-3:
    u_path = Path("0/U")
    utxt = u_path.read_text()
    parts = utxt.split("branch_inlet", 1)
    parts[1] = re.sub(r'const scalar uBulk\s*=\s*[0-9.]+;', f'const scalar uBulk = {u_br:.6f};', parts[1])
    utxt = "branch_inlet".join(parts)
    utxt = re.sub(r'(branch_inlet\s*\{[^}]*value\s+uniform\s+\(0\s+)-[0-9.]+(\s+0\.000000\);)', rf'\g<1>-{u_br:.6f}\g<2>', utxt)
    u_path.write_text(utxt)
    # Scale k and omega at branch_inlet for URANS (I = 5%, L = 0.07*0.115 = 0.00805 m)
    k_br = 1.5 * (0.05 * u_br) ** 2
    om_br = (k_br ** 0.5) / (0.09 ** 0.25 * 0.00805)
    for fname, val in [("0/k", k_br), ("0/omega", om_br)]:
        fp = Path(fname)
        if fp.exists():
            ftxt = fp.read_text()
            ftxt = re.sub(r'(branch_inlet\s*\{[^}]*value\s+uniform\s+)[0-9.eE+-]+(;)', rf'\g<1>{val:.4f}\g<2>', ftxt)
            fp.write_text(ftxt)

# 2. Switch turbulence to SpalartAllmarasIDDES if requested
if turb == "iddes":
    Path("constant/momentumTransport").write_text("""FoamFile
{
    format      ascii;
    class       dictionary;
    object      momentumTransport;
}

simulationType  LES;

LES
{
    model           SpalartAllmarasIDDES;
    turbulence      on;
    printCoeffs     on;
    delta           IDDESDelta;
    IDDESDeltaCoeffs
    {
        hmax            maxDeltaxyz;
    }
}
""")
    Path("constant/thermophysicalTransport").write_text("""FoamFile
{
    format      ascii;
    class       dictionary;
    object      thermophysicalTransport;
}

LES
{
    model           nonUnityLewisEddyDiffusivity;
    Prt             0.85;
    Sct             0.7;
}
""")
    Path("0/nuTilda").write_text("""FoamFile
{
    format      ascii;
    class       volScalarField;
    object      nuTilda;
}

dimensions      [0 2 -1 0 0 0 0];
internalField   uniform 1e-4;

boundaryField
{
    wall         { type fixedValue; value uniform 0; }
    main_inlet   { type fixedValue; value uniform 1e-4; }
    branch_inlet { type fixedValue; value uniform 1e-4; }
    outlet       { type inletOutlet; inletValue uniform 1e-4; value uniform 1e-4; }
}
""")
    # Ensure nuTilda in fvSchemes & fvSolution
    fvs = Path("system/fvSchemes").read_text()
    if "div(phi,nuTilda)" not in fvs:
        fvs = fvs.replace("div(phi,k)", "div(phi,nuTilda) Gauss limitedLinear 1;\n    div(phi,k)")
        Path("system/fvSchemes").write_text(fvs)
    fvsol = Path("system/fvSolution").read_text()
    if "nuTilda" not in fvsol:
        fvsol = fvsol.replace('"(k|omega|epsilon)"', '"(k|omega|epsilon|nuTilda)"')
        Path("system/fvSolution").write_text(fvsol)

# 3. Strict [0,1] species boundedness
fvs = Path("system/fvSchemes").read_text()
fvs = re.sub(r'div\(phi,H2\)\s+Gauss\s+limitedLinear\s+1;', 'div(phi,H2)     Gauss limitedLinear01 1;', fvs)
fvs = re.sub(r'div\(phi,CH4\)\s+Gauss\s+limitedLinear\s+1;', 'div(phi,CH4)    Gauss limitedLinear01 1;', fvs)
Path("system/fvSchemes").write_text(fvs)

# 4. DecomposeParDict + controlDict (cold start maxCo=1.5, writeInterval 0.25, purgeWrite 3, videoSlices)
dp = Path("system/decomposeParDict").read_text()
dp = re.sub(r'numberOfSubdomains\s+\d+;', f'numberOfSubdomains {nprocs};', dp)
Path("system/decomposeParDict").write_text(dp)

cd = Path("system/controlDict").read_text()
cd = re.sub(r'maxCo\s+[0-9.]+;', 'maxCo               1.5;', cd)
cd = re.sub(r'writeInterval\s+[0-9.]+;', 'writeInterval       0.25;', cd, count=1)
if "purgeWrite" in cd:
    cd = re.sub(r'purgeWrite\s+\d+;', 'purgeWrite          3;', cd)
else:
    cd = re.sub(r'(writeInterval\s+[0-9.]+;)', r'\1\npurgeWrite          3;', cd, count=1)

if "videoSlices" not in cd:
    block = """
    videoSlices
    {
        type                surfaces;
        libs                ("libsampling.so");
        writeControl        adjustableRunTime;
        writeInterval       0.025;
        surfaceFormat       vtk;
        fields              (H2 U);
        interpolationScheme cellPoint;
        surfaces
        {
            x0_center { type cuttingPlane; planeType pointAndNormal; pointAndNormalDict { point (0 0 0); normal (1 0 0); } interpolate true; }
            z1D       { type cuttingPlane; planeType pointAndNormal; pointAndNormalDict { point (0 0 2.76); normal (0 0 1); } interpolate true; }
            z2D       { type cuttingPlane; planeType pointAndNormal; pointAndNormalDict { point (0 0 3.22); normal (0 0 1); } interpolate true; }
            z4D       { type cuttingPlane; planeType pointAndNormal; pointAndNormalDict { point (0 0 4.14); normal (0 0 1); } interpolate true; }
            z6D       { type cuttingPlane; planeType pointAndNormal; pointAndNormalDict { point (0 0 5.06); normal (0 0 1); } interpolate true; }
            z10D      { type cuttingPlane; planeType pointAndNormal; pointAndNormalDict { point (0 0 6.85); normal (0 0 1); } interpolate true; }
        }
    }
"""
    idx = cd.rfind("}")
    cd = cd[:idx] + block + cd[idx:]
Path("system/controlDict").write_text(cd)

# 5. PIMPLE hot-tune (nOuterCorrectors 6 for ~2x faster clean convergence)
fvsol = Path("system/fvSolution").read_text()
fvsol = re.sub(r'nOuterCorrectors\s+\d+;', 'nOuterCorrectors            6;', fvsol)
fvsol = re.sub(r'nNonOrthogonalCorrectors\s+1;', 'nNonOrthogonalCorrectors    0;', fvsol)
fvsol = re.sub(r'(p_rgh\s*\{\s*tolerance\s+)1e-4(;)', r'\g<1>5e-4\2', fvsol)
fvsol = re.sub(r'(U\s*\{\s*tolerance\s+)1e-4(;)', r'\g<1>5e-4\2', fvsol)
fvsol = fvsol.replace('"(H2|h|e)" { tolerance 1e-4; relTol 0; }', '"H2" { tolerance 1e-4; relTol 0; }\n        "(h|e)" { tolerance 1e-3; relTol 0; }')
fvsol = re.sub(r'(p_rgh\s*\{[^}]*relTol\s+)0\.01(;)', r'\g<1>0.05\2', fvsol, count=1)
Path("system/fvSolution").write_text(fvsol)
PYEOF

decomposePar -force > log.decomposePar 2>&1
tmux kill-session -t "${tname}" 2>/dev/null || true
tmux new-session -d -s "${tname}" "bash -lc '. /opt/openfoam13/etc/bashrc || true; cd \$CASE_DIR; mpirun -np ${nprocs} --bind-to none --oversubscribe foamRun -parallel </dev/null >> log.solver 2>&1'"
nohup bash -c "sleep 240 && sed -i -E 's/maxCo[[:space:]]+[0-9.]+;/maxCo               ${max_co};/' \$CASE_DIR/system/controlDict" >/dev/null 2>&1 &
nohup ~/bin/spot_guard.sh "\$CASE_DIR" "${cname}" "${nprocs}" "3.0" >/dev/null 2>&1 &

for _ in \$(seq 1 20); do
  grep -q "^Time = " log.solver 2>/dev/null && break
  sleep 3
done
t_first=\$(grep "^Time = " log.solver 2>/dev/null | tail -n 1 | awk '{print \$3}')
fat=\$(grep -c "FOAM FATAL" log.solver 2>/dev/null || true)
procs=\$(pgrep -c foamRun 2>/dev/null || true)
echo "VERIFIED_LIVE: ${cname} | ip=${ip} | id=${inst_id} | ${itype} | procs=\${procs} | t=\${t_first:-starting} | FATAL=\${fat:-0} | turb=${turb} | uBr=${u_branch}"
EOF
}

# Launch all 4 On-Demand runs on aws-cfd-3 (zero impact on aws-cfd-2 friend reserve!)
deploy_run "aws-cfd-3" "us-east-1" "c6i.32xlarge" 128 "ami-0a951420c495183dc" "sg-0024a2d344600e4dc" \
  "h2_ch4_90deg_top_vr48_iddes" \
  "gdrive:CFD_H2_CH4_Full3D_OF13/cases/h2_ch4_90deg_base_verified/h2_ch4_90deg_base_verified_mesh_and_setup.tar.gz" \
  "top" "iddes" "48" "4.0" &
sleep 8

deploy_run "aws-cfd-3" "us-east-1" "c6i.32xlarge" 128 "ami-0a951420c495183dc" "sg-0024a2d344600e4dc" \
  "h2_ch4_120deg_bot_iddes" \
  "gdrive:CFD_H2_CH4_Full3D_OF13/cases/h2_ch4_120deg_top_verified/h2_ch4_120deg_top_verified_mesh_and_setup.tar.gz" \
  "bottom" "iddes" "32" "3.0" &
sleep 8

deploy_run "aws-cfd-3" "ap-south-1" "c6i.32xlarge" 128 "ami-032eca770b5d15e72" "sg-077659a6797697914" \
  "h2_ch4_90deg_top_vr48_urans" \
  "gdrive:CFD_H2_CH4_Full3D_OF13/cases/h2_ch4_90deg_base_verified/h2_ch4_90deg_base_verified_mesh_and_setup.tar.gz" \
  "top" "urans" "48" "4.0" &
sleep 8

deploy_run "aws-cfd-3" "ap-south-1" "c7i.16xlarge" 64 "ami-032eca770b5d15e72" "sg-077659a6797697914" \
  "h2_ch4_90deg_top_vr16_urans" \
  "gdrive:CFD_H2_CH4_Full3D_OF13/cases/h2_ch4_90deg_base_verified/h2_ch4_90deg_base_verified_mesh_and_setup.tar.gz" \
  "top" "urans" "16" "5.0" &

wait
echo "ALL_4_AWS3_VR_AND_IDDES_CASES_LIVE"
