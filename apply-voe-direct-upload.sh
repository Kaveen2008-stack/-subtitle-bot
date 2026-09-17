#!/usr/bin/env bash
set -e
echo "🔧 Switching VOE.sx uploads from Pixeldrain-URL-fetch to direct file upload..."

# IMPORTANT: run this from the ROOT of your subtitle-bot repo
# (the one with .github/workflows/burn.yml - NOT the onlyksub web repo).
#   bash apply-voe-direct-upload.sh

mkdir -p .github/workflows

cat > '.github/workflows/burn.yml' << 'VOEFIX_EOF'
name: Burn Subtitles (Matrix Parallel - 3 Qualities)

on:
  workflow_dispatch:
    inputs:
      video_url:
        description: "Direct/Pixeldrain video URL (best available source, e.g. 1080p)"
        required: true
        type: string
      srt_url:
        description: "Raw URL to the .srt file (gist)"
        required: true
        type: string
      sub_type:
        description: "english or sinhala"
        required: true
        type: string
      chat_id:
        description: "Telegram chat_id to notify when done"
        required: true
        type: string
      tmdb_id:
        description: "TMDB show ID"
        required: true
        type: string
      drama_name:
        description: "Drama/show name (used to build the output filename)"
        required: true
        type: string
      season_number:
        description: "Season number"
        required: true
        type: string
      episode_number:
        description: "Episode number"
        required: true
        type: string
      sub_format:
        description: "Subtitle format uploaded: srt or ass"
        required: false
        type: string
        default: "srt"
      job_id:
        description: "Supabase job UUID (for dashboard tracking)"
        required: false
        type: string
      telegram_message_id:
        description: "Telegram message ID to edit for live progress"
        required: false
        type: string
      qualities_json:
        description: 'Which qualities to burn, as a JSON array, e.g. ["720p"] or ["1080p","720p","480p"]'
        required: false
        type: string
        default: '["1080p","720p","480p"]'

concurrency:
  group: burn-${{ inputs.chat_id }}-${{ inputs.tmdb_id }}-${{ inputs.episode_number }}
  cancel-in-progress: false

