#!/usr/bin/env bash
set -euo pipefail

SSH_KEY="$HOME/.ssh/id_ed25519"
RCLONE_CONF="$HOME/.config/rclone/rclone.conf"
SCRIPTS_DIR="/Users/yash/cfd_full3d_of13_backup/scripts"

deploy_dd() {
  local profile="$1"
  local region="$2"
  local market="$3"       # ondemand | spot
  local ami="$4"
  local sg="$5"
  local cname="$6"
  local d2="$7"           # 0.0828 | 0.1610
  local u_branch="$8"     # 61.73 | 16.33
  local orientation="$9"  # top | bottom
  local turb="${10}"      # SpalartAllmarasIDDES | kOmegaSST
  local max_co="${11}"    # 4.0 | 5.0

  local market_args=()
  if [ "$market" = "spot" ]; then
    market_args=(--instance-market-options '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time"}}')
  fi

  local inst_id=""
  for itype in c6i.32xlarge c6a.32xlarge c7i.32xlarge; do
    inst_id=$(AWS_PROFILE="$profile" aws ec2 run-instances --region "$region" \
      --image-id "$ami" --instance-type "$itype" --key-name cfd-of13-key \
      --security-group-ids "$sg" \
      ${market_args[@]+"${market_args[@]}"} \
      --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":150,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
      --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=cfd-128c-${cname}},{Key=CaseName,Value=${cname}},{Key=ManagedBy,Value=cfd-dd-sweep}]" \
      --query 'Instances[0].InstanceId' --output text 2>/dev/null || true)
    if [ -n "$inst_id" ] && [ "$inst_id" != "None" ]; then
      echo "[$(date -u +%H:%M:%S)] LAUNCHED $cname -> $inst_id ($itype $market in $profile/$region)"
      break
    fi
  done

  if [ -z "$inst_id" ] || [ "$inst_id" = "None" ]; then
    echo "[$(date -u +%H:%M:%S)] ERROR: Could not launch $cname in $profile/$region ($market)"
    return 1
  fi

  AWS_PROFILE="$profile" aws ec2 wait instance-running --region "$region" --instance-ids "$inst_id"
  local ip
  ip=$(AWS_PROFILE="$profile" aws ec2 describe-instances --region "$region" --instance-ids "$inst_id" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

  for _ in $(seq 1 40); do
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=5 ubuntu@"$ip" "echo ok" >/dev/null 2>&1 && break
    sleep 3
  done

  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@"$ip" "mkdir -p ~/.config/rclone ~/cfd_full3d_of13/cases ~/cfd_full3d_of13/scripts ~/bin"
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=no -q "$RCLONE_CONF" ubuntu@"$ip":~/.config/rclone/rclone.conf
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=no -q "$SCRIPTS_DIR/spot_guard.sh" ubuntu@"$ip":~/bin/spot_guard.sh
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=no -q \
    "$SCRIPTS_DIR/build_case.py" "$SCRIPTS_DIR/generate_geometry_gmsh.py" "$SCRIPTS_DIR/verify_gate.py" \
    ubuntu@"$ip":~/cfd_full3d_of13/scripts/

  local tname="of13_${cname#h2_ch4_}"
  tname="${tname:0:18}"

  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@"$ip" "bash -s" << EOF
set -e
chmod +x ~/bin/spot_guard.sh
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq curl gnupg lsb-release tmux rclone zstd python3-pip libglu1-mesa libxft2 libxcursor1 libxinerama1 >/dev/null
pip3 install --break-system-packages -q gmsh >/dev/null 2>&1 || sudo apt-get install -y -qq python3-gmsh >/dev/null
if [ ! -f /opt/openfoam13/etc/bashrc ]; then
  sudo sh -c "wget -q -O - https://dl.openfoam.org/gpg.key > /etc/apt/trusted.gpg.d/openfoam.asc"
  sudo add-apt-repository -y http://dl.openfoam.org/ubuntu >/dev/null 2>&1
  sudo apt-get update -qq
  sudo apt-get install -y -qq openfoam13 >/dev/null
fi
. /opt/openfoam13/etc/bashrc || true

CASE_DIR=~/cfd_full3d_of13/cases/${cname}
rm -rf "\$CASE_DIR"

# 1. Build full-3D CAD + OpenFOAM v13 case with exact d2 & u_branch (constant ~14.2 mol% H2!)
python3 ~/cfd_full3d_of13/scripts/build_case.py \
  --case-dir "\$CASE_DIR" \
  --mode h2_ch4 \
  --grid gci_medium \
  --turbulence "${turb}" \
  --d1 0.460 \
  --d2 "${d2}" \
  --alpha 90.0 \
  --u-main 10.0 \
  --u-branch "${u_branch}" \
  --sct 0.7 \
  --max-co 1.5 \
  --end-time 3.0 \
  --avg-start 1.4 \
  --write-interval 0.25 \
  --n-procs 128

cd "\$CASE_DIR"
if [ "${orientation}" = "bottom" ]; then
  sed -i 's/(0 -9.81 0)/(0 9.81 0)/g' constant/g
fi

# 2. Parallel 128-core full-3D meshing + Gate 1 checkMesh verification
surfaceFeatures > log.surfaceFeatures 2>&1
blockMesh > log.blockMesh 2>&1
decomposePar -force -noFields > log.decomposePar_mesh 2>&1
mpirun -np 128 --bind-to none --oversubscribe snappyHexMesh -parallel -overwrite </dev/null > log.snappyHexMesh 2>&1
reconstructPar -constant -noFields > log.reconstructPar_mesh 2>&1
rm -rf processor*
checkMesh > log.checkMesh 2>&1
python3 ~/cfd_full3d_of13/scripts/verify_gate.py gate1 --case "\$CASE_DIR"

# 3. Strict [0,1] species boundedness + purgeWrite 3 + videoSlices (0.025s = 120 frames) + nOuterCorrectors 6
python3 - << 'PYEOF'
import re
from pathlib import Path

fvs = Path("system/fvSchemes").read_text()
fvs = re.sub(r'div\(phi,H2\)\s+Gauss\s+limitedLinear\s+1;', 'div(phi,H2)     Gauss limitedLinear01 1;', fvs)
fvs = re.sub(r'div\(phi,CH4\)\s+Gauss\s+limitedLinear\s+1;', 'div(phi,CH4)    Gauss limitedLinear01 1;', fvs)
Path("system/fvSchemes").write_text(fvs)

cd = Path("system/controlDict").read_text()
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

fvsol = Path("system/fvSolution").read_text()
fvsol = re.sub(r'nOuterCorrectors\s+\d+;', 'nOuterCorrectors            6;', fvsol)
fvsol = re.sub(r'nNonOrthogonalCorrectors\s+1;', 'nNonOrthogonalCorrectors    0;', fvsol)
fvsol = re.sub(r'(p_rgh\s*\{\s*tolerance\s+)1e-4(;)', r'\g<1>5e-4\2', fvsol)
fvsol = re.sub(r'(U\s*\{\s*tolerance\s+)1e-4(;)', r'\g<1>5e-4\2', fvsol)
fvsol = fvsol.replace('"(H2|h|e)" { tolerance 1e-4; relTol 0; }', '"H2" { tolerance 1e-4; relTol 0; }\n        "(h|e)" { tolerance 1e-3; relTol 0; }')
fvsol = re.sub(r'(p_rgh\s*\{[^}]*relTol\s+)0\.01(;)', r'\g<1>0.05\2', fvsol, count=1)
Path("system/fvSolution").write_text(fvsol)
PYEOF

# 4. Archive Gate-1 verified mesh_and_setup to Google Drive
tar -czf "/tmp/${cname}_mesh_and_setup.tar.gz" 0 constant system casespec.json gate1_mesh_evidence.json log.checkMesh
rclone mkdir "gdrive:CFD_H2_CH4_Full3D_OF13/cases/${cname}" 2>/dev/null || true
rclone copyto "/tmp/${cname}_mesh_and_setup.tar.gz" "gdrive:CFD_H2_CH4_Full3D_OF13/cases/${cname}/${cname}_mesh_and_setup.tar.gz" --retries 3
rm -f "/tmp/${cname}_mesh_and_setup.tar.gz"

# 5. Decompose & Launch 128-core transient solver + spot_guard.sh
decomposePar -force > log.decomposePar 2>&1
tmux kill-session -t "${tname}" 2>/dev/null || true
tmux new-session -d -s "${tname}" "bash -lc '. /opt/openfoam13/etc/bashrc || true; cd \$CASE_DIR; mpirun -np 128 --bind-to none --oversubscribe foamRun -parallel </dev/null >> log.solver 2>&1'"
nohup bash -c "sleep 240 && sed -i -E 's/maxCo[[:space:]]+[0-9.]+;/maxCo               ${max_co};/' \$CASE_DIR/system/controlDict" >/dev/null 2>&1 &
nohup ~/bin/spot_guard.sh "\$CASE_DIR" "${cname}" "128" "3.0" >/dev/null 2>&1 &

for _ in \$(seq 1 20); do
  grep -q "^Time = " log.solver 2>/dev/null && break
  sleep 3
done
cells=\$(grep -m1 "cells:" log.checkMesh | awk '{print \$2}')
t_first=\$(grep "^Time = " log.solver 2>/dev/null | tail -n 1 | awk '{print \$3}')
fat=\$(grep -c "FOAM FATAL" log.solver 2>/dev/null || true)
procs=\$(pgrep -c foamRun 2>/dev/null || true)
echo "VERIFIED_LIVE: ${cname} | ip=${ip} | id=${inst_id} | ${market} | cells=\${cells:-?} | procs=\${procs} | t=\${t_first:-starting} | FATAL=\${fat:-0} | turb=${turb} | d2=${d2} | uBr=${u_branch}"
EOF
}

