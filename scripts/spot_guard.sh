#!/usr/bin/env bash
# =============================================================================
# spot_guard.sh — Hardened v2 OpenFOAM Spot Interruption Catcher & Sync Daemon
# =============================================================================
# Runs alongside foamRun on an AWS Spot node.
# Safeguards:
#   1. IMDSv2 session-token polling every 5 s for AWS 2-min Spot termination notice
#   2. Also supports canary test via: touch /tmp/trigger_fake_reclaim
#   3. Sets stopAt writeNow; and waits for foamRun to exit cleanly (up to 65 s)
#   4. Single-file zstd -T0 -1 archive (zero Google Drive rate-limit risk)
#   5. Atomic two-phase commit: upload ckpt_*.tar.zst FIRST, then LATEST.json
#   6. Periodic sync of every new completed writeInterval checkpoint (purgeWrite 3)
#   7. Automatic final reconstruction & DONE.json upload on clean End
# =============================================================================
set -u

CASE_DIR="${1:?Usage: spot_guard.sh <CASE_DIR> <CASE_NAME> <NPROCS> <END_TIME>}"
CASE_NAME="${2:?Missing CASE_NAME}"
NPROCS="${3:?Missing NPROCS}"
END_TIME="${4:-3.0}"

GDRIVE_ROOT="gdrive:CFD_H2_CH4_Full3D_OF13/cases/${CASE_NAME}"
GDRIVE_LIVE="${GDRIVE_ROOT}/live_backup"
LAST_RANK=$((NPROCS - 1))
STATE_FILE="/tmp/spot_guard_${CASE_NAME}.last_synced"
LOG_FILE="/tmp/spot_guard_${CASE_NAME}.log"
FAKE_TRIGGER="/tmp/trigger_fake_reclaim"

log() {
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [spot_guard:${CASE_NAME}] $*" | tee -a "$LOG_FILE"
}

# Fetch IMDSv2 token (returns empty if IMDS unavailable)
IMDS_TOKEN=""
IMDS_TOKEN_TS=0
refresh_imds_token() {
  local now
  now=$(date +%s)
  if [ -z "$IMDS_TOKEN" ] || [ $((now - IMDS_TOKEN_TS)) -gt 18000 ]; then
    IMDS_TOKEN=$(curl -s -m 2 -X PUT "http://169.254.169.254/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" 2>/dev/null || true)
    IMDS_TOKEN_TS=$now
  fi
}

check_spot_interruption() {
  if [ -f "$FAKE_TRIGGER" ]; then
    log "CANARY TEST: Detected $FAKE_TRIGGER!"
    return 0
  fi
  refresh_imds_token
  [ -n "$IMDS_TOKEN" ] || return 1
  local http_code
  http_code=$(curl -s -m 2 -o /dev/null -w "%{http_code}" \
    -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
    "http://169.254.169.254/latest/meta-data/spot/instance-action" 2>/dev/null || echo "000")
  [ "$http_code" = "200" ]
}

# Return newest completed time directory > 0 that exists on both rank 0 and rank N-1
newest_completed_time() {
  local t
  for t in $(ls -d "${CASE_DIR}"/processor0/[0-9]* 2>/dev/null | xargs -r -n1 basename | grep -v '^0$' | sort -Vr); do
    if [ -f "${CASE_DIR}/processor0/${t}/H2" ] && \
       [ -f "${CASE_DIR}/processor0/${t}/U" ] && \
       [ -f "${CASE_DIR}/processor0/${t}/p_rgh" ] && \
       [ -f "${CASE_DIR}/processor${LAST_RANK}/${t}/H2" ] && \
       [ -f "${CASE_DIR}/processor${LAST_RANK}/${t}/U" ] && \
       [ -f "${CASE_DIR}/processor${LAST_RANK}/${t}/p_rgh" ]; then
      echo "$t"
      return 0
    fi
  done
  return 1
}

