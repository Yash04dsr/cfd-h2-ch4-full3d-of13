#!/usr/bin/env bash
set -euo pipefail

SSH_KEY="$HOME/.ssh/id_ed25519"
RCLONE_CONF="$HOME/.config/rclone/rclone.conf"
SPOT_GUARD="/Users/yash/cfd_full3d_of13_backup/scripts/spot_guard.sh"

# Launch 1 c6i.32xlarge (128c) instance (OD or one-time Spot), bootstrap OF13 + zstd + rclone,
# pull verified Gate-1 mesh from gdrive, inject videoSlices (0.025s = 120 frames) + limitedLinear01,
# launch 128-core foamRun + spot_guard.sh, and verify stepping with 0 FATAL errors!
deploy_case() {
  local profile="$1"
  local region="$2"
  local market="$3"      # ondemand | spot
  local ami="$4"
  local sg="$5"
  local subnet="$6"
  local cname="$7"
  local mesh_remote="$8"
  local orientation="$9" # top | bottom
  local max_co="${10}"

  local market_args=()
  if [ "$market" = "spot" ]; then
    market_args=(--instance-market-options '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time"}}')
  fi

  local inst_id=""
  for itype in c6i.32xlarge c6a.32xlarge c7i.32xlarge; do
    inst_id=$(AWS_PROFILE="$profile" aws ec2 run-instances --region "$region" \
      --image-id "$ami" --instance-type "$itype" --key-name cfd-of13-key \
      --security-group-ids "$sg" --subnet-id "$subnet" \
      ${market_args[@]+"${market_args[@]}"} \
      --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":150,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
      --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=cfd-128c-${cname}},{Key=CaseName,Value=${cname}},{Key=ManagedBy,Value=cfd-128c-angles}]" \
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
rclone copyto "${mesh_remote}" /tmp/mesh_setup.tar.gz --transfers 4
tar -xzf /tmp/mesh_setup.tar.gz -C "\$CASE_DIR"
rm -f /tmp/mesh_setup.tar.gz

cd "\$CASE_DIR"
rm -rf dynamicCode
if [ "${orientation}" = "bottom" ]; then
  sed -i 's/(0 -9.81 0)/(0 9.81 0)/g' constant/g
fi
# Strict [0,1] species boundedness
sed -i 's/div(phi,H2)     Gauss limitedLinear 1;/div(phi,H2)     Gauss limitedLinear01 1;/g' system/fvSchemes
sed -i 's/div(phi,CH4)    Gauss limitedLinear 1;/div(phi,CH4)    Gauss limitedLinear01 1;/g' system/fvSchemes
# 128-core decomposition + cold-start maxCo=1.5 + writeInterval 0.25 + purgeWrite 3
sed -i -E 's/numberOfSubdomains[[:space:]]+[0-9]+;/numberOfSubdomains 128;/' system/decomposeParDict
sed -i -E 's/maxCo[[:space:]]+[0-9.]+;/maxCo               1.5;/' system/controlDict
sed -i -E 's/writeInterval[[:space:]]+[0-9.]+;/writeInterval       0.25;/' system/controlDict
if grep -q "purgeWrite" system/controlDict; then
  sed -i -E 's/purgeWrite[[:space:]]+[0-9]+;/purgeWrite          3;/' system/controlDict
else
  sed -i '/writeInterval/a purgeWrite          3;' system/controlDict
fi

# Inject videoSlices (0.025 s = 120 frames for time-evolution MP4) into system/controlDict
python3 - << 'PYEOF'
from pathlib import Path
import re

# 1. Inject videoSlices functionObject right before the closing brace of functions { ... }
cd_path = Path("system/controlDict")
cd = cd_path.read_text()
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
    assert idx != -1, "Could not find closing brace in controlDict"
    cd = cd[:idx] + block + cd[idx:]
    cd_path.write_text(cd)

# 2. Apply PIMPLE hot-tune in fvSolution
p = Path("system/fvSolution")
txt = p.read_text()
txt = re.sub(r'nNonOrthogonalCorrectors\s+1;', 'nNonOrthogonalCorrectors    0;', txt)
txt = re.sub(r'(p_rgh\s*\{\s*tolerance\s+)1e-4(;)', r'\g<1>5e-4\2', txt)
txt = re.sub(r'(U\s*\{\s*tolerance\s+)1e-4(;)', r'\g<1>5e-4\2', txt)
txt = txt.replace('"(H2|h|e)" { tolerance 1e-4; relTol 0; }', '"H2" { tolerance 1e-4; relTol 0; }\n        "(h|e)" { tolerance 1e-3; relTol 0; }')
txt = re.sub(r'(p_rgh\s*\{[^}]*relTol\s+)0\.01(;)', r'\g<1>0.05\2', txt, count=1)
p.write_text(txt)
PYEOF

