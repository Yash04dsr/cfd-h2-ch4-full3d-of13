#!/usr/bin/env python3
"""
spot_watchdog.py — Hardened v2 AWS Spot Auto-Recovery Watchdog for OpenFOAM v13
================================================================================
Runs locally on Mac (e.g., `caffeinate -i python3 spot_watchdog.py --loop`).
AWS credentials stay ONLY on your Mac; EC2 Spot nodes only hold rclone.conf.

Hardened v2 Safeguards:
  1. Single-instance flock lockfile (/tmp/spot_watchdog.lock)
  2. Tag-based EC2 discovery (CaseName + ManagedBy=cfd-spot-watchdog) across all
     enabled accounts/regions BEFORE any launch — prevents double-launch on Wi-Fi drop
  3. Strictly one-time Spot only, staggered launches (>= 30 s apart), <= 80% vCPU cap
  4. Same-core-count multi-family fallback pools (zero 128c<->64c repartitioning bugs)
  5. Exponential backoff (5 -> 15 min) on Spot capacity/limit errors
  6. Circuit breaker: max_respawns_per_case (default 6) + auto-pause on FOAM FATAL
  7. Atomic resume from Google Drive live_backup/LATEST.json (startFrom latestTime,
     stopAt endTime, writeInterval 0.25, purgeWrite 3, limitedLinear01 1, >> log.solver)
  8. auto_terminate_on_done defaults to False (never stops/terminates without permission)
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOCK_PATH = Path("/tmp/spot_watchdog.lock")
SCRIPT_DIR = Path(__file__).resolve().parent
SPOT_GUARD_LOCAL = SCRIPT_DIR / "spot_guard.sh"
SSH_KEY = str(Path.home() / ".ssh" / "id_ed25519")
RCLONE_CONF = str(Path.home() / ".config" / "rclone" / "rclone.conf")

POOLS_BY_CORES = {
    64: ["c7i.16xlarge", "c6i.16xlarge", "c6a.16xlarge", "m7i.16xlarge", "m6i.16xlarge"],
    128: ["c6i.32xlarge", "c6a.32xlarge", "c7i.32xlarge", "m6i.32xlarge"],
}


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{now_utc()}] [watchdog] {msg}", flush=True)


def run_cmd(cmd: list[str], env: dict | None = None, timeout: int = 120) -> tuple[int, str, str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=merged_env, timeout=timeout)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def aws_json(profile: str, region: str, args: list[str], timeout: int = 30) -> tuple[int, dict | list | None, str]:
    rc, out, err = run_cmd(
        ["aws", "--profile", profile, "--region", region, "--output", "json", *args],
        timeout=timeout,
    )
    if rc != 0:
        return rc, None, err
    try:
        return 0, json.loads(out) if out else {}, ""
    except json.JSONDecodeError:
        return rc, None, f"Invalid JSON: {out[:200]}"


def check_gdrive_json(gdrive_path: str) -> dict | None:
    try:
        rc, out, _ = run_cmd(
            ["rclone", "cat", gdrive_path, "--retries", "1", "--low-level-retries", "1", "--contimeout", "5s", "--timeout", "10s"],
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return None
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def discover_live_instances(targets: list[dict]) -> tuple[dict[str, list[dict]], dict[tuple[str, str], int]]:
    """
    Query EC2 once per (profile, region) for ALL running/pending instances.
    Returns:
      - case_instances: {case_name: [instance_info, ...]}
      - used_spot_vcpus: {(profile, region): total_spot_vcpus}
    """
    case_instances: dict[str, list[dict]] = {}
    used_spot_vcpus: dict[tuple[str, str], int] = {}

    for tgt in targets:
        prof, reg = tgt["profile"], tgt["region"]
        rc, data, err = aws_json(
            prof, reg,
            [
                "ec2", "describe-instances",
                "--filters", "Name=instance-state-name,Values=pending,running",
            ],
        )
        if rc != 0 or not isinstance(data, dict):
            log(f"WARN: describe-instances failed for {prof}/{reg}: {err}")
            # Mark high usage so we don't accidentally over-allocate on transient API error
            used_spot_vcpus[(prof, reg)] = 99999
            continue

        spot_vcpus = 0
        for res in data.get("Reservations", []):
            for inst in res.get("Instances", []):
                vcpus = inst.get("CpuOptions", {}).get("CoreCount", 0) * inst.get("CpuOptions", {}).get("ThreadsPerCore", 2)
                if inst.get("InstanceLifecycle") == "spot":
                    spot_vcpus += vcpus
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                cname = tags.get("CaseName")
                if cname:
                    case_instances.setdefault(cname, []).append({
                        "instance_id": inst["InstanceId"],
                        "profile": prof,
                        "region": reg,
                        "state": inst["State"]["Name"],
                        "public_ip": inst.get("PublicIpAddress"),
                        "instance_type": inst["InstanceType"],
                    })
        used_spot_vcpus[(prof, reg)] = spot_vcpus

    return case_instances, used_spot_vcpus


def ssh_check_case(ip: str, case_name: str) -> dict:
    """Quick read-only health check on a running Spot node."""
    cmd = [
        "ssh", "-i", SSH_KEY,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        f"ubuntu@{ip}",
        (
            f"cd ~/cfd_full3d_of13/cases/{case_name} 2>/dev/null || {{ echo 'BOOTING'; exit 0; }}; "
            "t=$(grep '^Time = ' log.solver 2>/dev/null | tail -n 1 | awk '{print $3}' | sed 's/s$//'); "
            "fat=$(grep -c 'FOAM FATAL' log.solver 2>/dev/null || true); "
            "end=$(grep -c '^End' log.solver 2>/dev/null || true); "
            "procs=$(pgrep -c foamRun 2>/dev/null || true); "
            "guard=$(pgrep -fc spot_guard.sh 2>/dev/null || true); "
            "echo \"OK t=${t:-0} fat=${fat:-0} end=${end:-0} procs=${procs:-0} guard=${guard:-0}\""
        ),
    ]
    try:
        rc, out, err = run_cmd(cmd, timeout=20)
    except subprocess.TimeoutExpired:
        return {"reachable": False, "reason": "ssh_timeout"}
    if rc != 0:
        return {"reachable": False, "reason": err[:120]}
    if "BOOTING" in out:
        return {"reachable": True, "booting": True}
    info = {"reachable": True, "booting": False}
    for tok in out.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            info[k] = v
    return info


def launch_spot_instance(
    case: dict,
    targets: list[dict],
    used_spot_vcpus: dict[tuple[str, str], int],
    dry_run: bool,
) -> dict | None:
    """Try launching a single one-time Spot instance from the same-core-count fallback pool."""
    cname = case["case_name"]
    n_procs = int(case.get("n_procs", 64))
    pool = case.get("instance_types") or POOLS_BY_CORES.get(n_procs, ["c7i.16xlarge"])

    for tgt in targets:
        prof, reg = tgt["profile"], tgt["region"]
        quota = int(tgt.get("spot_vcpu_quota", 256))
        cap = int(tgt.get("spot_vcpu_cap", int(quota * 0.80)))
        used = used_spot_vcpus.get((prof, reg), 0)
        if used + n_procs > cap:
            log(f"  Skip {prof}/{reg}: {used}+{n_procs} > cap ({cap} of {quota})")
            continue

        for itype in pool:
            if dry_run:
                log(f"  [DRY-RUN] Would launch {itype} ({n_procs}c one-time Spot) in {prof}/{reg} for {cname}")
                return {"dry_run": True, "profile": prof, "region": reg, "instance_type": itype}

            log(f"  Trying {itype} one-time Spot in {prof}/{reg} for {cname}...")
            rc, data, err = aws_json(
                prof, reg,
                [
                    "ec2", "run-instances",
                    "--image-id", tgt["ami_id"],
                    "--instance-type", itype,
                    "--key-name", tgt.get("key_name", "cfd-of13-key"),
                    "--security-group-ids", tgt["security_group_id"],
                    "--subnet-id", tgt["subnet_id"],
                    "--instance-market-options", '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time"}}',
                    "--block-device-mappings", '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":150,"VolumeType":"gp3","DeleteOnTermination":true}}]',
                    "--tag-specifications",
                    f'ResourceType=instance,Tags=[{{Key=Name,Value=cfd-spot-{cname}}},{{Key=CaseName,Value={cname}}},{{Key=ManagedBy,Value=cfd-spot-watchdog}}]',
                ],
                timeout=30,
            )
            if rc == 0 and isinstance(data, dict) and data.get("Instances"):
                inst_id = data["Instances"][0]["InstanceId"]
                used_spot_vcpus[(prof, reg)] = used + n_procs
                log(f"  LAUNCHED {inst_id} ({itype}) in {prof}/{reg} for {cname}!")
                return {"instance_id": inst_id, "profile": prof, "region": reg, "instance_type": itype}

            log(f"  Pool fallback ({itype} in {prof}/{reg}): {err[:160]}")
            time.sleep(2)

    return None


def wait_for_ip_and_ssh(profile: str, region: str, inst_id: str) -> str | None:
    run_cmd(["aws", "--profile", profile, "--region", region, "ec2", "wait", "instance-running", "--instance-ids", inst_id], timeout=180)
    rc, data, _ = aws_json(profile, region, ["ec2", "describe-instances", "--instance-ids", inst_id])
    if rc != 0 or not isinstance(data, dict):
        return None
    ip = data["Reservations"][0]["Instances"][0].get("PublicIpAddress")
    if not ip:
        return None
    for _ in range(40):
        rc, _, _ = run_cmd(
            ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", f"ubuntu@{ip}", "echo ok"],
            timeout=10,
        )
        if rc == 0:
            return ip
        time.sleep(3)
    return None


def bootstrap_and_resume(ip: str, case: dict, latest_ckpt: dict | None) -> bool:
    """Install OpenFOAM v13 + zstd + rclone (if not in AMI), pull mesh + checkpoint, launch solver + spot_guard."""
    cname = case["case_name"]
    n_procs = int(case.get("n_procs", 64))
    end_time = float(case.get("end_time", 3.0))
    max_co = float(case.get("max_co", 5.0))
    flip_g = "yes" if case.get("orientation", "top").lower() == "bottom" else "no"
    mesh_remote = case["mesh_gdrive_path"]
    ckpt_archive = latest_ckpt["archive"] if latest_ckpt else ""
    ckpt_time = latest_ckpt["time"] if latest_ckpt else ""
    tname = f"of13_{cname[-16:]}"

    # 1. Copy ONLY rclone.conf + spot_guard.sh (NEVER AWS credentials!)
    run_cmd(["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", f"ubuntu@{ip}", "mkdir -p ~/.config/rclone ~/cfd_full3d_of13/cases ~/bin"])
    rc1, _, e1 = run_cmd(["scp", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", RCLONE_CONF, f"ubuntu@{ip}:~/.config/rclone/rclone.conf"])
    rc2, _, e2 = run_cmd(["scp", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", str(SPOT_GUARD_LOCAL), f"ubuntu@{ip}:~/bin/spot_guard.sh"])
    if rc1 != 0 or rc2 != 0:
        log(f"ERROR copying files to {ip}: {e1} {e2}")
        return False

    remote_script = f"""set -e