# Atomic two-phase checkpoint upload: 1 single .tar.zst -> then LATEST.json
upload_checkpoint() {
  local t="$1"
  local reason="$2"
  local fname="ckpt_${t}_np${NPROCS}.tar.zst"
  local tmp_tar="/tmp/${CASE_NAME}_${fname}"
  local tmp_json="/tmp/${CASE_NAME}_LATEST.json"

  log "Packing checkpoint t=${t} (np=${NPROCS}, reason=${reason})..."
  local t0 t1
  t0=$(date +%s)

  # Collect items safely (postProcessing + log.solver + controlDict + processor*/t)
  local extras=()
  [ -d "${CASE_DIR}/postProcessing" ] && extras+=("postProcessing")
  [ -f "${CASE_DIR}/log.solver" ] && extras+=("log.solver")
  [ -f "${CASE_DIR}/system/controlDict" ] && extras+=("system/controlDict")

  if ! (cd "$CASE_DIR" && tar -I 'zstd -T0 -1' -cf "$tmp_tar" "${extras[@]}" processor*/"${t}" 2>>"$LOG_FILE"); then
    log "ERROR: tar/zstd failed for t=${t}"
    rm -f "$tmp_tar"
    return 1
  fi

  local size_mb
  size_mb=$(du -m "$tmp_tar" | awk '{print $1}')
  log "Uploading ${fname} (${size_mb} MB) to ${GDRIVE_LIVE}/..."

  rclone mkdir "$GDRIVE_LIVE" 2>>"$LOG_FILE" || true
  if ! rclone copyto "$tmp_tar" "${GDRIVE_LIVE}/${fname}" --transfers 4 --retries 3 --low-level-retries 5 2>>"$LOG_FILE"; then
    log "ERROR: rclone upload failed for ${fname}"
    rm -f "$tmp_tar"
    return 1
  fi
  rm -f "$tmp_tar"

  # Phase 2: Atomic pointer update ONLY after archive upload succeeded
  cat > "$tmp_json" << EOF
{
  "case_name": "${CASE_NAME}",
  "time": "${t}",
  "n_procs": ${NPROCS},
  "archive": "${fname}",
  "size_mb": ${size_mb},
  "reason": "${reason}",
  "timestamp_utc": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
}
EOF
  if rclone copyto "$tmp_json" "${GDRIVE_LIVE}/LATEST.json" --retries 3 2>>"$LOG_FILE"; then
    t1=$(date +%s)
    echo "$t" > "$STATE_FILE"
    log "COMMITTED LATEST.json (t=${t}, ${size_mb} MB, elapsed=$((t1 - t0))s)"
    rm -f "$tmp_json"
    return 0
  else
    log "ERROR: Failed to upload LATEST.json"
    rm -f "$tmp_json"
    return 1
  fi
}

handle_interruption() {
  log "SPOT INTERRUPTION DETECTED! Triggering graceful stopAt writeNow..."
  sed -i -E 's/stopAt[[:space:]]+[a-zA-Z]+;/stopAt          writeNow;/' "${CASE_DIR}/system/controlDict"

  # Wait up to 65 s for foamRun to write current timestep and exit cleanly
  local waited=0
  while [ $waited -lt 65 ]; do
    if ! pgrep -x foamRun >/dev/null 2>&1; then
      log "foamRun exited cleanly after ${waited}s!"
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done

  if pgrep -x foamRun >/dev/null 2>&1; then
    log "WARNING: foamRun still running after 65s; proceeding with newest completed checkpoint."
  fi

  local t_new
  t_new=$(newest_completed_time || true)
  if [ -n "$t_new" ]; then
    upload_checkpoint "$t_new" "spot_reclaim" || true
  else
    log "WARNING: No completed checkpoint found > 0 to upload."
  fi

  rm -f "$FAKE_TRIGGER"
  log "Interruption handler complete. Exiting spot_guard."
  exit 0
}

