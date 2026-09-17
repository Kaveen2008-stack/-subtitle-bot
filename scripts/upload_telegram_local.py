"""
Uploads a video to a Telegram channel via a LOCAL Telegram Bot API server
(not api.telegram.org directly) - raises the upload limit from the normal
Bot API's 50MB to 2000MB (2GB), using the SAME bot token. No phone-number
login or session string needed.

Usage: python upload_telegram_local.py <file_path> <caption>

Required env vars:
  TELEGRAM_BOT_TOKEN     - your existing bot token
  TELEGRAM_CHANNEL_ID    - channel to post to (numeric id or @username)
Optional:
  LOCAL_BOT_API_BASE     - defaults to http://localhost:8081

On success, writes the result in two forms so either consumer can read it:
  - telegram_link.txt / telegram_message_id.txt / telegram_chat_id.txt
    (plain text, one value per file)
  - telegram_upload_result.json {chat_id, message_id, link}
    (read by report_telegram_upload.py to tell the website which message
    holds this episode's video)
"""
import os
import sys
import time
import json
import requests


def build_message_link(channel_id: str, message_id: int) -> str:
    if channel_id.startswith("@"):
        return f"https://t.me/{channel_id[1:]}/{message_id}"
    if channel_id.startswith("-100"):
        internal_id = channel_id[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"
    return f"chat={channel_id} message_id={message_id}"


def upload_once(url, channel_id, caption, file_path):
    with open(file_path, "rb") as f:
        resp = requests.post(
            url,
            data={
                "chat_id": channel_id,
                "caption": caption,
                "supports_streaming": True,
            },
            files={"video": f},
            timeout=3600,
        )
    return resp


def write_result(channel_id, message_id, link):
    with open("telegram_link.txt", "w") as f:
        f.write(link)
    with open("telegram_message_id.txt", "w") as f:
        f.write(str(message_id))
    with open("telegram_chat_id.txt", "w") as f:
        f.write(str(channel_id))
    with open("telegram_upload_result.json", "w") as f:
        json.dump({"chat_id": str(channel_id), "message_id": message_id, "link": link}, f)


def main():
    file_path = sys.argv[1]
    caption = sys.argv[2] if len(sys.argv) > 2 else ""

    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    channel_id = os.environ["TELEGRAM_CHANNEL_ID"]
    api_base = os.environ.get("LOCAL_BOT_API_BASE", "http://localhost:8081")

    url = f"{api_base}/bot{bot_token}/sendVideo"

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    print(f"Uploading {file_path} ({file_size_mb:.1f} MB) to Telegram via local Bot API server...")

    max_attempts = 6
    backoff = 5  # seconds, doubles each attempt (capped)

    for attempt in range(1, max_attempts + 1):
        try:
            resp = upload_once(url, channel_id, caption, file_path)
        except requests.exceptions.RequestException as e:
            print(f"Attempt {attempt}/{max_attempts}: network error ({e}), retrying in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
            continue

        try:
            data = resp.json()
        except ValueError:
            print(f"Attempt {attempt}/{max_attempts}: non-JSON response (HTTP {resp.status_code}), retrying in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
            continue

        if data.get("ok"):
            message_id = data["result"]["message_id"]
            # Prefer the chat id Telegram actually confirmed the message
            # landed in over the channel_id we were given (e.g. an
            # @username input still resolves to a numeric chat id here).
            chat_id = str(data["result"].get("chat", {}).get("id", channel_id))
            link = build_message_link(channel_id, message_id)
            write_result(chat_id, message_id, link)
            print(f"Uploaded successfully: {link} (chat_id={chat_id}, message_id={message_id})")
            return

        # Telegram flood control (429) - honor the retry_after it tells us
        retry_after = None
        if resp.status_code == 429 or data.get("error_code") == 429:
            retry_after = (data.get("parameters") or {}).get("retry_after")

        if retry_after:
            wait_s = int(retry_after) + 1
            print(f"Attempt {attempt}/{max_attempts}: flood control, Telegram asked us to wait {wait_s}s...", file=sys.stderr)
            time.sleep(wait_s)
            continue

        # 5xx server errors are usually transient - retry with backoff
        if resp.status_code >= 500:
            print(f"Attempt {attempt}/{max_attempts}: server error (HTTP {resp.status_code}): {data}, retrying in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
            continue

        # Anything else (bad request, invalid chat, etc.) won't be fixed by
        # retrying - fail fast so we don't waste 6 attempts on a real bug.
        print(f"Telegram upload failed (non-retryable): {data}", file=sys.stderr)
        sys.exit(1)

    print(f"Telegram upload failed after {max_attempts} attempts.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
