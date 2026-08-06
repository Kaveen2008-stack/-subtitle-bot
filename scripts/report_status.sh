#!/usr/bin/env bash
# report_status.sh - Reports a job step's status to BOTH Supabase (dashboard)
# and Telegram (edit-in-place progress message), from inside GitHub Actions.
#
# Usage:
#   ./report_status.sh <step> <status> [detail_json]
#
#   step:   short machine name, e.g. "download", "crop_detect", "burn",
#           "upload_pixeldrain", "upload_drive", "upload_voe"
#   status: "started" | "success" | "failed"
#   detail: optional JSON object string, e.g. '{"crop":"1280:582:0:68"}'
#
# Required environment variables (set once per workflow run):
#   SUPABASE_URL
#   SUPABASE_SERVICE_ROLE_KEY   (the sb_secret_... key - server-side only)
#   JOB_ID                      (uuid of the row in the `jobs` table)
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#   TELEGRAM_MESSAGE_ID         (the progress message to edit)
#
# This script NEVER fails the workflow (set -e is intentionally not used
# for the network calls) - a dashboard/Telegram hiccup should never stop
# the actual video processing job.

STEP="$1"
STATUS="$2"
DETAIL_JSON="$3"
# NOTE: avoid the "${3:-{}}" one-liner here - bash's brace-matching for
# parameter expansion gets confused by the literal { } characters inside
# the default value, which can silently corrupt this variable and send
# malformed JSON to Supabase (this was the actual root cause of the
# "Empty or invalid json" / PGRST102 errors seen on real runs).
if [ -z "$DETAIL_JSON" ]; then
  DETAIL_JSON="{}"
fi

if [ -z "$STEP" ] || [ -z "$STATUS" ]; then
  echo "Usage: report_status.sh <step> <status> [detail_json]" >&2
  exit 1
fi

# --- Human-readable labels for the Telegram progress message -----------
declare -A STEP_LABELS=(
  [download]="Downloading video"
  [crop_detect]="Detecting black bars"
  [translate]="Translating subtitles"
  [burn]="Burning subtitles"
  [upload_pixeldrain]="Uploading to Pixeldrain"
  [upload_drive]="Uploading to Google Drive"
  [upload_voe]="Uploading to VOE.sx"
  [webhook_notify]="Notifying website"
)
LABEL="${STEP_LABELS[$STEP]:-$STEP}"

case "$STATUS" in
  started) ICON="🔄" ;;
  success) ICON="✅" ;;
  failed)  ICON="❌" ;;
  *)       ICON="ℹ️" ;;
esac

# --- 1. Report to Supabase (job_events table - always, full detail) ----
if [ -n "$SUPABASE_URL" ] && [ -n "$SUPABASE_SERVICE_ROLE_KEY" ] && [ -n "$JOB_ID" ]; then
  # -w writes the HTTP status code after the body, so we can tell success
  # from failure even though curl doesn't use -f here (we WANT the body
  # printed on error, and -f would suppress it).
  RESP=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "${SUPABASE_URL}/rest/v1/job_events" \
    -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
    -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"job_id\":\"${JOB_ID}\",\"step\":\"${STEP}\",\"status\":\"${STATUS}\",\"detail\":${DETAIL_JSON}}")
  HTTP_CODE=$(echo "$RESP" | grep -o "HTTP_STATUS:[0-9]*" | cut -d: -f2)
  BODY=$(echo "$RESP" | sed '/HTTP_STATUS:/d')
  if [ "$HTTP_CODE" -lt 200 ] || [ "$HTTP_CODE" -ge 300 ]; then
    echo "WARNING: Supabase job_events insert failed (HTTP $HTTP_CODE): $BODY" >&2
  else
    echo "Supabase job_events insert OK (HTTP $HTTP_CODE)" >&2
  fi

  # On success/failed of a step, also bump the job's overall `status`
  # field so the dashboard's job list reflects "running" vs "failed"
  # without needing to read the whole event timeline.
  if [ "$STATUS" = "failed" ]; then
    curl -s -o /dev/null -X PATCH "${SUPABASE_URL}/rest/v1/jobs?id=eq.${JOB_ID}" \
      -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Content-Type: application/json" \
      -d "{\"status\":\"failed\",\"error_message\":\"Step '${STEP}' failed\",\"finished_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" \
      || true
  elif [ "$STATUS" = "started" ] && [ "$STEP" = "download" ]; then
    curl -s -o /dev/null -X PATCH "${SUPABASE_URL}/rest/v1/jobs?id=eq.${JOB_ID}" \
      -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Content-Type: application/json" \
      -d "{\"status\":\"running\"}" \
      || true
  fi
else
  echo "WARNING: Supabase env vars missing, skipping dashboard report" >&2
fi

# --- 2. Report to Telegram (edit-in-place progress message) ------------
# We keep a running text file (progress.log) in the workspace so each
# call can APPEND a line and re-send the whole accumulated message -
# this is what gets edited into the single Telegram message.
if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ] && [ -n "$TELEGRAM_MESSAGE_ID" ]; then
  PROGRESS_FILE="${RUNNER_TEMP:-/tmp}/telegram_progress.log"

  if [ "$STATUS" = "started" ]; then
    echo "🔄 ${LABEL}..." >> "$PROGRESS_FILE"
  elif [ "$STATUS" = "success" ]; then
    # Replace the last "started" line for this step with a "done" line
    sed -i "\$s/.*/✅ ${LABEL}/" "$PROGRESS_FILE" 2>/dev/null || echo "✅ ${LABEL}" >> "$PROGRESS_FILE"
  elif [ "$STATUS" = "failed" ]; then
    sed -i "\$s/.*/❌ ${LABEL} - FAILED/" "$PROGRESS_FILE" 2>/dev/null || echo "❌ ${LABEL} - FAILED" >> "$PROGRESS_FILE"
  fi

  FULL_TEXT=$(cat "$PROGRESS_FILE" 2>/dev/null || echo "${ICON} ${LABEL}")

  curl -s -o /dev/null -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/editMessageText" \
    -d chat_id="${TELEGRAM_CHAT_ID}" \
    -d message_id="${TELEGRAM_MESSAGE_ID}" \
    --data-urlencode text="${FULL_TEXT}" \
    || echo "WARNING: Telegram edit failed (continuing anyway)" >&2
else
  echo "WARNING: Telegram env vars missing, skipping progress message update" >&2
fi

exit 0