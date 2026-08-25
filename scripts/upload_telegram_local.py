"""
Uploads a video to a Telegram channel via a LOCAL Telegram Bot API server
(not api.telegram.org directly) - this raises the upload limit from the
normal Bot API's 50MB to 2000MB (2GB), using the SAME bot token you
already have. No phone-number login or session string needed - the local
server only needs your api_id + api_hash (from my.telegram.org) to run.

Usage: python upload_telegram_local.py <file_path> <caption>

Required env vars:
  TELEGRAM_BOT_TOKEN     - your existing bot token (same one used elsewhere)
  TELEGRAM_CHANNEL_ID    - channel to post to. Either:
                             - a public channel username, e.g. "@mychannel"
                             - a numeric chat id, e.g. "-1001234567890"
Optional:
  LOCAL_BOT_API_BASE     - defaults to http://localhost:8081 (where the
                            telegram-bot-api service container listens,
                            see the workflow's `services:` block)
"""
import os
import sys
import requests


def build_message_link(channel_id: str, message_id: int) -> str:
    # Public channel (@username) -> https://t.me/username/123
    if channel_id.startswith("@"):
        return f"https://t.me/{channel_id[1:]}/{message_id}"
    # Private channel numeric id (-100xxxxxxxxxx) -> https://t.me/c/xxxxxxxxxx/123
    if channel_id.startswith("-100"):
        internal_id = channel_id[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"
    # Fallback: just return the raw id/message combo for debugging
    return f"chat={channel_id} message_id={message_id}"


def main():
    file_path = sys.argv[1]
    caption = sys.argv[2] if len(sys.argv) > 2 else ""

    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    channel_id = os.environ["TELEGRAM_CHANNEL_ID"]
    api_base = os.environ.get("LOCAL_BOT_API_BASE", "http://localhost:8081")

    url = f"{api_base}/bot{bot_token}/sendVideo"

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    print(f"Uploading {file_path} ({file_size_mb:.1f} MB) to Telegram via local Bot API server...")

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

    data = resp.json()
    if not data.get("ok"):
        print(f"Telegram upload failed: {data}", file=sys.stderr)
        sys.exit(1)

    message_id = data["result"]["message_id"]
    link = build_message_link(channel_id, message_id)

    with open("telegram_link.txt", "w") as f:
        f.write(link)

    print(f"Uploaded successfully: {link}")


if __name__ == "__main__":
    main()