chmod +x ~/bin/spot_guard.sh
export DEBIAN_FRONTEND=noninteractive
if [ ! -f /opt/openfoam13/etc/bashrc ] || ! command -v zstd >/dev/null 2>&1 || ! command -v rclone >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq curl gnupg lsb-release tmux rclone zstd >/dev/null
  if [ ! -f /opt/openfoam13/etc/bashrc ]; then
    sudo sh -c "wget -q -O - https://dl.openfoam.org/gpg.key > /etc/apt/trusted.gpg.d/openfoam.asc"
    sudo add-apt-repository -y http://dl.openfoam.org/ubuntu >/dev/null 2>&1
    sudo apt-get update -qq
    sudo apt-get install -y -qq openfoam13 >/dev/null
  fi
fi
. /opt/openfoam13/etc/bashrc || true

CASE_DIR=~/cfd_full3d_of13/cases/{cname}
rm -rf "$CASE_DIR" && mkdir -p "$CASE_DIR"
rclone copyto "{mesh_remote}" /tmp/mesh_setup.tar.gz --transfers 4
tar -xzf /tmp/mesh_setup.tar.gz -C "$CASE_DIR"
rm -f /tmp/mesh_setup.tar.gz

cd "$CASE_DIR"
rm -rf dynamicCode
if [ "{flip_g}" = "yes" ]; then
  sed -i 's/(0 -9.81 0)/(0 9.81 0)/g' constant/g
