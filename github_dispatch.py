"""
Everything needed to hand a job off to GitHub Actions:
  1. Push the raw SRT(s) to a secret Gist -> raw URL(s).
  2. Dispatch burn.yml (single episode) or burn_batch.yml (full season/archive).
"""
import requests

import config
from logger import get_logger

log = get_logger(__name__)
GITHUB_API = "https://api.github.com"


def _headers():
    return {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}


def push_srt_to_gist(srt_path: str, label) -> str | None:
    try:
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
        payload = {
            "description": f"Subtitles for {label}",
            "public": False,
            "files": {f"{label}_subs.srt": {"content": content}},
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


def trigger_burn_workflow(
    video_url: str,
    srt_url: str,
    sub_type: str,
    chat_id,
    tmdb_id,
    season_number,
    episode_number,
) -> bool:
    try:
        url = f"{GITHUB_API}/repos/{config.GITHUB_REPO}/actions/workflows/burn.yml/dispatches"
        payload = {
            "ref": config.GITHUB_BRANCH,
            "inputs": {
                "video_url": video_url,
                "srt_url": srt_url,
                "sub_type": sub_type,
                "chat_id": str(chat_id),
                "tmdb_id": str(tmdb_id),
                "season_number": str(season_number),
                "episode_number": str(episode_number),
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


def trigger_burn_batch_workflow(
    archive_url: str,
    srt_urls: list[str],
    sub_type: str,
    chat_id,
    tmdb_id,
    season_number,
) -> bool:
    try:
        url = f"{GITHUB_API}/repos/{config.GITHUB_REPO}/actions/workflows/burn_batch.yml/dispatches"
        payload = {
            "ref": config.GITHUB_BRANCH,
            "inputs": {
                "archive_url": archive_url,
                "srt_urls": ",".join(srt_urls),
                "sub_type": sub_type,
                "chat_id": str(chat_id),
                "tmdb_id": str(tmdb_id),
                "season_number": str(season_number),
            },
        }
        resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
        if resp.status_code == 204:
            return True
        log.error("Batch workflow dispatch failed (%s): %s", resp.status_code, resp.text)
        return False
    except Exception:
        log.exception("Failed to trigger GitHub Actions batch workflow")
        return False


def run_via_github_actions(
    srt_path: str,
    video_url: str,
    sub_type: str,
    chat_id,
    tmdb_id=None,
    season_number=None,
    episode_number=None,
) -> tuple[bool, str]:
    srt_url = push_srt_to_gist(srt_path, chat_id)
    if not srt_url:
        return False, "❌ Couldn't upload the subtitle file. Try again."
    if not trigger_burn_workflow(
        video_url, srt_url, sub_type, chat_id, tmdb_id, season_number, episode_number
    ):
        return False, "❌ Couldn't start the job. Check the bot's GitHub token/repo config."
    return True, "⏳ Job started on GitHub Actions. I'll message you here once it's done."


def run_batch_via_github_actions(
    srt_paths: list[str],
    archive_url: str,
    sub_type: str,
    chat_id,
    tmdb_id=None,
    season_number=None,
) -> tuple[bool, str]:
    srt_urls = []
    for i, path in enumerate(srt_paths):
        url = push_srt_to_gist(path, f"{chat_id}_ep{i + 1}")
        if not url:
            return False, f"❌ Couldn't upload subtitle file #{i + 1}. Try again."
        srt_urls.append(url)

    if not trigger_burn_batch_workflow(
        archive_url, srt_urls, sub_type, chat_id, tmdb_id, season_number
    ):
        return False, "❌ Couldn't start the batch job. Check the bot's GitHub token/repo config."
    return True, f"⏳ Batch job started for {len(srt_urls)} episodes. This may take 1-2 hours. I'll notify you as each episode completes."