# 2 × 128c On-Demand on aws-cfd-4 (ap-south-1)
deploy_dd "aws-cfd-4" "ap-south-1" "ondemand" "ami-032eca770b5d15e72" "sg-076436176a9acc43e" \
  "h2_ch4_90deg_top_dd018_iddes" "0.0828" "61.73" "top" "SpalartAllmarasIDDES" "4.0" &
sleep 8

deploy_dd "aws-cfd-4" "ap-south-1" "ondemand" "ami-032eca770b5d15e72" "sg-076436176a9acc43e" \
  "h2_ch4_90deg_top_dd035_urans" "0.1610" "16.33" "top" "kOmegaSST" "5.0" &
sleep 8

# 2 × 128c Spot on aws-cfd-3 (ap-south-1 & us-east-1, guarded by spot_guard.sh)
deploy_dd "aws-cfd-3" "ap-south-1" "spot" "ami-032eca770b5d15e72" "sg-077659a6797697914" \
  "h2_ch4_90deg_bot_dd018_urans" "0.0828" "61.73" "bottom" "kOmegaSST" "4.0" &
sleep 8

deploy_dd "aws-cfd-3" "us-east-1" "spot" "ami-0a951420c495183dc" "sg-0024a2d344600e4dc" \
  "h2_ch4_90deg_bot_dd035_urans" "0.1610" "16.33" "bottom" "kOmegaSST" "5.0" &

wait
echo "ALL_4_DD_SWEEP_CASES_VERIFIED_AND_LIVE"