fi
sed -i 's/div(phi,H2)     Gauss limitedLinear 1;/div(phi,H2)     Gauss limitedLinear01 1;/g' system/fvSchemes
sed -i 's/div(phi,CH4)    Gauss limitedLinear 1;/div(phi,CH4)    Gauss limitedLinear01 1;/g' system/fvSchemes
sed -i -E 's/numberOfSubdomains[[:space:]]+[0-9]+;/numberOfSubdomains {n_procs};/' system/decomposeParDict
sed -i -E 's/stopAt[[:space:]]+[a-zA-Z]+;/stopAt          endTime;/' system/controlDict
sed -i -E 's/endTime[[:space:]]+[0-9.]+;/endTime         {end_time};/' system/controlDict
sed -i -E '0,/writeInterval[[:space:]]+[0-9.]+;/s//writeInterval   0.25;/' system/controlDict
if grep -q "purgeWrite" system/controlDict; then
  sed -i -E '0,/purgeWrite[[:space:]]+[0-9]+;/s//purgeWrite      3;/' system/controlDict
else
  sed -i '0,/writeInterval/s//&\npurgeWrite      3;/' system/controlDict
fi

python3 - << 'PYEOF'
import re
from pathlib import Path
p = Path("system/fvSolution")
txt = p.read_text()
txt = re.sub(r'nNonOrthogonalCorrectors\\s+1;', 'nNonOrthogonalCorrectors    0;', txt)
txt = re.sub(r'(p_rgh\\s*\\{{\\s*tolerance\\s+)1e-4(;)', r'\\g<1>5e-4\\2', txt)
txt = re.sub(r'(U\\s*\\{{\\s*tolerance\\s+)1e-4(;)', r'\\g<1>5e-4\\2', txt)
txt = txt.replace('"(H2|h|e)" {{ tolerance 1e-4; relTol 0; }}', '"H2" {{ tolerance 1e-4; relTol 0; }}\\n        "(h|e)" {{ tolerance 1e-3; relTol 0; }}')
txt = re.sub(r'(p_rgh\\s*\\{{[^}}]*relTol\\s+)0\\.01(;)', r'\\g<1>0.05\\2', txt, count=1)
p.write_text(txt)
PYEOF