handle_clean_finish() {
  log "Detected clean solver completion (End at t >= ${END_TIME}). Archiving final results..."
  . /opt/openfoam13/etc/bashrc || true
  cd "$CASE_DIR" || return 1

  local t_final
  t_final=$(newest_completed_time || echo "$END_TIME")

  # Reconstruct latestTime (includes H2Mean, UMean, H2, U, p, p_rgh, T, rho)
  reconstructPar -time "$t_final" >> "$LOG_FILE" 2>&1 || true

  local final_tar="/tmp/${CASE_NAME}_final_t${t_final}.tar.zst"
  tar -I 'zstd -T0 -1' -cf "$final_tar" "$t_final" postProcessing log.* gate*.json 0 constant system 2>>"$LOG_FILE" || \
    tar -I 'zstd -T0 -1' -cf "$final_tar" "$t_final" postProcessing log.solver 0 constant system 2>>"$LOG_FILE"
  local size_mb
  size_mb=$(du -m "$final_tar" | awk '{print $1}')

  rclone copyto "$final_tar" "${GDRIVE_ROOT}/${CASE_NAME}_final_t${t_final}.tar.zst" --transfers 4 --retries 3 2>>"$LOG_FILE"
  rm -f "$final_tar"

  local done_json="/tmp/${CASE_NAME}_DONE.json"
  cat > "$done_json" << EOF
{
  "case_name": "${CASE_NAME}",
  "final_time": "${t_final}",
  "n_procs": ${NPROCS},
  "final_archive": "${CASE_NAME}_final_t${t_final}.tar.zst",
  "size_mb": ${size_mb},
  "timestamp_utc": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
}
EOF
  rclone copyto "$done_json" "${GDRIVE_ROOT}/DONE.json" --retries 3 2>>"$LOG_FILE"
  rm -f "$done_json"
  log "FINAL ARCHIVE COMMITTED: ${CASE_NAME}_final_t${t_final}.tar.zst (${size_mb} MB)"
}

# --- Main Loop ---
log "Started spot_guard (CASE_DIR=${CASE_DIR}, NPROCS=${NPROCS}, END_TIME=${END_TIME})"
LAST_PERIODIC=0

while true; do
  # 1. Highest priority: check AWS 2-min Spot termination notice every 5 s
  if check_spot_interruption; then
    handle_interruption
  fi

  now=$(date +%s)

  # 2. Periodic checkpoint sync every 60 s
  if [ $((now - LAST_PERIODIC)) -ge 60 ]; then
    LAST_PERIODIC=$now
    last_synced=$(cat "$STATE_FILE" 2>/dev/null || echo "")
    t_latest=$(newest_completed_time || true)
    if [ -n "$t_latest" ] && [ "$t_latest" != "$last_synced" ]; then
      # Only upload if foamRun is not actively modifying processor0/t_latest (age >= 5 s)
      age=$(( now - $(stat -c %Y "${CASE_DIR}/processor${LAST_RANK}/${t_latest}/H2" 2>/dev/null || echo "$now") ))
      if [ "$age" -ge 5 ]; then
        upload_checkpoint "$t_latest" "periodic" || true
      fi
    fi

    # 3. Check if solver finished cleanly (End reached and foamRun exited)
    if ! pgrep -x foamRun >/dev/null 2>&1; then
      if [ -f "${CASE_DIR}/log.solver" ] && grep -q "^End" "${CASE_DIR}/log.solver"; then
        if ! grep -q "FOAM FATAL" "${CASE_DIR}/log.solver"; then
          t_log=$(grep "^Time = " "${CASE_DIR}/log.solver" | tail -n 1 | awk '{print $3}' | sed 's/s$//')
          reached=$(python3 -c "print(1 if float('${t_log:-0}') >= float('${END_TIME}') - 1e-4 else 0)" 2>/dev/null || echo 0)
          if [ "$reached" = "1" ]; then
            handle_clean_finish
            exit 0
          fi
        fi
      fi
    fi
  fi

  sleep 5
done