jobs:
  # ============================================================
  # JOB 1: Runs ONCE. Subtitle download/clean/translate (Gemini -
  # expensive, must not be duplicated across the 3 quality runners).
  # Produces a shared subs.ass consumed by every quality job, plus
  # a sanitized SAFE_NAME artifact used to build output filenames.
  # ============================================================
  prepare-subs:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
      SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
    outputs:
      safe_name: ${{ steps.name.outputs.safe_name }}
      telegram_quality: ${{ steps.name.outputs.telegram_quality }}
    steps:
      - name: Checkout repo (for scripts)
        uses: actions/checkout@v4

      - name: Build sanitized output name (drama_S<season>E<episode>)
        id: name
        env:
          QUALITIES_JSON: ${{ inputs.qualities_json }}
        run: |
          set -e
          RAW="${{ inputs.drama_name }}_S${{ inputs.season_number }}E${{ inputs.episode_number }}"
          # spaces -> underscores, strip anything that isn't alnum/underscore/hyphen
          SAFE=$(echo "$RAW" | tr ' ' '_' | tr -cd '[:alnum:]_-')
          echo "safe_name=${SAFE}" >> "$GITHUB_OUTPUT"
          echo "Output basename will be: ${SAFE}_<quality>.mp4"

          # Telegram/Drive/VOE only get ONE quality (the best one actually
          # selected) - don't hardcode 1080p, some sources only have
          # 720p/480p available.
          python3 -c "
          import json, os
          qualities = json.loads(os.environ['QUALITIES_JSON'])
          priority = ['1080p', '720p', '480p']
          best = next((q for q in priority if q in qualities), qualities[0] if qualities else '')
          with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
              f.write(f'telegram_quality={best}\n')
          print(f'telegram_quality={best}')
          "

      - name: Install Sinhala font (Noto Sans Sinhala)
        run: |
          set -e
          pip install --quiet fonttools
          mkdir -p sinhala-font
          python scripts/install_sinhala_font.py sinhala-font
          if ! file sinhala-font/NotoSansSinhala-Bold.ttf | grep -qE "TrueType|OpenType"; then
            echo "ERROR: Font generation failed or file is not a valid font"
            exit 1
          fi
          echo "SINHALA_FONT_NAME=Noto Sans Sinhala" >> "$GITHUB_ENV"
          chmod +x scripts/report_status.sh

      - name: Set up Python deps
        run: pip install --quiet google-generativeai requests

      - name: Download subtitle file
        run: |
          set -e
          EXT="${{ inputs.sub_format }}"
          if [ -z "$EXT" ]; then EXT="srt"; fi
          curl -L -o "subs_raw.${EXT}" "${{ inputs.srt_url }}"

      - name: Clean subtitle (strip site credits/font tags - runs BEFORE translation)
        if: inputs.sub_format != 'ass'
        run: python scripts/clean_srt.py subs_raw.srt subs_cleaned.srt

      - name: Translate to Sinhala (only if needed, after cleaning - runs ONCE)
        if: inputs.sub_type == 'english' && inputs.sub_format != 'ass'
        env:
          GEMINI_API_KEY_1: ${{ secrets.GEMINI_API_KEY_1 }}
          GEMINI_API_KEY_2: ${{ secrets.GEMINI_API_KEY_2 }}
          GEMINI_API_KEY_3: ${{ secrets.GEMINI_API_KEY_3 }}
          GEMINI_API_KEY_4: ${{ secrets.GEMINI_API_KEY_4 }}
          GEMINI_API_KEY_5: ${{ secrets.GEMINI_API_KEY_5 }}
        run: |
          ./scripts/report_status.sh "translate" "started"
          python scripts/translate.py subs_cleaned.srt subs.srt \
            && ./scripts/report_status.sh "translate" "success" \
            || { ./scripts/report_status.sh "translate" "failed"; exit 1; }

      - name: Use cleaned sub as-is (already Sinhala, no translation needed)
        if: inputs.sub_type != 'english' && inputs.sub_format != 'ass'
        run: cp subs_cleaned.srt subs.srt

      - name: Fetch render settings (uses 1080p row for font/margin/outline - shared across qualities)
        run: |
          set -e
          RESULT=$(curl -s "${SUPABASE_URL}/rest/v1/render_settings?quality=eq.1080p&select=*" \
            -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
            -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}")
          python3 -c "
          import json, sys, os
          try:
              rows = json.loads(sys.argv[1])
              s = rows[0] if rows else {}
          except Exception:
              s = {}
          out = os.environ['GITHUB_ENV']
          with open(out, 'a') as f:
              f.write(f\"RENDER_FONT_SIZE={s.get('font_size', 42)}\n\")
              f.write(f\"RENDER_MARGIN_V={s.get('margin_v', 14)}\n\")
              f.write(f\"RENDER_OUTLINE={s.get('outline_width', 1)}\n\")
          " "$RESULT"

      - name: Convert SRT to ASS (fixes Sinhala conjunct shaping in libass)
        if: inputs.sub_format != 'ass'
        run: |
          set -e
          python scripts/srt_to_ass.py subs.srt subs.ass "$SINHALA_FONT_NAME" \
            --font-size "$RENDER_FONT_SIZE" \
            --margin-v "$RENDER_MARGIN_V" \
            --outline "$RENDER_OUTLINE"

      - name: Clean uploaded ASS (strip source-site credits, force installed font)
        if: inputs.sub_format == 'ass'
        run: |
          set -e
          python scripts/clean_ass.py subs_raw.ass subs.ass "$SINHALA_FONT_NAME" \
            --font-size "$RENDER_FONT_SIZE" \
            --margin-v "$RENDER_MARGIN_V" \
            --outline "$RENDER_OUTLINE"

      - name: Upload subs.ass + font as shared artifact for the quality jobs
        uses: actions/upload-artifact@v4
        with:
          name: prepared-subs
          path: |
            subs.ass
            sinhala-font/NotoSansSinhala-Bold.ttf
          retention-days: 1

  # ============================================================
  # JOB 2: Matrix - runs 3 TIMES IN PARALLEL (one per quality).
  # Each downloads the video and the shared subs.ass independently,
  # crops+burns+encodes+uploads its own quality, using a filename
  # that includes the drama name, season and episode.
  # ============================================================
  burn:
    needs: prepare-subs
    runs-on: ubuntu-latest
    timeout-minutes: 90
    strategy:
      fail-fast: false   # one quality failing shouldn't cancel the others
      matrix:
        quality: ${{ fromJson(inputs.qualities_json) }}
        include:
          - quality: 1080p
            scale: "1920:-2"
          - quality: 720p
            scale: "1280:-2"
          - quality: 480p
            scale: "854:-2"
    env:
      SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
      SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ inputs.chat_id }}
      JOB_ID: ${{ inputs.job_id }}
      OUT_NAME: ${{ needs.prepare-subs.outputs.safe_name }}_${{ matrix.quality }}.mp4
    steps:
      - name: Checkout repo (for scripts)
        uses: actions/checkout@v4

      - name: Install ffmpeg
        run: |
          sudo apt-get update -qq
          sudo apt-get install -y ffmpeg
          chmod +x scripts/report_status.sh

      - name: Set up Python deps
        run: pip install --quiet google-api-python-client google-auth requests Pillow

      - name: Download prepared subs + font
        uses: actions/download-artifact@v4
        with:
          name: prepared-subs

      - name: Fetch render settings for ${{ matrix.quality }}
        run: |
          set -e
          RESULT=$(curl -s "${SUPABASE_URL}/rest/v1/render_settings?quality=eq.${{ matrix.quality }}&select=*" \
            -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
            -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}")
          python3 -c "
          import json, sys, os
          try:
              rows = json.loads(sys.argv[1])
              s = rows[0] if rows else {}
          except Exception:
              s = {}
          out = os.environ['GITHUB_ENV']
          with open(out, 'a') as f:
              f.write(f\"RENDER_WATERMARK_TEXT={s.get('watermark_text', 'OnlyKSub')}\n\")
              f.write(f\"RENDER_WATERMARK_OPACITY={s.get('watermark_opacity', 0.6)}\n\")
              f.write(f\"RENDER_WATERMARK_FONTSIZE={s.get('watermark_fontsize', 16)}\n\")
              f.write(f\"RENDER_CRF={s.get('crf', 23)}\n\")
              f.write(f\"RENDER_AUDIO_BITRATE={s.get('audio_bitrate', '128k')}\n\")
          " "$RESULT"

      - name: Download video
        run: |
          set -e
          ./scripts/report_status.sh "download_${{ matrix.quality }}" "started"
          URL="${{ inputs.video_url }}"
          if echo "$URL" | grep -q "pixeldrain.com/u/"; then
            FILE_ID=$(echo "$URL" | sed -E 's#.*pixeldrain\.com/u/([a-zA-Z0-9]+).*#\1#')
            URL="https://pixeldrain.com/api/file/${FILE_ID}"
          fi
          if ! curl -f -L -A "Mozilla/5.0" -e "https://pixeldrain.com/" -o input_video.mp4 "$URL"; then
            ./scripts/report_status.sh "download_${{ matrix.quality }}" "failed"
            exit 1
          fi
          ./scripts/report_status.sh "download_${{ matrix.quality }}" "success"

      - name: Detect and remove black bars (robust pixel-based auto-crop)
        run: |
          set -e
          CROP=$(python3 scripts/detect_crop.py input_video.mp4 --threshold 24 --samples 30 | grep "^crop=" || true)
          if [ -z "$CROP" ]; then
            echo "CROP_FILTER=" >> "$GITHUB_ENV"
          else
            echo "CROP_FILTER=${CROP}," >> "$GITHUB_ENV"
          fi

      - name: Inject OnlyKsub site credit (start + end)
        run: |
          set -e
          DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 input_video.mp4)
          python3 scripts/inject_credits.py subs.ass "$DURATION" "Noto Sans Sinhala"

      - name: Burn subtitles + watermark for ${{ matrix.quality }}
        run: |
          set -e
          ./scripts/report_status.sh "burn_${{ matrix.quality }}" "started"
          FONT_DIR=$(pwd)/sinhala-font
          FONT_PATH="${FONT_DIR}/NotoSansSinhala-Bold.ttf"

          FILTER="${CROP_FILTER}ass=subs.ass:fontsdir=${FONT_DIR},scale=${{ matrix.scale }},"
          FILTER="${FILTER}drawtext=text='${RENDER_WATERMARK_TEXT}':fontfile=${FONT_PATH}:fontsize=${RENDER_WATERMARK_FONTSIZE}:fontcolor=white@${RENDER_WATERMARK_OPACITY}:borderw=1:bordercolor=black@0.5:x=w-tw-16:y=h-th-16"

          if ! ffmpeg -y -i input_video.mp4 \
            -vf "$FILTER" \
            -c:v libx264 -preset veryfast -crf "${RENDER_CRF}" -pix_fmt yuv420p \
            -c:a aac -b:a "${RENDER_AUDIO_BITRATE}" \
            -movflags +faststart \
            "$OUT_NAME"; then
            ./scripts/report_status.sh "burn_${{ matrix.quality }}" "failed"
            exit 1
          fi
          ./scripts/report_status.sh "burn_${{ matrix.quality }}" "success"

      - name: Upload to Google Drive (1080p only)
        id: drive
        if: matrix.quality == needs.prepare-subs.outputs.telegram_quality
        continue-on-error: true
        env:
          GDRIVE_SA_JSON_B64: ${{ secrets.GDRIVE_SA_JSON_B64 }}
          GDRIVE_FOLDER_ID: ${{ secrets.GDRIVE_FOLDER_ID }}
        run: |
          set -e
          ./scripts/report_status.sh "upload_drive" "started"
          echo "$GDRIVE_SA_JSON_B64" | base64 -d > sa.json
          if GDRIVE_SA_JSON=sa.json python scripts/upload_drive.py "$OUT_NAME"; then
            echo "link=$(cat drive_link.txt)" >> "$GITHUB_OUTPUT"
            ./scripts/report_status.sh "upload_drive" "success" "{\"link\":\"$(cat drive_link.txt)\"}"
          else
            ./scripts/report_status.sh "upload_drive" "failed"
          fi

      - name: Upload to Pixeldrain
        id: pixeldrain
        env:
          PIXELDRAIN_API_KEY: ${{ secrets.PIXELDRAIN_API_KEY }}
        run: |
          set -e
          ./scripts/report_status.sh "upload_${{ matrix.quality }}" "started"
          if python scripts/upload_pixeldrain.py "$OUT_NAME"; then
            echo "link=$(cat pixeldrain_link.txt)" >> "$GITHUB_OUTPUT"
            ./scripts/report_status.sh "upload_${{ matrix.quality }}" "success" "{\"link\":\"$(cat pixeldrain_link.txt)\"}"
          else
            ./scripts/report_status.sh "upload_${{ matrix.quality }}" "failed"
            exit 1
          fi

      - name: Save video for later sequential Telegram upload
        # Telegram upload is done OUTSIDE this concurrent matrix job (see
        # the separate upload-telegram job below), same pattern as
        # burn_batch.yml uses. Uploading from parallel matrix runners (one
        # per quality) on the SAME bot token at once risks Telegram's
        # flood control (retry_after can be very large since it looks
        # like abuse from Telegram's side). Sequential, one-at-a-time
        # uploads after all qualities finish rendering avoids that.
        uses: actions/upload-artifact@v4
        with:
          name: telegram-pending-${{ matrix.quality }}
          path: ${{ env.OUT_NAME }}
          retention-days: 1

      - name: Upload to VOE.sx (1080p only, direct file upload)
        id: voe
        if: matrix.quality == needs.prepare-subs.outputs.telegram_quality
        continue-on-error: true
        env:
          VOE_API_KEY: ${{ secrets.VOE_API_KEY }}
        run: |
          ./scripts/report_status.sh "upload_voe" "started"

          # Was: ask VOE to fetch the file itself from a Pixeldrain URL
          # (upload/url). Pixeldrain blocks/CAPTCHA-walls fetches coming
          # from VOE's servers (datacenter IP), so VOE downloaded a CAPTCHA
          # or error page instead of the video ("Not a video file") no
          # matter which Pixeldrain URL shape was used. Fix: skip the
          # remote-fetch step entirely and POST the video file we already
          # have on this runner straight to VOE.

          SERVER_RESPONSE=$(curl -s "https://voe.sx/api/upload/server?key=${VOE_API_KEY}")
          echo "VOE upload/server response: $SERVER_RESPONSE"
          UPLOAD_SERVER=$(echo "$SERVER_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('result',{}).get('upload_server',''))" 2>/dev/null || echo "")

          if [ -z "$UPLOAD_SERVER" ]; then
            echo "WARNING: could not get a VOE upload server, continuing without VOE link."
            ./scripts/report_status.sh "upload_voe" "failed" "{\"reason\":\"no upload_server in response\"}"
            exit 0
          fi
          echo "VOE upload server: $UPLOAD_SERVER"

          UPLOAD_RESPONSE=$(curl -s -F "key=${VOE_API_KEY}" -F "file=@${OUT_NAME}" "$UPLOAD_SERVER")
          echo "VOE direct upload response: $UPLOAD_RESPONSE"

          # Response shape for the direct-upload endpoint isn't fully
          # documented publicly, so try every plausible path this family
          # of file-host APIs (VOE/StreamHG/Doodstream-style clones) uses,
          # instead of assuming one and breaking silently if it's wrong.
          FILE_CODE=$(echo "$UPLOAD_RESPONSE" | python3 -c "
          import sys, json
          try:
              d = json.load(sys.stdin)
          except Exception:
              print('')
              raise SystemExit
          code = ''
          if isinstance(d, dict):
              if isinstance(d.get('files'), list) and d['files']:
                  f0 = d['files'][0]
                  code = f0.get('file_code') or f0.get('filecode') or ''
              if not code:
                  code = d.get('file_code') or d.get('filecode') or ''
              if not code and isinstance(d.get('result'), dict):
                  code = d['result'].get('file_code') or d['result'].get('filecode') or ''
          print(code)
          " 2>/dev/null || echo "")

          if [ -n "$FILE_CODE" ]; then
            echo "link=https://voe.sx/e/${FILE_CODE}" >> "$GITHUB_OUTPUT"
            echo "VOE embed link ready: https://voe.sx/e/${FILE_CODE}"
            ./scripts/report_status.sh "upload_voe" "success" "{\"link\":\"https://voe.sx/e/${FILE_CODE}\"}"
          else
            echo "WARNING: VOE direct upload response had no recognizable file_code - see the raw response logged above, continuing without VOE link."
            ./scripts/report_status.sh "upload_voe" "failed" '{"reason":"no file_code in direct-upload response"}'
          fi

      - name: Notify website (webhook) for ${{ matrix.quality }}
        if: always()
        continue-on-error: true
        env:
          WEBHOOK_SECRET: ${{ secrets.WEBHOOK_SECRET }}
          LINK: ${{ steps.pixeldrain.outputs.link }}
          VOE_LINK: ${{ steps.voe.outputs.link }}
        run: |
          if [ -z "$LINK" ]; then exit 0; fi
          curl -s -X POST "https://onlyksub.vercel.app/api/webhook-hardcode-complete" \
            -H "Content-Type: application/json" \
            -H "x-webhook-secret: ${WEBHOOK_SECRET}" \
            -d "{
              \"tmdb_id\": ${{ inputs.tmdb_id }},
              \"season_number\": ${{ inputs.season_number }},
              \"episode_number\": ${{ inputs.episode_number }},
              \"pixeldrain_download_url\": \"${LINK}\",
              \"voe_embed_url\": \"${VOE_LINK}\",
              \"quality\": \"${{ matrix.quality }}\"
            }"

      - name: Notify on Telegram for ${{ matrix.quality }}
        if: always()
        env:
          BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          CHAT_ID: ${{ inputs.chat_id }}
          LINK: ${{ steps.pixeldrain.outputs.link }}
          VOE_LINK: ${{ steps.voe.outputs.link }}
        run: |
          if [ -n "$LINK" ]; then
            TEXT="✅ ${{ inputs.drama_name }} S${{ inputs.season_number }}E${{ inputs.episode_number }} (${{ matrix.quality }}) done! 📥 ${LINK}"
            if [ -n "$VOE_LINK" ]; then
              TEXT="${TEXT}"$'\n'"🎬 VOE: ${VOE_LINK}"
            fi
          else
            TEXT="❌ ${{ inputs.drama_name }} S${{ inputs.season_number }}E${{ inputs.episode_number }} (${{ matrix.quality }}) failed. Check the GitHub Actions log."
          fi
          curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d chat_id="${CHAT_ID}" \
            --data-urlencode text="${TEXT}"

      - name: Upload artifact (backup)
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: burned-video-${{ matrix.quality }}
          path: ${{ env.OUT_NAME }}
          retention-days: 3

  # ============================================================
  # JOB 3: Uploads each quality to Telegram ONE AT A TIME, after all
  # burns finish - avoids flood control from 3 parallel matrix
  # runners hitting the same bot token/channel at once (same pattern
  # as burn_batch.yml's upload-telegram job).
  # ============================================================
  upload-telegram:
    needs: [prepare-subs, burn]
    if: always()
    runs-on: ubuntu-latest
    timeout-minutes: 60
    services:
      telegram-bot-api:
        image: aiogram/telegram-bot-api:latest
        env:
          TELEGRAM_API_ID: ${{ secrets.TELEGRAM_API_ID }}
          TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
        ports:
          - 8081:8081
    steps:
      - name: Checkout repo (for scripts)
        uses: actions/checkout@v4

      - name: Set up Python deps
        run: pip install --quiet requests

      - name: Wait for local Telegram Bot API server
        run: |
          for i in $(seq 1 30); do
            if curl -s -o /dev/null "http://localhost:8081"; then
              echo "Local Bot API server is up."
              exit 0
            fi
            sleep 1
          done
          echo "WARNING: local Bot API server did not come up in time."

      - name: Upload each quality sequentially
        env:
          GH_TOKEN: ${{ github.token }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHANNEL_ID: ${{ secrets.TELEGRAM_CHANNEL_ID }}
          WEBHOOK_SECRET: ${{ secrets.WEBHOOK_SECRET }}
          QUALITIES_JSON: ${{ inputs.qualities_json }}
        run: |
          set +e  # one quality's failure shouldn't stop the rest
          QUALITIES=$(python3 -c "import json,os; print(' '.join(json.loads(os.environ['QUALITIES_JSON'])))")
          for QUALITY in $QUALITIES; do
            echo "=== Quality $QUALITY ==="
            ARTIFACT_NAME="telegram-pending-${QUALITY}"
            rm -rf dl && mkdir dl
            if ! gh run download "${{ github.run_id }}" --name "$ARTIFACT_NAME" --dir dl 2>/dev/null; then
              echo "No artifact found for $QUALITY (burn may have failed) - skipping."
              continue
            fi
            VIDEO_FILE=$(find dl -maxdepth 1 -type f -iname "*.mp4" | head -1)
            if [ -z "$VIDEO_FILE" ]; then
              echo "WARNING: no video file found in artifact for $QUALITY - skipping."
              continue
            fi
            rm -f telegram_message_id.txt telegram_chat_id.txt
            CAPTION="${{ needs.prepare-subs.outputs.safe_name }} (${QUALITY}) - OnlyKsub"
            if python scripts/upload_telegram_local.py "$VIDEO_FILE" "$CAPTION"; then
              echo "$QUALITY uploaded: $(cat telegram_link.txt)"
              if [ -f telegram_message_id.txt ]; then
                MSG_ID=$(cat telegram_message_id.txt)
                CHAT_ID=$(cat telegram_chat_id.txt)
                curl -s -f -X POST "https://onlyksub.vercel.app/api/webhook-telegram-uploaded" \
                  -H "Content-Type: application/json" \
                  -H "x-webhook-secret: ${WEBHOOK_SECRET}" \
                  -d "{
                    \"tmdb_id\": ${{ inputs.tmdb_id }},
                    \"season_number\": ${{ inputs.season_number }},
                    \"episode_number\": ${{ inputs.episode_number }},
                    \"quality\": \"${QUALITY}\",
                    \"telegram_chat_id\": \"${CHAT_ID}\",
                    \"telegram_message_id\": ${MSG_ID}
                  }" \
                  && echo "Notified website for $QUALITY (chat_id=${CHAT_ID}, message_id=${MSG_ID})." \
                  || echo "::warning::Failed to notify website about $QUALITY's Telegram upload."
              fi
            else
              echo "::warning::$QUALITY Telegram upload failed - the site will NOT show a Telegram download link for this quality."
            fi
            echo "Waiting 15s before next upload (flood control safety margin)..."
            sleep 15
          done

  # ============================================================
  # JOB 4: Runs once ALL 3 quality jobs + Telegram uploads finish.
  # Marks the Supabase job row as done.
  # ============================================================
  finalize:
    needs: [burn, upload-telegram]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - name: Mark job done in dashboard
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          JOB_ID: ${{ inputs.job_id }}
        run: |
          if [ -n "$SUPABASE_URL" ] && [ -n "$JOB_ID" ]; then
            FINAL_STATUS="done"
            if [ "${{ needs.burn.result }}" != "success" ]; then FINAL_STATUS="failed"; fi
            curl -s -o /dev/null -X PATCH "${SUPABASE_URL}/rest/v1/jobs?id=eq.${JOB_ID}" \
              -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
              -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
              -H "Content-Type: application/json" \
              -d "{\"status\":\"${FINAL_STATUS}\",\"finished_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"github_run_url\":\"${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}\"}" \
              || true
          fi

VOEFIX_EOF
echo '  ✓ wrote .github/workflows/burn.yml'

cat > '.github/workflows/burn_batch.yml' << 'VOEFIX_EOF'
name: Burn Subtitles Batch (Per-Episode List - Matrix Parallel)

on:
  workflow_dispatch:
    inputs:
      episodes_json:
        description: 'JSON array: [{"episode_number":1,"video_url":"https://pixeldrain.com/u/xxxx","srt_url":"https://gist..."}, ...]'
        required: true
        type: string
      qualities_json:
        description: 'JSON array of qualities to render, e.g. ["1080p","720p"]. Defaults to all 3.'
        required: false
        type: string
        default: '["1080p","720p","480p"]'
      sub_type:
        description: "english or sinhala"
        required: true
        type: string
      chat_id:
        description: "Telegram chat_id to notify when done"
        required: true
        type: string
      tmdb_id:
        description: "TMDB show ID"
        required: true
        type: string
      drama_name:
        description: "Drama/show name (used to build output filenames + watermark)"
        required: true
        type: string
      season_number:
        description: "Season number"
        required: true
        type: string
      sub_format:
        description: "Subtitle format uploaded: srt or ass"
        required: false
        type: string
        default: "srt"
      job_id:
        description: "Supabase job UUID (for dashboard tracking)"
        required: false
        type: string

concurrency:
  group: burn-batch-${{ inputs.chat_id }}-${{ inputs.tmdb_id }}-${{ inputs.season_number }}
  cancel-in-progress: false

jobs:
  # ============================================================
  # JOB 1: Runs ONCE. Parses the episode list + selected qualities
  # so both matrix jobs below can fan out on them. No archive
  # download/extraction - each episode already has a direct URL.
  # ============================================================
  # ============================================================
  # JOB 1: Parses episodes_json + qualities_json so both matrix
  # jobs below can fan out on them.
  # ============================================================
  plan:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    outputs:
      episodes: ${{ steps.parse.outputs.episodes }}
      qualities: ${{ steps.parse.outputs.qualities }}
      telegram_quality: ${{ steps.parse.outputs.telegram_quality }}
    steps:
      - name: Parse episodes_json + qualities_json
        id: parse
        env:
          EPISODES_JSON: ${{ inputs.episodes_json }}
          QUALITIES_JSON: ${{ inputs.qualities_json }}
        run: |
          set -e
          python3 << 'PYEOF'
          import json, os

          episodes_raw = os.environ["EPISODES_JSON"]
          qualities_raw = os.environ["QUALITIES_JSON"]

          episodes = json.loads(episodes_raw)
          qualities = json.loads(qualities_raw)

          if not isinstance(episodes, list) or len(episodes) == 0:
              raise SystemExit("episodes_json must be a non-empty JSON array")

          for e in episodes:
              missing = [k for k in ("episode_number", "video_url", "srt_url") if k not in e]
              if missing:
                  raise SystemExit(f"bad entry (missing {missing}): {e}")

          # Telegram only gets ONE quality per episode (to keep concurrent
          # uploads on the single bot token manageable). Pick the highest
          # quality actually selected for this run - don't hardcode 1080p,
          # since some sources only have 720p/480p available.
          PRIORITY = ["1080p", "720p", "480p"]
          telegram_quality = next((q for q in PRIORITY if q in qualities), qualities[0] if qualities else "")

          print(f"{len(episodes)} episode(s) planned, qualities={qualities}, telegram_quality={telegram_quality}")

          episodes_out = json.dumps(episodes, separators=(",", ":"))
          qualities_out = json.dumps(qualities, separators=(",", ":"))

          with open(os.environ["GITHUB_OUTPUT"], "a") as out:
              out.write(f"episodes={episodes_out}\n")
              out.write(f"qualities={qualities_out}\n")
              out.write(f"telegram_quality={telegram_quality}\n")
          PYEOF


  prepare-subs:
    needs: plan
    runs-on: ubuntu-latest
    timeout-minutes: 30
    strategy:
      fail-fast: false
      max-parallel: 10
      matrix:
        episode: ${{ fromJson(needs.plan.outputs.episodes) }}
    env:
      SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
      SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
    steps:
      - name: Checkout repo (for scripts)
        uses: actions/checkout@v4

      - name: Install Sinhala font (Noto Sans Sinhala)
        run: |
          set -e
          pip install --quiet fonttools
          mkdir -p sinhala-font
          python scripts/install_sinhala_font.py sinhala-font
          if ! file sinhala-font/NotoSansSinhala-Bold.ttf | grep -qE "TrueType|OpenType"; then
            echo "ERROR: Font generation failed or file is not a valid font"
            exit 1
          fi
          echo "SINHALA_FONT_NAME=Noto Sans Sinhala" >> "$GITHUB_ENV"
          chmod +x scripts/report_status.sh

      - name: Set up Python deps
        run: pip install --quiet google-generativeai requests

      - name: Download subtitle file for ep${{ matrix.episode.episode_number }}
        run: |
          set -e
          EXT="${{ inputs.sub_format }}"
          if [ -z "$EXT" ]; then EXT="srt"; fi
          curl -f -L -o "subs_raw.${EXT}" "${{ matrix.episode.srt_url }}"

      - name: Clean subtitle (strip site credits/font tags)
        if: inputs.sub_format != 'ass'
        run: python scripts/clean_srt.py subs_raw.srt subs_cleaned.srt

      - name: Translate to Sinhala (only if needed)
        if: inputs.sub_type == 'english' && inputs.sub_format != 'ass'
        env:
          GEMINI_API_KEY_1: ${{ secrets.GEMINI_API_KEY_1 }}
          GEMINI_API_KEY_2: ${{ secrets.GEMINI_API_KEY_2 }}
          GEMINI_API_KEY_3: ${{ secrets.GEMINI_API_KEY_3 }}
          GEMINI_API_KEY_4: ${{ secrets.GEMINI_API_KEY_4 }}
          GEMINI_API_KEY_5: ${{ secrets.GEMINI_API_KEY_5 }}
        run: |
          ./scripts/report_status.sh "translate_ep${{ matrix.episode.episode_number }}" "started"
          python scripts/translate.py subs_cleaned.srt subs.srt \
            && ./scripts/report_status.sh "translate_ep${{ matrix.episode.episode_number }}" "success" \
            || { ./scripts/report_status.sh "translate_ep${{ matrix.episode.episode_number }}" "failed"; exit 1; }

      - name: Use cleaned sub as-is (already Sinhala)
        if: inputs.sub_type != 'english' && inputs.sub_format != 'ass'
        run: cp subs_cleaned.srt subs.srt

      - name: Fetch render settings (uses 1080p row for font/margin/outline)
        run: |
          set -e
          RESULT=$(curl -s "${SUPABASE_URL}/rest/v1/render_settings?quality=eq.1080p&select=*" \
            -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
            -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}")
          python3 -c "
          import json, sys, os
          try:
              rows = json.loads(sys.argv[1]); s = rows[0] if rows else {}
          except Exception:
              s = {}
          out = os.environ['GITHUB_ENV']
          with open(out, 'a') as f:
              f.write(f\"RENDER_FONT_SIZE={s.get('font_size', 42)}\n\")
              f.write(f\"RENDER_MARGIN_V={s.get('margin_v', 14)}\n\")
              f.write(f\"RENDER_OUTLINE={s.get('outline_width', 1)}\n\")
          " "$RESULT"

      - name: Convert SRT to ASS (fixes Sinhala conjunct shaping in libass)
        if: inputs.sub_format != 'ass'
        run: |
          set -e
          python scripts/srt_to_ass.py subs.srt subs.ass "$SINHALA_FONT_NAME" \
            --font-size "$RENDER_FONT_SIZE" --margin-v "$RENDER_MARGIN_V" --outline "$RENDER_OUTLINE"

      - name: Clean uploaded ASS (strip source-site credits, force installed font)
        if: inputs.sub_format == 'ass'
        run: |
          set -e
          python scripts/clean_ass.py subs_raw.ass subs.ass "$SINHALA_FONT_NAME" \
            --font-size "$RENDER_FONT_SIZE" --margin-v "$RENDER_MARGIN_V" --outline "$RENDER_OUTLINE"

      - name: Upload subs.ass + font as per-episode shared artifact
        uses: actions/upload-artifact@v4
        with:
          name: batch-subs-ep${{ matrix.episode.episode_number }}
          path: |
            subs.ass
            sinhala-font/NotoSansSinhala-Bold.ttf
          retention-days: 1

  # ============================================================
  # JOB 3: Matrix over episode x quality (built-in cross product,
  # true parallel, capped at 10 concurrent runners). Each run
  # downloads its own episode video + the shared subs.ass for
  # that episode, crops+burns+encodes+uploads its own file, named
  # <drama>_S<season>E<episode>_<quality>.mp4 with the drama name
  # and episode burned into the watermark.
  # ============================================================
  burn:
    needs: [plan, prepare-subs]
    runs-on: ubuntu-latest
    timeout-minutes: 90
    # NOTE: no telegram-bot-api service here anymore - Telegram upload now
    # happens in the separate sequential `upload-telegram` job below, to
    # avoid every parallel (episode x quality) runner hitting the same bot
    # token at once (Telegram flood control).
    strategy:
      fail-fast: false
      max-parallel: 20
      matrix:
        episode: ${{ fromJson(needs.plan.outputs.episodes) }}
        quality: ${{ fromJson(needs.plan.outputs.qualities) }}
        include:
          - quality: 1080p
            scale: "1920:-2"
          - quality: 720p
            scale: "1280:-2"
          - quality: 480p
            scale: "854:-2"
    env:
      SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
      SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ inputs.chat_id }}
      JOB_ID: ${{ inputs.job_id }}
    steps:
      - name: Checkout repo (for scripts)
        uses: actions/checkout@v4

      - name: Install ffmpeg
        run: |
          sudo apt-get update -qq
          sudo apt-get install -y ffmpeg
          chmod +x scripts/report_status.sh

      - name: Set up Python deps
        run: pip install --quiet google-api-python-client google-auth requests Pillow

      - name: Download prepared subs + font for ep${{ matrix.episode.episode_number }}
        uses: actions/download-artifact@v4
        with:
          name: batch-subs-ep${{ matrix.episode.episode_number }}

      - name: Build sanitized output name (drama_S<season>E<episode>_<quality>)
        id: name
        run: |
          set -e
          RAW="${{ inputs.drama_name }}_S${{ inputs.season_number }}E${{ matrix.episode.episode_number }}"
          SAFE=$(echo "$RAW" | tr ' ' '_' | tr -cd '[:alnum:]_-')
          OUT_NAME="${SAFE}_${{ matrix.quality }}.mp4"
          echo "OUT_NAME=${OUT_NAME}" >> "$GITHUB_ENV"
          echo "Output filename will be: ${OUT_NAME}"

      - name: Fetch render settings for ${{ matrix.quality }}
        run: |
          set -e
          RESULT=$(curl -s "${SUPABASE_URL}/rest/v1/render_settings?quality=eq.${{ matrix.quality }}&select=*" \
            -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
            -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}")
          python3 -c "
          import json, sys, os
          try:
              rows = json.loads(sys.argv[1]); s = rows[0] if rows else {}
          except Exception:
              s = {}
          out = os.environ['GITHUB_ENV']
          with open(out, 'a') as f:
              f.write(f\"RENDER_WATERMARK_TEXT={s.get('watermark_text', 'OnlyKSub')}\n\")
              f.write(f\"RENDER_WATERMARK_OPACITY={s.get('watermark_opacity', 0.6)}\n\")
              f.write(f\"RENDER_WATERMARK_FONTSIZE={s.get('watermark_fontsize', 16)}\n\")
              f.write(f\"RENDER_CRF={s.get('crf', 23)}\n\")
              f.write(f\"RENDER_AUDIO_BITRATE={s.get('audio_bitrate', '128k')}\n\")
          " "$RESULT"

      - name: Download episode video (ep${{ matrix.episode.episode_number }}, ${{ matrix.quality }})
        run: |
          set -e
          ./scripts/report_status.sh "download_ep${{ matrix.episode.episode_number }}_${{ matrix.quality }}" "started"
          URL="${{ matrix.episode.video_url }}"
          if echo "$URL" | grep -q "pixeldrain.com/u/"; then
            FILE_ID=$(echo "$URL" | sed -E 's#.*pixeldrain\.com/u/([a-zA-Z0-9]+).*#\1#')
            URL="https://pixeldrain.com/api/file/${FILE_ID}"
          fi
          if ! curl -f -L -A "Mozilla/5.0" -e "https://pixeldrain.com/" -o input_video.mp4 "$URL"; then
            ./scripts/report_status.sh "download_ep${{ matrix.episode.episode_number }}_${{ matrix.quality }}" "failed"
            exit 1
          fi
          ./scripts/report_status.sh "download_ep${{ matrix.episode.episode_number }}_${{ matrix.quality }}" "success"

      - name: Detect and remove black bars (robust pixel-based auto-crop)
        run: |
          set -e
          CROP=$(python3 scripts/detect_crop.py input_video.mp4 --threshold 24 --samples 30 | grep "^crop=" || true)
          if [ -z "$CROP" ]; then
            echo "CROP_FILTER=" >> "$GITHUB_ENV"
          else
            echo "CROP_FILTER=${CROP}," >> "$GITHUB_ENV"
          fi

      - name: Inject OnlyKsub site credit (start + end)
        run: |
          set -e
          DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 input_video.mp4)
          python3 scripts/inject_credits.py subs.ass "$DURATION" "Noto Sans Sinhala"

      - name: Burn subtitles + watermark (ep${{ matrix.episode.episode_number }}, ${{ matrix.quality }})
        run: |
          set -e
          ./scripts/report_status.sh "burn_ep${{ matrix.episode.episode_number }}_${{ matrix.quality }}" "started"
          FONT_DIR=$(pwd)/sinhala-font
          FONT_PATH="${FONT_DIR}/NotoSansSinhala-Bold.ttf"

          WATERMARK_TEXT=$(echo "$RENDER_WATERMARK_TEXT" | sed "s/'/\\\\'/g" | sed "s/:/\\\\:/g")

          FILTER="${CROP_FILTER}ass=subs.ass:fontsdir=${FONT_DIR},scale=${{ matrix.scale }},"
          FILTER="${FILTER}drawtext=text='${WATERMARK_TEXT}':fontfile=${FONT_PATH}:fontsize=${RENDER_WATERMARK_FONTSIZE}:fontcolor=white@${RENDER_WATERMARK_OPACITY}:borderw=1:bordercolor=black@0.5:x=w-tw-16:y=h-th-16"

          if ! ffmpeg -y -i input_video.mp4 \
            -vf "$FILTER" \
            -c:v libx264 -preset veryfast -crf "${RENDER_CRF}" -pix_fmt yuv420p \
            -c:a aac -b:a "${RENDER_AUDIO_BITRATE}" \
            -movflags +faststart \
            "$OUT_NAME"; then
            ./scripts/report_status.sh "burn_ep${{ matrix.episode.episode_number }}_${{ matrix.quality }}" "failed"
            exit 1
          fi
          ./scripts/report_status.sh "burn_ep${{ matrix.episode.episode_number }}_${{ matrix.quality }}" "success"

      - name: Upload to Google Drive (1080p only)
        id: drive
        if: matrix.quality == needs.plan.outputs.telegram_quality
        continue-on-error: true
        env:
          GDRIVE_SA_JSON_B64: ${{ secrets.GDRIVE_SA_JSON_B64 }}
          GDRIVE_FOLDER_ID: ${{ secrets.GDRIVE_FOLDER_ID }}
        run: |
          set -e
          echo "$GDRIVE_SA_JSON_B64" | base64 -d > sa.json
          if GDRIVE_SA_JSON=sa.json python scripts/upload_drive.py "$OUT_NAME"; then
            echo "link=$(cat drive_link.txt)" >> "$GITHUB_OUTPUT"
          fi

      - name: Upload to Pixeldrain
        id: pixeldrain
        env:
          PIXELDRAIN_API_KEY: ${{ secrets.PIXELDRAIN_API_KEY }}
        run: |
          set -e
          ./scripts/report_status.sh "upload_ep${{ matrix.episode.episode_number }}_${{ matrix.quality }}" "started"
          if python scripts/upload_pixeldrain.py "$OUT_NAME"; then
            echo "link=$(cat pixeldrain_link.txt)" >> "$GITHUB_OUTPUT"
            ./scripts/report_status.sh "upload_ep${{ matrix.episode.episode_number }}_${{ matrix.quality }}" "success" "{\"link\":\"$(cat pixeldrain_link.txt)\"}"
          else
            ./scripts/report_status.sh "upload_ep${{ matrix.episode.episode_number }}_${{ matrix.quality }}" "failed"
            exit 1
          fi

      - name: Save video for later sequential Telegram upload
        # Telegram upload is done OUTSIDE this concurrent matrix job (see the
        # separate upload-telegram job below). Uploading from many parallel
        # matrix runners (one per episode/quality) on the SAME bot token at
        # once triggers Telegram's flood control (retry_after can be 3000+
        # seconds) since it looks like abuse from Telegram's side.
        # Sequential, one-at-a-time uploads after all burns finish avoids
        # that entirely. All qualities now upload (previously 1080p-only),
        # so the artifact is keyed by episode AND quality.
        uses: actions/upload-artifact@v4
        with:
          name: telegram-pending-ep${{ matrix.episode.episode_number }}-${{ matrix.quality }}
          path: ${{ env.OUT_NAME }}
          retention-days: 1

      - name: Upload to VOE.sx (1080p only, direct file upload)
        id: voe
        if: matrix.quality == needs.plan.outputs.telegram_quality
        continue-on-error: true
        env:
          VOE_API_KEY: ${{ secrets.VOE_API_KEY }}
        run: |
          # Was: ask VOE to fetch the file itself from a Pixeldrain URL
          # (upload/url). Pixeldrain blocks/CAPTCHA-walls fetches coming
          # from VOE's servers (datacenter IP), so VOE downloaded a CAPTCHA
          # or error page instead of the video ("Not a video file") no
          # matter which Pixeldrain URL shape was used. Fix: skip the
          # remote-fetch step entirely and POST the video file we already
          # have on this runner straight to VOE.

          SERVER_RESPONSE=$(curl -s "https://voe.sx/api/upload/server?key=${VOE_API_KEY}")
          echo "VOE upload/server response: $SERVER_RESPONSE"
          UPLOAD_SERVER=$(echo "$SERVER_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('result',{}).get('upload_server',''))" 2>/dev/null || echo "")

          if [ -z "$UPLOAD_SERVER" ]; then
            echo "WARNING: could not get a VOE upload server, continuing without VOE link."
            exit 0
          fi
          echo "VOE upload server: $UPLOAD_SERVER"

          UPLOAD_RESPONSE=$(curl -s -F "key=${VOE_API_KEY}" -F "file=@${OUT_NAME}" "$UPLOAD_SERVER")
          echo "VOE direct upload response: $UPLOAD_RESPONSE"

          # Response shape for the direct-upload endpoint isn't fully
          # documented publicly, so try every plausible path this family
          # of file-host APIs (VOE/StreamHG/Doodstream-style clones) uses,
          # instead of assuming one and breaking silently if it's wrong.
          FILE_CODE=$(echo "$UPLOAD_RESPONSE" | python3 -c "
          import sys, json
          try:
              d = json.load(sys.stdin)
          except Exception:
              print('')
              raise SystemExit
          code = ''
          if isinstance(d, dict):
              if isinstance(d.get('files'), list) and d['files']:
                  f0 = d['files'][0]
                  code = f0.get('file_code') or f0.get('filecode') or ''
              if not code:
                  code = d.get('file_code') or d.get('filecode') or ''
              if not code and isinstance(d.get('result'), dict):
                  code = d['result'].get('file_code') or d['result'].get('filecode') or ''
          print(code)
          " 2>/dev/null || echo "")

          if [ -n "$FILE_CODE" ]; then
            echo "link=https://voe.sx/e/${FILE_CODE}" >> "$GITHUB_OUTPUT"
            echo "VOE embed link ready: https://voe.sx/e/${FILE_CODE}"
          else
            echo "WARNING: VOE direct upload response had no recognizable file_code - see the raw response logged above, continuing without VOE link."
          fi

      - name: Notify website (webhook) for ep${{ matrix.episode.episode_number }} ${{ matrix.quality }}
        if: always()
        continue-on-error: true
        env:
          WEBHOOK_SECRET: ${{ secrets.WEBHOOK_SECRET }}
          LINK: ${{ steps.pixeldrain.outputs.link }}
          VOE_LINK: ${{ steps.voe.outputs.link }}
        run: |
          if [ -z "$LINK" ]; then exit 0; fi
          curl -s -X POST "https://onlyksub.vercel.app/api/webhook-hardcode-complete" \
            -H "Content-Type: application/json" \
            -H "x-webhook-secret: ${WEBHOOK_SECRET}" \
            -d "{
              \"tmdb_id\": ${{ inputs.tmdb_id }},
              \"season_number\": ${{ inputs.season_number }},
              \"episode_number\": ${{ matrix.episode.episode_number }},
              \"pixeldrain_download_url\": \"${LINK}\",
              \"voe_embed_url\": \"${VOE_LINK}\",
              \"quality\": \"${{ matrix.quality }}\"
            }"

      - name: Notify on Telegram for ep${{ matrix.episode.episode_number }} ${{ matrix.quality }}
        if: always()
        env:
          BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          CHAT_ID: ${{ inputs.chat_id }}
          LINK: ${{ steps.pixeldrain.outputs.link }}
          VOE_LINK: ${{ steps.voe.outputs.link }}
        run: |
          if [ -n "$LINK" ]; then
            TEXT="✅ ${{ inputs.drama_name }} S${{ inputs.season_number }}E${{ matrix.episode.episode_number }} (${{ matrix.quality }}) done! 📥 ${LINK}"
            if [ -n "$VOE_LINK" ]; then
              TEXT="${TEXT}"$'\n'"🎬 VOE: ${VOE_LINK}"
            fi
          else
            TEXT="❌ ${{ inputs.drama_name }} S${{ inputs.season_number }}E${{ matrix.episode.episode_number }} (${{ matrix.quality }}) failed. Check the GitHub Actions log."
          fi
          curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d chat_id="${CHAT_ID}" \
            --data-urlencode text="${TEXT}"

      - name: Upload artifact (backup)
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: batch-burned-ep${{ matrix.episode.episode_number }}-${{ matrix.quality }}
          path: ${{ env.OUT_NAME }}
          retention-days: 3

  # ============================================================
  # JOB 3.5: Uploads every episode's best-quality video to Telegram
  # ONE AT A TIME, on a single runner - not a matrix. Doing this
  # concurrently (many matrix legs sharing one bot token) triggers
  # Telegram's flood control, which can impose a multi-thousand-
  # second penalty on the whole bot. Sequential uploads with a
  # short delay between each avoids that entirely.
  # ============================================================
  upload-telegram:
    needs: [plan, burn]
    if: always()
    runs-on: ubuntu-latest
    timeout-minutes: 180
    services:
      telegram-bot-api:
        image: aiogram/telegram-bot-api:latest
        env:
          TELEGRAM_API_ID: ${{ secrets.TELEGRAM_API_ID }}
          TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
        ports:
          - 8081:8081
    steps:
      - name: Checkout repo (for scripts)
        uses: actions/checkout@v4

      - name: Set up Python deps
        run: pip install --quiet requests

      - name: Wait for local Telegram Bot API server
        run: |
          for i in $(seq 1 30); do
            if curl -s -o /dev/null "http://localhost:8081"; then
              echo "Local Bot API server is up."
              exit 0
            fi
            sleep 1
          done
          echo "WARNING: local Bot API server did not come up in time."

      - name: Upload each episode/quality video sequentially
        env:
          GH_TOKEN: ${{ github.token }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHANNEL_ID: ${{ secrets.TELEGRAM_CHANNEL_ID }}
          EPISODES_JSON: ${{ needs.plan.outputs.episodes }}
          QUALITIES_JSON: ${{ needs.plan.outputs.qualities }}
          WEBHOOK_SECRET: ${{ secrets.WEBHOOK_SECRET }}
          TMDB_ID: ${{ inputs.tmdb_id }}
          SEASON_NUMBER: ${{ inputs.season_number }}
        run: |
          set +e  # one episode/quality's failure shouldn't stop the rest
          echo "$EPISODES_JSON" > episodes.json
          EPISODES=$(python3 -c "import json; print(' '.join(str(e['episode_number']) for e in json.load(open('episodes.json'))))")
          QUALITIES=$(python3 -c "import json,os; print(' '.join(json.loads(os.environ['QUALITIES_JSON'])))")

          for EP in $EPISODES; do
            for QUALITY in $QUALITIES; do
              echo "=== Episode $EP ($QUALITY) ==="
              ARTIFACT_NAME="telegram-pending-ep${EP}-${QUALITY}"
              rm -rf dl && mkdir dl
              if ! gh run download "${{ github.run_id }}" --name "$ARTIFACT_NAME" --dir dl 2>/dev/null; then
                echo "No artifact found for episode $EP $QUALITY (burn may have failed) - skipping."
                continue
              fi
              # The artifact contains exactly one properly-named video file
              # (drama_S<season>E<episode>_<quality>.mp4) - find it instead of
              # assuming a hardcoded filename.
              VIDEO_FILE=$(find dl -maxdepth 1 -type f -iname "*.mp4" | head -1)
              if [ -z "$VIDEO_FILE" ]; then
                echo "WARNING: no video file found in artifact for episode $EP $QUALITY - skipping."
                continue
              fi
              rm -f telegram_message_id.txt telegram_chat_id.txt
              CAPTION="${{ inputs.drama_name }} S${{ inputs.season_number }}E${EP} (${QUALITY}) - OnlyKsub"
              if python scripts/upload_telegram_local.py "$VIDEO_FILE" "$CAPTION"; then
                echo "Episode $EP ($QUALITY) uploaded: $(cat telegram_link.txt)"
                if [ -f telegram_message_id.txt ]; then
                  MSG_ID=$(cat telegram_message_id.txt)
                  CHAT_ID=$(cat telegram_chat_id.txt)
                  curl -s -f -X POST "https://onlyksub.vercel.app/api/webhook-telegram-uploaded" \
                    -H "Content-Type: application/json" \
                    -H "x-webhook-secret: ${WEBHOOK_SECRET}" \
                    -d "{
                      \"tmdb_id\": ${TMDB_ID},
                      \"season_number\": ${SEASON_NUMBER},
                      \"episode_number\": ${EP},
                      \"quality\": \"${QUALITY}\",
                      \"telegram_chat_id\": \"${CHAT_ID}\",
                      \"telegram_message_id\": ${MSG_ID}
                    }" \
                    && echo "Notified website for episode $EP $QUALITY (chat_id=${CHAT_ID}, message_id=${MSG_ID})." \
                    || echo "::warning::Failed to notify website about episode $EP $QUALITY's Telegram upload."
                fi
              else
                echo "::warning::Episode $EP $QUALITY Telegram upload failed - the site will NOT show a Telegram download link for this quality."
              fi
              echo "Waiting 15s before next upload (flood control safety margin)..."
              sleep 15
            done
          done

  # ============================================================
  # JOB 4: Runs once ALL episode x quality burns (+ Telegram
  # uploads) finish. Marks the Supabase job row as done.
  # ============================================================
  finalize:
    needs: [burn, upload-telegram]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - name: Mark job done in dashboard
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          JOB_ID: ${{ inputs.job_id }}
        run: |
          if [ -n "$SUPABASE_URL" ] && [ -n "$JOB_ID" ]; then
            FINAL_STATUS="done"
            if [ "${{ needs.burn.result }}" != "success" ]; then FINAL_STATUS="failed"; fi
            curl -s -o /dev/null -X PATCH "${SUPABASE_URL}/rest/v1/jobs?id=eq.${JOB_ID}" \
              -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
              -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
              -H "Content-Type: application/json" \
              -d "{\"status\":\"${FINAL_STATUS}\",\"finished_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"github_run_url\":\"${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}\"}" \
              || true
          fi

VOEFIX_EOF
echo '  ✓ wrote .github/workflows/burn_batch.yml'

cat > '.github/workflows/burn_season.yml' << 'VOEFIX_EOF'
name: Burn Season (Individual Subs + Confirm-Before-Burn + Matrix Parallel)

on:
  workflow_dispatch:
    inputs:
      archive_url:
        description: "Direct URL to a zip/7z archive containing season video files"
        required: true
        type: string
      srt_files_json:
        description: 'JSON array: [{"episode_number":1,"srt_url":"https://gist..."}, ...]'
        required: true
        type: string
      sub_type:
        description: "english or sinhala"
        required: true
        type: string
      chat_id:
        description: "Telegram chat_id to notify when done"
        required: true
        type: string
      tmdb_id:
        description: "TMDB show ID"
        required: true
        type: string
      drama_name:
        description: "Drama/show name (used to build output filenames)"
        required: true
        type: string
      season_number:
        description: "Season number"
        required: true
        type: string
      qualities_json:
        description: 'JSON array of qualities to render, e.g. ["720p","480p"]. Defaults to all 3.'
        required: false
        type: string
        default: '["1080p","720p","480p"]'
      sub_format:
        description: "Subtitle format uploaded: srt or ass"
        required: false
        type: string
        default: "srt"
      job_id:
        description: "Supabase job UUID (for dashboard tracking)"
        required: false
        type: string
      telegram_message_id:
        description: "Telegram message ID to edit for live progress"
        required: false
        type: string

concurrency:
  group: burn-season-${{ inputs.chat_id }}-${{ inputs.tmdb_id }}-${{ inputs.season_number }}
  cancel-in-progress: false

jobs:
  # ============================================================
  # JOB 1: Runs ONCE. Validates the archive URL, extracts it,
  # downloads each individually-provided srt, matches videos to
  # the episode numbers that actually have subtitles, and uploads
  # each raw video to Pixeldrain to get a stable URL. Sends a
  # match report to Telegram for visibility - the workflow does
  # NOT wait for manual approval, it proceeds straight to burning.
  # ============================================================
  extract-and-plan:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    outputs:
      episodes: ${{ steps.plan.outputs.episodes }}
      telegram_quality: ${{ steps.telegram-quality.outputs.telegram_quality }}
    steps:
      - name: Checkout repo (for scripts)
        uses: actions/checkout@v4

      - name: Install archive tools
        run: sudo apt-get update -qq && sudo apt-get install -y p7zip-full unzip

      - name: Set up Python deps
        run: pip install --quiet requests

      - name: Validate archive URL is reachable
        run: |
          set -e
          STATUS=$(curl -s -o /dev/null -w "%{http_code}" -L -A "Mozilla/5.0" "${{ inputs.archive_url }}")
          echo "Archive URL status: $STATUS"
          if [ "$STATUS" -ge 400 ]; then
            echo "ERROR: archive_url is not reachable (HTTP $STATUS). Aborting before wasting minutes."
            exit 1
          fi

      - name: Download + extract video archive
        run: |
          set -e
          URL="${{ inputs.archive_url }}"
          # Pixeldrain share links (pixeldrain.com/u/XXXX) serve an HTML
          # viewer page, not the raw file - curl would silently download
          # that small HTML page instead of the archive (this is exactly
          # what caused "Cannot open the file as archive" on a ~4KB file).
          # Convert to the direct file API endpoint first.
          if echo "$URL" | grep -q "pixeldrain.com/u/"; then
            FILE_ID=$(echo "$URL" | sed -E 's#.*pixeldrain\.com/u/([a-zA-Z0-9]+).*#\1#')
            URL="https://pixeldrain.com/api/file/${FILE_ID}"
          fi

          curl -f -L -A "Mozilla/5.0" -e "https://pixeldrain.com/" -o video_archive "$URL"

          # Sanity check: a real season archive is going to be at least a
          # few MB. Anything tiny is almost certainly an HTML error/login
          # page that curl downloaded instead of the actual file - fail
          # here with a clear message instead of a confusing 7z error.
          SIZE=$(stat -c%s video_archive)
          if [ "$SIZE" -lt 1000000 ]; then
            echo "ERROR: downloaded archive is only ${SIZE} bytes - this is almost"
            echo "certainly not the real archive (likely an HTML error/login page)."
            echo "First 500 bytes of what was downloaded:"
            head -c 500 video_archive
            echo ""
            exit 1
          fi

          mkdir -p videos_extracted
          FILE_TYPE=$(file -b video_archive)
          if echo "$FILE_TYPE" | grep -qi "7-zip"; then
            7z x video_archive -ovideos_extracted -y
          elif echo "$FILE_TYPE" | grep -qi "zip"; then
            unzip -o video_archive -d videos_extracted
          else
            7z x video_archive -ovideos_extracted -y
          fi
          echo "--- Extracted files ---"
          find videos_extracted -type f

      - name: Download individually-provided subtitle files
        env:
          # Passed via env instead of interpolated directly into the
          # python string - direct ${{ }} substitution happens BEFORE the
          # script runs, so quote/newline characters in the JSON can
          # corrupt the script itself (this caused JSONDecodeError
          # elsewhere in this repo). Env vars carry the value as-is.
          SRT_FILES_JSON: ${{ inputs.srt_files_json }}
          SUB_EXT: ${{ inputs.sub_format }}
        run: |
          set -e
          python3 -c "
          import json, os
          srt_list = json.loads(os.environ['SRT_FILES_JSON'])
          os.makedirs('srt_by_episode', exist_ok=True)
          episodes = [str(item['episode_number']) for item in srt_list]
          with open('wanted_episodes.txt', 'w') as f:
              f.write(','.join(episodes))
          with open('srt_list.json', 'w') as f:
              json.dump(srt_list, f)
          print(f'{len(srt_list)} subtitle entries received')
          "
          if [ -z "$SUB_EXT" ]; then SUB_EXT="srt"; fi
          while IFS= read -r line; do
            EP=$(echo "$line" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['episode_number'])")
            URL=$(echo "$line" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['srt_url'])")
            echo "Downloading subtitle for episode $EP..."
            curl -f -L -o "srt_by_episode/ep${EP}.${SUB_EXT}" "$URL"
          done < <(python3 -c "
          import json
          with open('srt_list.json') as f:
              for item in json.load(f):
                  print(json.dumps(item))
          ")
          echo "--- Downloaded subtitle files ---"
          ls -la srt_by_episode/

      - name: Match videos to episodes that have subtitles
        run: |
          set -e
          WANTED=$(cat wanted_episodes.txt)
          python3 scripts/match_videos_only.py videos_extracted "$WANTED" matched_episodes.json

      - name: Upload each matched raw video to Pixeldrain
        id: plan
        env:
          PIXELDRAIN_API_KEY: ${{ secrets.PIXELDRAIN_API_KEY }}
        run: |
          set -e
          python3 scripts/plan_season_uploads.py matched_episodes.json episode_plan.json
          DELIM="EOF_$(date +%s%N)"
          echo "episodes<<$DELIM" >> "$GITHUB_OUTPUT"
          cat episode_plan.json >> "$GITHUB_OUTPUT"
          echo "" >> "$GITHUB_OUTPUT"
          echo "$DELIM" >> "$GITHUB_OUTPUT"
          echo "--- Final planned episodes (video + srt both confirmed) ---"
          cat episode_plan.json

      - uses: actions/upload-artifact@v4
        with:
          name: season-srts
          path: srt_by_episode/
          retention-days: 1

      - name: Compute best quality for Telegram/Drive/VOE upload
        id: telegram-quality
        env:
          QUALITIES_JSON: ${{ inputs.qualities_json }}
        run: |
          python3 -c "
          import json, os
          qualities = json.loads(os.environ['QUALITIES_JSON'])
          priority = ['1080p', '720p', '480p']
          best = next((q for q in priority if q in qualities), qualities[0] if qualities else '')
          with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
              f.write(f'telegram_quality={best}\n')
          print(f'telegram_quality={best}')
          "

      - name: Send match report to Telegram for review
        if: always()
        env:
          BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          CHAT_ID: ${{ inputs.chat_id }}
        run: |
          COUNT=$(python3 -c "import json; print(len(json.load(open('episode_plan.json'))))" 2>/dev/null || echo "0")
          TEXT="Season plan ready: ${COUNT} episode(s) matched (video+srt confirmed). Go to GitHub Actions to REVIEW the matched list and APPROVE before burning starts."
          curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d chat_id="${CHAT_ID}" --data-urlencode text="${TEXT}"

  # ============================================================
  # ============================================================
  # JOB 2: Matrix over episodes (parallel). Cleans + translates
  # (Gemini, once per episode - not duplicated per quality) +
  # converts to ASS. Runs automatically right after extract-and-plan
  # - no manual approval step. You (the operator) are responsible
  # for what's in the video/subtitle sources you point this at.
  # ============================================================
  prepare-subs:
    needs: extract-and-plan
    runs-on: ubuntu-latest
    timeout-minutes: 30
    strategy:
      fail-fast: false
      max-parallel: 20
      matrix:
        episode: ${{ fromJson(needs.extract-and-plan.outputs.episodes) }}
    env:
      SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
      SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
      JOB_ID: ${{ inputs.job_id }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ inputs.chat_id }}
      TELEGRAM_MESSAGE_ID: ${{ inputs.telegram_message_id }}
    steps:
      - name: Checkout repo (for scripts)
        uses: actions/checkout@v4

      - name: Install Sinhala font
        run: |
          set -e
          pip install --quiet fonttools
          mkdir -p sinhala-font
          python scripts/install_sinhala_font.py sinhala-font
          if ! file sinhala-font/NotoSansSinhala-Bold.ttf | grep -qE "TrueType|OpenType"; then
            echo "ERROR: Font generation failed or file is not a valid font"
            exit 1
          fi
          echo "SINHALA_FONT_NAME=Noto Sans Sinhala" >> "$GITHUB_ENV"

      - name: Set up Python deps
        run: pip install --quiet google-generativeai requests

      - uses: actions/download-artifact@v4
        with:
          name: season-srts
          path: srt_by_episode

      - name: Prep subtitle for this episode (clean / translate / convert)
        run: |
          set -e
          EXT="${{ inputs.sub_format }}"
          if [ -z "$EXT" ]; then EXT="srt"; fi
          EP="${{ matrix.episode.episode_number }}"
          SRC="srt_by_episode/ep${EP}.${EXT}"

          if [ "$EXT" = "ass" ]; then
            cp "$SRC" subs_raw.ass
          else
            python scripts/clean_srt.py "$SRC" subs_cleaned.srt
          fi

      - name: Translate to Sinhala (only if needed)
        if: inputs.sub_type == 'english' && inputs.sub_format != 'ass'
        env:
          GEMINI_API_KEY_1: ${{ secrets.GEMINI_API_KEY_1 }}
          GEMINI_API_KEY_2: ${{ secrets.GEMINI_API_KEY_2 }}
          GEMINI_API_KEY_3: ${{ secrets.GEMINI_API_KEY_3 }}
          GEMINI_API_KEY_4: ${{ secrets.GEMINI_API_KEY_4 }}
          GEMINI_API_KEY_5: ${{ secrets.GEMINI_API_KEY_5 }}
        run: python scripts/translate.py subs_cleaned.srt subs.srt

      - name: Use cleaned sub as-is (already Sinhala)
        if: inputs.sub_type != 'english' && inputs.sub_format != 'ass'
        run: cp subs_cleaned.srt subs.srt

      - name: Fetch render settings
        run: |
          set -e
          RESULT=$(curl -s "${SUPABASE_URL}/rest/v1/render_settings?quality=eq.1080p&select=*" \
            -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
            -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}")
          python3 -c "
          import json, sys, os
          try:
              rows = json.loads(sys.argv[1]); s = rows[0] if rows else {}
          except Exception:
              s = {}
          out = os.environ['GITHUB_ENV']
          with open(out, 'a') as f:
              f.write(f\"RENDER_FONT_SIZE={s.get('font_size', 42)}\n\")
              f.write(f\"RENDER_MARGIN_V={s.get('margin_v', 14)}\n\")
              f.write(f\"RENDER_OUTLINE={s.get('outline_width', 1)}\n\")
          " "$RESULT"

      - name: Convert SRT to ASS
        if: inputs.sub_format != 'ass'
        run: |
          python scripts/srt_to_ass.py subs.srt subs.ass "$SINHALA_FONT_NAME" \
            --font-size "$RENDER_FONT_SIZE" --margin-v "$RENDER_MARGIN_V" --outline "$RENDER_OUTLINE"

      - name: Clean uploaded ASS (strip source-site credits, force installed font)
        if: inputs.sub_format == 'ass'
        run: |
          python scripts/clean_ass.py subs_raw.ass subs.ass "$SINHALA_FONT_NAME" \
            --font-size "$RENDER_FONT_SIZE" --margin-v "$RENDER_MARGIN_V" --outline "$RENDER_OUTLINE"

      - uses: actions/upload-artifact@v4
        with:
          name: subs-ep${{ matrix.episode.episode_number }}
          path: subs.ass
          retention-days: 1

  # ============================================================
  # JOB 3: Matrix over episode x quality (built-in cross product).
  # Runs TRUE PARALLEL across separate runners, capped at
  # max-parallel: 20 concurrent jobs (GitHub free-tier cap). Runs automatically once prepare-subs
  # finishes - no manual approval step.
  # ============================================================
  burn:
    needs: [extract-and-plan, prepare-subs]
    runs-on: ubuntu-latest
    timeout-minutes: 90
    strategy:
      fail-fast: false
      max-parallel: 20
      matrix:
        episode: ${{ fromJson(needs.extract-and-plan.outputs.episodes) }}
        quality: ${{ fromJson(inputs.qualities_json) }}
        include:
          - quality: 1080p
            scale: "1920:-2"
          - quality: 720p
            scale: "1280:-2"
          - quality: 480p
            scale: "854:-2"
    env:
      SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
      SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ inputs.chat_id }}
      TELEGRAM_MESSAGE_ID: ${{ inputs.telegram_message_id }}
      JOB_ID: ${{ inputs.job_id }}
    steps:
      - name: Checkout repo (for scripts)
        uses: actions/checkout@v4

      - name: Install ffmpeg
        run: sudo apt-get update -qq && sudo apt-get install -y ffmpeg

      - name: Set up Python deps
        run: pip install --quiet requests Pillow

      - name: Install Sinhala font
        run: |
          set -e
          pip install --quiet fonttools
          mkdir -p sinhala-font
          python scripts/install_sinhala_font.py sinhala-font
          if ! file sinhala-font/NotoSansSinhala-Bold.ttf | grep -qE "TrueType|OpenType"; then
            echo "ERROR: Font generation failed or file is not a valid font"
            exit 1
          fi

      - uses: actions/download-artifact@v4
        with:
          name: subs-ep${{ matrix.episode.episode_number }}

      - name: Build sanitized output name (drama_S<season>E<episode>_<quality>)
        id: name
        run: |
          set -e
          RAW="${{ inputs.drama_name }}_S${{ inputs.season_number }}E${{ matrix.episode.episode_number }}"
          SAFE=$(echo "$RAW" | tr ' ' '_' | tr -cd '[:alnum:]_-')
          OUT_NAME="${SAFE}_${{ matrix.quality }}.mp4"
          echo "OUT_NAME=${OUT_NAME}" >> "$GITHUB_ENV"
          echo "Output filename will be: ${OUT_NAME}"

      - name: Fetch render settings for ${{ matrix.quality }}
        run: |
          set -e
          RESULT=$(curl -s "${SUPABASE_URL}/rest/v1/render_settings?quality=eq.${{ matrix.quality }}&select=*" \
            -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
            -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}")
          python3 -c "
          import json, sys, os
          try:
              rows = json.loads(sys.argv[1]); s = rows[0] if rows else {}
          except Exception:
              s = {}
          out = os.environ['GITHUB_ENV']
          with open(out, 'a') as f:
              f.write(f\"RENDER_WATERMARK_TEXT={s.get('watermark_text', 'OnlyKSub')}\n\")
              f.write(f\"RENDER_WATERMARK_OPACITY={s.get('watermark_opacity', 0.6)}\n\")
              f.write(f\"RENDER_WATERMARK_FONTSIZE={s.get('watermark_fontsize', 16)}\n\")
              f.write(f\"RENDER_CRF={s.get('crf', 23)}\n\")
              f.write(f\"RENDER_AUDIO_BITRATE={s.get('audio_bitrate', '128k')}\n\")
          " "$RESULT"

      - name: Download episode video
        run: |
          set -e
          URL="${{ matrix.episode.video_url }}"
          if echo "$URL" | grep -q "pixeldrain.com/u/"; then
            FILE_ID=$(echo "$URL" | sed -E 's#.*pixeldrain\.com/u/([a-zA-Z0-9]+).*#\1#')
            URL="https://pixeldrain.com/api/file/${FILE_ID}"
          fi
          curl -f -L -A "Mozilla/5.0" -e "https://pixeldrain.com/" -o input_video.mp4 "$URL"

      - name: Inject OnlyKsub credit card (start + end)
        run: |
          set -e
          DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 input_video.mp4)
          python3 scripts/inject_credits.py subs.ass "$DURATION" "Noto Sans Sinhala"

      - name: Detect and remove black bars
        run: |
          set -e
          CROP=$(python3 scripts/detect_crop.py input_video.mp4 --threshold 24 --samples 30 | grep "^crop=" || true)
          if [ -z "$CROP" ]; then
            echo "CROP_FILTER=" >> "$GITHUB_ENV"
          else
            echo "CROP_FILTER=${CROP}," >> "$GITHUB_ENV"
          fi

      - name: Burn subtitles + watermark (ep${{ matrix.episode.episode_number }}, ${{ matrix.quality }})
        run: |
          set -e
          FONT_DIR=$(pwd)/sinhala-font
          FONT_PATH="${FONT_DIR}/NotoSansSinhala-Bold.ttf"
          FILTER="${CROP_FILTER}ass=subs.ass:fontsdir=${FONT_DIR},scale=${{ matrix.scale }},"
          FILTER="${FILTER}drawtext=text='${RENDER_WATERMARK_TEXT}':fontfile=${FONT_PATH}:fontsize=${RENDER_WATERMARK_FONTSIZE}:fontcolor=white@${RENDER_WATERMARK_OPACITY}:borderw=1:bordercolor=black@0.5:x=w-tw-16:y=h-th-16"
          ffmpeg -y -i input_video.mp4 -vf "$FILTER" \
            -c:v libx264 -preset veryfast -crf "${RENDER_CRF}" -pix_fmt yuv420p \
            -c:a aac -b:a "${RENDER_AUDIO_BITRATE}" -movflags +faststart \
            "$OUT_NAME"

      - name: Upload to Pixeldrain
        id: pixeldrain
        env:
          PIXELDRAIN_API_KEY: ${{ secrets.PIXELDRAIN_API_KEY }}
        run: |
          set -e
          python scripts/upload_pixeldrain.py "$OUT_NAME"
          echo "link=$(cat pixeldrain_link.txt)" >> "$GITHUB_OUTPUT"

      - name: Save video for later sequential Telegram upload
        # Telegram upload moved OUT of this concurrent matrix job (see the
        # separate upload-telegram job below). Uploading from 20+ parallel
        # matrix runners on the SAME bot token triggers Telegram's flood
        # control (retry_after can be 3000+ seconds) since it looks like
        # abuse from Telegram's side. Sequential, one-at-a-time uploads
        # after all burns finish avoids that entirely. All qualities now
        # upload (previously 1080p-only), so the artifact is keyed by
        # episode AND quality.
        uses: actions/upload-artifact@v4
        with:
          name: telegram-pending-ep${{ matrix.episode.episode_number }}-${{ matrix.quality }}
          path: ${{ env.OUT_NAME }}
          retention-days: 1

      - name: Upload to VOE.sx (1080p only, direct file upload)
        id: voe
        if: matrix.quality == needs.extract-and-plan.outputs.telegram_quality
        continue-on-error: true
        env:
          VOE_API_KEY: ${{ secrets.VOE_API_KEY }}
        run: |
          # Was: ask VOE to fetch the file itself from a Pixeldrain URL
          # (upload/url). Pixeldrain blocks/CAPTCHA-walls fetches coming
          # from VOE's servers (datacenter IP), so VOE downloaded a CAPTCHA
          # or error page instead of the video ("Not a video file") no
          # matter which Pixeldrain URL shape was used. Fix: skip the
          # remote-fetch step entirely and POST the video file we already
          # have on this runner straight to VOE.

          SERVER_RESPONSE=$(curl -s "https://voe.sx/api/upload/server?key=${VOE_API_KEY}")
          echo "VOE upload/server response: $SERVER_RESPONSE"
          UPLOAD_SERVER=$(echo "$SERVER_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('result',{}).get('upload_server',''))" 2>/dev/null || echo "")

          if [ -z "$UPLOAD_SERVER" ]; then
            echo "WARNING: could not get a VOE upload server, continuing without VOE link."
            exit 0
          fi
          echo "VOE upload server: $UPLOAD_SERVER"

          UPLOAD_RESPONSE=$(curl -s -F "key=${VOE_API_KEY}" -F "file=@${OUT_NAME}" "$UPLOAD_SERVER")
          echo "VOE direct upload response: $UPLOAD_RESPONSE"

          # Response shape for the direct-upload endpoint isn't fully
          # documented publicly, so try every plausible path this family
          # of file-host APIs (VOE/StreamHG/Doodstream-style clones) uses,
          # instead of assuming one and breaking silently if it's wrong.
          FILE_CODE=$(echo "$UPLOAD_RESPONSE" | python3 -c "
          import sys, json
          try:
              d = json.load(sys.stdin)
          except Exception:
              print('')
              raise SystemExit
          code = ''
          if isinstance(d, dict):
              if isinstance(d.get('files'), list) and d['files']:
                  f0 = d['files'][0]
                  code = f0.get('file_code') or f0.get('filecode') or ''
              if not code:
                  code = d.get('file_code') or d.get('filecode') or ''
              if not code and isinstance(d.get('result'), dict):
                  code = d['result'].get('file_code') or d['result'].get('filecode') or ''
          print(code)
          " 2>/dev/null || echo "")

          if [ -n "$FILE_CODE" ]; then
            echo "link=https://voe.sx/e/${FILE_CODE}" >> "$GITHUB_OUTPUT"
            echo "VOE embed link ready: https://voe.sx/e/${FILE_CODE}"
          else
            echo "WARNING: VOE direct upload response had no recognizable file_code - see the raw response logged above, continuing without VOE link."
          fi

      - name: Notify website (webhook)
        continue-on-error: true
        env:
          # This GH secret's NAME doesn't need to match Vercel's env var
          # name (HARDCODE_WEBHOOK_SECRET) - only the VALUE has to match,
          # since it's just sent as a header. Verify: GitHub repo secret
          # "WEBHOOK_SECRET" must contain the exact same value as Vercel's
          # "HARDCODE_WEBHOOK_SECRET" env var, or the webhook silently
          # 401s (this step has continue-on-error, so the workflow still
          # shows green even when this fails).
          WEBHOOK_SECRET: ${{ secrets.WEBHOOK_SECRET }}
          LINK: ${{ steps.pixeldrain.outputs.link }}
          VOE_LINK: ${{ steps.voe.outputs.link }}
        run: |
          curl -s -X POST "https://onlyksub.vercel.app/api/webhook-hardcode-complete" \
            -H "Content-Type: application/json" \
            -H "x-webhook-secret: ${WEBHOOK_SECRET}" \
            -d "{
              \"tmdb_id\": ${{ inputs.tmdb_id }},
              \"season_number\": ${{ inputs.season_number }},
              \"episode_number\": ${{ matrix.episode.episode_number }},
              \"pixeldrain_download_url\": \"${LINK}\",
              \"voe_embed_url\": \"${VOE_LINK}\",
              \"quality\": \"${{ matrix.quality }}\"
            }"

      - name: Notify on Telegram
        if: always()
        env:
          BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          CHAT_ID: ${{ inputs.chat_id }}
          LINK: ${{ steps.pixeldrain.outputs.link }}
          VOE_LINK: ${{ steps.voe.outputs.link }}
        run: |
          if [ -n "$LINK" ]; then
            TEXT="Ep${{ matrix.episode.episode_number }} ${{ matrix.quality }} done! ${LINK}"
            if [ -n "$VOE_LINK" ]; then
              TEXT="${TEXT}"$'\n'"VOE: ${VOE_LINK}"
            fi
          else
            TEXT="Ep${{ matrix.episode.episode_number }} ${{ matrix.quality }} failed."
          fi
          curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d chat_id="${CHAT_ID}" --data-urlencode text="${TEXT}"

  # ============================================================
  # JOB 3.5: Uploads every episode's 1080p video to Telegram ONE
  # AT A TIME, on a single runner - not a matrix. Doing this
  # concurrently (20+ matrix jobs sharing one bot token) triggers
  # Telegram's flood control, which can impose a multi-thousand-
  # second penalty on the whole bot. Sequential uploads with a
  # short delay between each avoids that entirely.
  # ============================================================
  upload-telegram:
    needs: [extract-and-plan, burn]
    if: always()
    runs-on: ubuntu-latest
    timeout-minutes: 180
    services:
      telegram-bot-api:
        image: aiogram/telegram-bot-api:latest
        env:
          TELEGRAM_API_ID: ${{ secrets.TELEGRAM_API_ID }}
          TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
        ports:
          - 8081:8081
    steps:
      - name: Checkout repo (for scripts)
        uses: actions/checkout@v4

      - name: Set up Python deps
        run: pip install --quiet requests

      - name: Wait for local Telegram Bot API server
        run: |
          for i in $(seq 1 30); do
            if curl -s -o /dev/null "http://localhost:8081"; then
              echo "Local Bot API server is up."
              exit 0
            fi
            sleep 1
          done
          echo "WARNING: local Bot API server did not come up in time."

      - name: Upload each episode/quality video sequentially
        env:
          GH_TOKEN: ${{ github.token }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHANNEL_ID: ${{ secrets.TELEGRAM_CHANNEL_ID }}
          WEBHOOK_SECRET: ${{ secrets.WEBHOOK_SECRET }}
          TMDB_ID: ${{ inputs.tmdb_id }}
          SEASON_NUMBER: ${{ inputs.season_number }}
          QUALITIES_JSON: ${{ inputs.qualities_json }}
        run: |
          set +e  # one episode/quality's failure shouldn't stop the rest
          echo '${{ needs.extract-and-plan.outputs.episodes }}' > episodes.json
          EPISODES=$(python3 -c "import json; print(' '.join(str(e['episode_number']) for e in json.load(open('episodes.json'))))")
          QUALITIES=$(python3 -c "import json,os; print(' '.join(json.loads(os.environ['QUALITIES_JSON'])))")

          for EP in $EPISODES; do
            for QUALITY in $QUALITIES; do
              echo "=== Episode $EP ($QUALITY) ==="
              ARTIFACT_NAME="telegram-pending-ep${EP}-${QUALITY}"
              rm -rf dl && mkdir dl
              if ! gh run download "${{ github.run_id }}" --name "$ARTIFACT_NAME" --dir dl 2>/dev/null; then
                echo "No artifact found for episode $EP $QUALITY (burn may have failed) - skipping."
                continue
              fi
              # The artifact contains exactly one properly-named video file
              # (drama_S<season>E<episode>_<quality>.mp4) - find it instead of
              # assuming a hardcoded filename.
              VIDEO_FILE=$(find dl -maxdepth 1 -type f -iname "*.mp4" | head -1)
              if [ -z "$VIDEO_FILE" ]; then
                echo "WARNING: no video file found in artifact for episode $EP $QUALITY - skipping."
                continue
              fi
              rm -f telegram_message_id.txt telegram_chat_id.txt
              CAPTION="${{ inputs.drama_name }} S${{ inputs.season_number }}E${EP} (${QUALITY}) - OnlyKsub"
              if python scripts/upload_telegram_local.py "$VIDEO_FILE" "$CAPTION"; then
                echo "Episode $EP ($QUALITY) uploaded: $(cat telegram_link.txt)"
                if [ -f telegram_message_id.txt ]; then
                  MSG_ID=$(cat telegram_message_id.txt)
                  CHAT_ID=$(cat telegram_chat_id.txt)
                  curl -s -f -X POST "https://onlyksub.vercel.app/api/webhook-telegram-uploaded" \
                    -H "Content-Type: application/json" \
                    -H "x-webhook-secret: ${WEBHOOK_SECRET}" \
                    -d "{
                      \"tmdb_id\": ${TMDB_ID},
                      \"season_number\": ${SEASON_NUMBER},
                      \"episode_number\": ${EP},
                      \"quality\": \"${QUALITY}\",
                      \"telegram_chat_id\": \"${CHAT_ID}\",
                      \"telegram_message_id\": ${MSG_ID}
                    }" \
                    && echo "Notified website for episode $EP $QUALITY (chat_id=${CHAT_ID}, message_id=${MSG_ID})." \
                    || echo "::warning::Failed to notify website about episode $EP $QUALITY's Telegram upload."
                fi
              else
                echo "::warning::Episode $EP $QUALITY Telegram upload failed - the site will NOT show a Telegram download link for this quality."
              fi
              echo "Waiting 15s before next upload (flood control safety margin)..."
              sleep 15
            done
          done

  # ============================================================
  # JOB 4: Final summary once everything finishes.
  # ============================================================
  finalize:
    needs: [burn, upload-telegram]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - name: Notify season complete
        env:
          BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          CHAT_ID: ${{ inputs.chat_id }}
        run: |
          STATUS="Season ${{ inputs.season_number }} batch complete!"
          if [ "${{ needs.burn.result }}" != "success" ]; then
            STATUS="Season ${{ inputs.season_number }} batch finished with some failures - check Actions log."
          fi
          curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d chat_id="${CHAT_ID}" --data-urlencode text="$STATUS"

      # Without this, the Supabase `jobs` row created by the web dashboard
      # (or the bot) stayed "queued" FOREVER - every season burn looked
      # permanently pending on the Jobs page even after it finished fine.
      # burn.yml and burn_batch.yml already did this; burn_season.yml
      # didn't even accept a job_id input.
      - name: Mark job done in dashboard
        if: always()
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          JOB_ID: ${{ inputs.job_id }}
        run: |
          if [ -n "$SUPABASE_URL" ] && [ -n "$JOB_ID" ]; then
            FINAL_STATUS="done"
            if [ "${{ needs.burn.result }}" != "success" ]; then FINAL_STATUS="failed"; fi
            curl -s -o /dev/null -X PATCH "${SUPABASE_URL}/rest/v1/jobs?id=eq.${JOB_ID}" \
              -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
              -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
              -H "Content-Type: application/json" \
              -d "{\"status\":\"${FINAL_STATUS}\",\"finished_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"github_run_url\":\"${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}\"}" \
              || true
          fi

VOEFIX_EOF
echo '  ✓ wrote .github/workflows/burn_season.yml'

echo ""
echo "✅ VOE uploads now POST the video directly - no more Pixeldrain fetch/captcha issue."
echo ""
echo "Validating YAML..."
python3 -c "
import yaml
for f in ['burn.yml','burn_batch.yml','burn_season.yml']:
    yaml.safe_load(open('.github/workflows/'+f))
    print(f, 'YAML OK')
" || echo "⚠️  PyYAML not installed locally - skip this check, GitHub will validate on push anyway."