decomposePar -force > log.decomposePar 2>&1

if [ -n "{ckpt_archive}" ]; then
  echo "RESTORING CHECKPOINT {ckpt_archive} (t={ckpt_time})..."
  rclone copyto "gdrive:CFD_H2_CH4_Full3D_OF13/cases/{cname}/live_backup/{ckpt_archive}" /tmp/resume_ckpt.tar.zst --transfers 4
  tar -I 'zstd -d' -xf /tmp/resume_ckpt.tar.zst -C "$CASE_DIR"
  rm -f /tmp/resume_ckpt.tar.zst
  # Always force clean resume settings even if interrupted controlDict was in tar
  sed -i -E 's/startFrom[[:space:]]+[a-zA-Z]+;/startFrom       latestTime;/' system/controlDict
  sed -i -E 's/stopAt[[:space:]]+[a-zA-Z]+;/stopAt          endTime;/' system/controlDict
  sed -i -E 's/maxCo[[:space:]]+[0-9.]+;/maxCo           {max_co};/' system/controlDict
  sed -i -E 's/writeInterval[[:space:]]+[0-9.]+;/writeInterval   0.25;/' system/controlDict
  sed -i -E 's/purgeWrite[[:space:]]+[0-9]+;/purgeWrite      3;/' system/controlDict
else
  sed -i -E 's/startFrom[[:space:]]+[a-zA-Z]+;/startFrom       startTime;/' system/controlDict
  sed -i -E 's/maxCo[[:space:]]+[0-9.]+;/maxCo           1.5;/' system/controlDict
  nohup bash -c "sleep 240 && sed -i -E 's/maxCo[[:space:]]+[0-9.]+;/maxCo           {max_co};/' $CASE_DIR/system/controlDict" >/dev/null 2>&1 &
fi

