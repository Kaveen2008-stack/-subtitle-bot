"""
Everything needed to hand a job off to GitHub Actions:
  1. Push the raw SRT (untranslated is fine) to a secret Gist -> raw URL.
  2. Dispatch burn.yml with video_url, srt_url, sub_type, chat_id.
The workflow itself does translation (if needed), download, burn, Drive
upload, and the Telegram "done" notification.
"""
import requests

import config
from logger import get_logger

log = get_logger(__name__)
GITHUB_API = "https://api.github.com"


def _headers():
    return {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}


def push_srt_to_gist(srt_path: str, chat_id) -> str | None:
    try:
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
        payload = {
            "description": f"Subtitles for chat {chat_id}",
            "public": False,
            "files": {f"{chat_id}_subs.srt": {"content": content}},
        }
        resp = requests.post(f"{GITHUB_API}/gists", headers=_headers(), json=payload, timeout=30)
        if resp.status_code != 201:
            log.error("Gist creation failed (%s): %s", resp.status_code, resp.text)
        resp.raise_for_status()
        data = resp.json()
        filename = next(iter(data["files"]))
        return data["files"][filename]["raw_url"]
    except Exception:
        log.exception("Failed to push SRT to Gist")
        return None
        data = resp.json()
        filename = next(iter(data["files"]))
        return data["files"][filename]["raw_url"]
    except Exception:
        log.exception("Failed to push SRT to Gist")
        return None


def trigger_burn_workflow(video_url: str, srt_url: str, sub_type: str, chat_id) -> bool:
    try:
        url = f"{GITHUB_API}/repos/{config.GITHUB_REPO}/actions/workflows/burn.yml/dispatches"
        payload = {
            "ref": config.GITHUB_BRANCH,
            "inputs": {
                "video_url": video_url,
                "srt_url": srt_url,
                "sub_type": sub_type,
                "chat_id": str(chat_id),
            },
        }
        resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
        if resp.status_code == 204:
            return True
        log.error("Workflow dispatch failed (%s): %s", resp.status_code, resp.text)
        return False
    except Exception:
        log.exception("Failed to trigger GitHub Actions workflow")
        return False


def run_via_github_actions(srt_path: str, video_url: str, sub_type: str, chat_id) -> tuple[bool, str]:
    srt_url = push_srt_to_gist(srt_path, chat_id)
    if not srt_url:
        return False, "❌ Couldn't upload the subtitle file. Try again."
    if not trigger_burn_workflow(video_url, srt_url, sub_type, chat_id):
        return False, "❌ Couldn't start the job. Check the bot's GitHub token/repo config."
    return True, "⏳ Job started on GitHub Actions. I'll message you here once it's done."