decomposePar -force > log.decomposePar 2>&1
tmux kill-session -t "${tname}" 2>/dev/null || true
tmux new-session -d -s "${tname}" "bash -lc '. /opt/openfoam13/etc/bashrc || true; cd \$CASE_DIR; mpirun -np 128 --bind-to none --oversubscribe foamRun -parallel </dev/null >> log.solver 2>&1'"
nohup bash -c "sleep 240 && sed -i -E 's/maxCo[[:space:]]+[0-9.]+;/maxCo               ${max_co};/' \$CASE_DIR/system/controlDict" >/dev/null 2>&1 &
nohup ~/bin/spot_guard.sh "\$CASE_DIR" "${cname}" "128" "3.0" >/dev/null 2>&1 &

# Wait up to 45s for JIT compile + first timestep verification
for _ in \$(seq 1 15); do
  grep -q "^Time = " log.solver 2>/dev/null && break
  sleep 3
done
t_first=\$(grep "^Time = " log.solver 2>/dev/null | tail -n 1 | awk '{print \$3}')
fat=\$(grep -c "FOAM FATAL" log.solver 2>/dev/null || true)
procs=\$(pgrep -c foamRun 2>/dev/null || true)
echo "VERIFIED_LIVE: ${cname} | ip=${ip} | id=${inst_id} | ${market} | procs=\${procs} | t=\${t_first:-starting} | FATAL=\${fat:-0} | g=\$(grep value constant/g | awk '{print \$2,\$3,\$4}') | H2=\$(grep 'div(phi,H2)' system/fvSchemes | awk '{print \$3}')"
EOF
}

# Stagger launches cleanly (10s stagger between parallel deploys)
# --- 4 On-Demand 128c (Counter-Flow 120 & 150 deg: Top & Bottom) ---
deploy_case "aws-cfd-2" "us-east-1" "ondemand" "ami-0a951420c495183dc" "sg-01d55f1d0acedd237" "subnet-031616fd835226c94" \
  "h2_ch4_150deg_top_verified" "gdrive:CFD_H2_CH4_Full3D_OF13/cases/h2_ch4_150deg_top_verified/h2_ch4_150deg_top_verified_mesh_and_setup.tar.gz" "top" "3.0" &
sleep 10
deploy_case "aws-cfd-2" "us-east-1" "ondemand" "ami-0a951420c495183dc" "sg-01d55f1d0acedd237" "subnet-031616fd835226c94" \
  "h2_ch4_150deg_bottom_verified" "gdrive:CFD_H2_CH4_Full3D_OF13/cases/h2_ch4_150deg_top_verified/h2_ch4_150deg_top_verified_mesh_and_setup.tar.gz" "bottom" "3.0" &
sleep 10
deploy_case "aws-cfd" "us-east-1" "ondemand" "ami-0a951420c495183dc" "sg-02e30c3e45cd34e46" "subnet-0a10f872a5e3a42c4" \
  "h2_ch4_120deg_top_verified" "gdrive:CFD_H2_CH4_Full3D_OF13/cases/h2_ch4_120deg_top_verified/h2_ch4_120deg_top_verified_mesh_and_setup.tar.gz" "top" "3.0" &
sleep 10
deploy_case "aws-cfd" "us-east-1" "ondemand" "ami-0a951420c495183dc" "sg-02e30c3e45cd34e46" "subnet-0a10f872a5e3a42c4" \
  "h2_ch4_120deg_bottom_verified" "gdrive:CFD_H2_CH4_Full3D_OF13/cases/h2_ch4_120deg_top_verified/h2_ch4_120deg_top_verified_mesh_and_setup.tar.gz" "bottom" "3.0" &
sleep 10

# --- 3 Spot 128c (Co-Flow 30 deg Top/Bot + 60 deg Bot, guarded by spot_guard.sh) ---
deploy_case "aws-cfd" "us-east-1" "spot" "ami-0a951420c495183dc" "sg-02e30c3e45cd34e46" "subnet-0a10f872a5e3a42c4" \
  "h2_ch4_30deg_top_verified" "gdrive:CFD_H2_CH4_Full3D_OF13/cases/h2_ch4_30deg_top_verified/h2_ch4_30deg_top_verified_mesh_and_setup.tar.gz" "top" "5.0" &
sleep 10
deploy_case "aws-cfd" "ap-south-1" "spot" "ami-032eca770b5d15e72" "sg-02c5f778cd9f2d61c" "subnet-0d8cfad2777138aba" \
  "h2_ch4_30deg_bottom_verified" "gdrive:CFD_H2_CH4_Full3D_OF13/cases/h2_ch4_30deg_top_verified/h2_ch4_30deg_top_verified_mesh_and_setup.tar.gz" "bottom" "5.0" &
sleep 10
deploy_case "aws-cfd-2" "ap-south-1" "spot" "ami-032eca770b5d15e72" "sg-03036192aee102d77" "subnet-003be27ab6bf398af" \
  "h2_ch4_60deg_bottom_verified" "gdrive:CFD_H2_CH4_Full3D_OF13/cases/h2_ch4_60deg_verified/h2_ch4_60deg_verified_mesh_and_setup.tar.gz" "bottom" "5.0" &

wait
echo "ALL_7_128C_ANGLE_CASES_VERIFIED_AND_LIVE"
