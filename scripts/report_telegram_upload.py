"""
Reports a completed Telegram upload (chat_id + message_id) back to the
website, so the download bot can later copyMessage() this exact upload to
a user. Reads telegram_upload_result.json in the current working directory.

Usage: python report_telegram_upload.py <tmdb_id> <season_number> <episode_number> <quality>

Required env vars:
  WEBHOOK_SECRET     - must match onlyksub's HARDCODE_WEBHOOK_SECRET value
Optional:
  WEBSITE_BASE_URL   - defaults to https://onlyksub.xyz

Non-fatal by design - never fails the whole burn workflow.
"""
import os
import sys
import json
import requests


def main():
    if len(sys.argv) < 5:
        print("Usage: report_telegram_upload.py <tmdb_id> <season_number> <episode_number> <quality>")
        sys.exit(0)

    tmdb_id, season, episode, quality = sys.argv[1:5]

    try:
        with open("telegram_upload_result.json") as f:
            result = json.load(f)
    except FileNotFoundError:
        print("WARNING: telegram_upload_result.json not found - skipping report.")
        sys.exit(0)

    base_url = os.environ.get("WEBSITE_BASE_URL", "https://onlyksub.xyz")
    secret = os.environ.get("WEBHOOK_SECRET", "")

    try:
        resp = requests.post(
            f"{base_url}/api/webhook-telegram-uploaded",
            headers={"x-webhook-secret": secret, "Content-Type": "application/json"},
            json={
                "tmdb_id": int(tmdb_id),
                "season_number": int(season),
                "episode_number": int(episode),
                "quality": quality,
                "telegram_chat_id": result["chat_id"],
                "telegram_message_id": result["message_id"],
            },
            timeout=30,
        )
        if resp.ok:
            print(f"Reported Telegram upload for ep{episode} ({quality}) to website.")
        else:
            print(f"WARNING: failed to report Telegram upload: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"WARNING: failed to report Telegram upload: {e}")


if __name__ == "__main__":
    main()