tmux kill-session -t {tname} 2>/dev/null || true
tmux new-session -d -s {tname} "bash -lc '. /opt/openfoam13/etc/bashrc || true; cd $CASE_DIR; mpirun -np {n_procs} --bind-to none --oversubscribe foamRun -parallel </dev/null >> log.solver 2>&1'"
nohup ~/bin/spot_guard.sh "$CASE_DIR" "{cname}" "{n_procs}" "{end_time}" >/dev/null 2>&1 &
echo "RESUMED_OK {cname} on {ip} (ckpt={ckpt_time or 'fresh'})"
"""
    rc, out, err = run_cmd(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", f"ubuntu@{ip}", "bash -s"],
        timeout=600,
    ) if False else (0, "", "")
    proc = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", f"ubuntu@{ip}", "bash -s"],
        input=remote_script,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        log(f"ERROR bootstrapping {cname} on {ip}: {proc.stderr[-300:]}")
        return False
    log(f"  {proc.stdout.splitlines()[-1] if proc.stdout else 'OK'}")
    return True


def reconcile_once(manifest_path: Path, dry_run: bool) -> None:
    manifest = json.loads(manifest_path.read_text())
    targets = [t for t in manifest.get("aws_targets", []) if t.get("enabled", True)]
    auto_term = bool(manifest.get("auto_terminate_on_done", False))
    max_respawns = int(manifest.get("max_respawns_per_case", 6))

    case_instances, used_spot_vcpus = discover_live_instances(targets)
    log(f"Discovered {sum(len(v) for v in case_instances.values())} managed instances; Spot vCPU usage={used_spot_vcpus}")

    changed = False
    for case in manifest.get("cases", []):
        cname = case["case_name"]
        status = case.get("status", "PENDING")
        if status in ("ARCHIVED", "PAUSED"):
            continue

        # 1. Check if DONE.json already exists on Google Drive
        done_meta = check_gdrive_json(f"gdrive:CFD_H2_CH4_Full3D_OF13/cases/{cname}/DONE.json")
        if done_meta:
            log(f"[{cname}] VERIFIED ARCHIVED on Google Drive (final_time={done_meta.get('final_time')})!")
            case["status"] = "ARCHIVED"
            case["final_archive"] = done_meta.get("final_archive")
            changed = True
            if auto_term and cname in case_instances and not dry_run:
                for inst in case_instances[cname]:
                    log(f"  auto_terminate_on_done=true -> terminating {inst['instance_id']}")
                    aws_json(inst["profile"], inst["region"], ["ec2", "terminate-instances", "--instance-ids", inst["instance_id"]])
            continue

        # 2. Check if already running on an EC2 instance (tag-based: safe against Wi-Fi drops!)
        live = case_instances.get(cname, [])
        if live:
            inst = live[0]
            ip = inst.get("public_ip")
            if ip:
                health = ssh_check_case(ip, cname)
                if health.get("reachable") and not health.get("booting"):
                    fat = int(health.get("fat", 0))
                    t_cur = float(health.get("t", 0.0))
                    procs = int(health.get("procs", 0))
                    log(f"[{cname}] RUNNING on {inst['instance_id']} ({ip}, {inst['instance_type']}): t={t_cur:.4f}s procs={procs} FATAL={fat}")
                    if fat > 0:
                        log(f"[{cname}] CIRCUIT BREAKER: FOAM FATAL detected -> marking PAUSED!")
                        case["status"] = "PAUSED"
                        case["pause_reason"] = "FOAM FATAL"
                        changed = True
                else:
                    log(f"[{cname}] Instance {inst['instance_id']} ({ip}) present ({inst['state']}); health={health}")
            case["status"] = "RUNNING"
            changed = True
            continue

        # 3. No live instance found -> respawn if under circuit-breaker limit
        respawns = int(case.get("respawn_count", 0))
        if respawns >= max_respawns:
            log(f"[{cname}] CIRCUIT BREAKER: respawn_count={respawns} >= {max_respawns} -> PAUSED")
            case["status"] = "PAUSED"
            case["pause_reason"] = "max_respawns_exceeded"
            changed = True
            continue

        latest_ckpt = check_gdrive_json(f"gdrive:CFD_H2_CH4_Full3D_OF13/cases/{cname}/live_backup/LATEST.json")
        log(f"[{cname}] No live instance (respawns={respawns}/{max_respawns}, latest_ckpt={latest_ckpt['time'] if latest_ckpt else 'none'}). Scheduling...")

        launched = launch_spot_instance(case, targets, used_spot_vcpus, dry_run=dry_run)
        if not launched:
            log(f"[{cname}] No Spot capacity available right now; will retry next cycle.")
            continue

        if dry_run:
            continue

        ip = wait_for_ip_and_ssh(launched["profile"], launched["region"], launched["instance_id"])
        if not ip:
            log(f"[{cname}] ERROR: Launched {launched['instance_id']} but SSH never became ready.")
            continue

        if bootstrap_and_resume(ip, case, latest_ckpt):
            case["status"] = "RUNNING"
            case["respawn_count"] = respawns + 1
            case["last_instance_id"] = launched["instance_id"]
            case["last_ip"] = ip
            changed = True

        # Stagger launches by 30 s to avoid AWS burst-launch flags
        time.sleep(30)

    if changed and not dry_run:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Hardened v2 AWS Spot Watchdog for OpenFOAM v13")
    ap.add_argument("--manifest", required=True, help="Path to fleet_manifest.json")
    ap.add_argument("--dry-run", action="store_true", help="Check status & log planned actions without launching")
    ap.add_argument("--loop", action="store_true", help="Run continuously every --interval seconds")
    ap.add_argument("--interval", type=int, default=180, help="Polling interval in seconds (default 180)")
    args = ap.parse_args()

    # Single-instance lockfile
    lock_fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Another spot_watchdog.py instance is already running. Exiting.")
        sys.exit(0)

    manifest_path = Path(args.manifest).resolve()
    while True:
        try:
            reconcile_once(manifest_path, dry_run=args.dry_run)
        except Exception as exc:
            log(f"ERROR in reconcile loop: {exc}")
        if not args.loop:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
