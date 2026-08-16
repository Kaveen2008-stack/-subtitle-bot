#!/usr/bin/env python3
"""
fetch_sublk_subs.py
--------------------
Finds a subtitle on sub.lk for a given search query, via our own
Cloudflare Worker (sublk-finder) which does the actual sub.lk scraping.

WHY THE WORKER: sub.lk's WAF blocks GitHub Actions/Codespaces IPs
directly (403 Forbidden), but allows Cloudflare Worker edge IPs. The
Worker also does the zip download + unzip server-side, so this script
just gets back plain srt text over HTTPS from our own domain - no
direct contact with sub.lk from this runner at all.

USAGE:
    # Just find + print the raw .srt content locally (no Gist):
    python scripts/fetch_sublk_subs.py --query "Agent Kim Reactivated 2026 S01 E10" --out subs_raw.srt

    # Find + publish to a Gist, print the raw URL (for workflow_dispatch):
    python scripts/fetch_sublk_subs.py --query "Agent Kim Reactivated 2026 S01 E10" --gist

Requires: pip install requests
Gist mode requires env var GITHUB_TOKEN (a PAT with "gist" scope).

EXIT CODES:
    0 = success
    1 = no search results / srt not found (see Worker error message)
    4 = gist publish failed (only relevant with --gist)
"""

import argparse
import os
import re
import sys

import requests

WORKER_BASE_URL = "https://sublk-finder.banujadewmina.workers.dev"


def fetch_srt_via_worker(query: str):
    """Calls our Cloudflare Worker's /get-srt endpoint, which does the
    sub.lk search + download + unzip server-side and returns plain
    srt text directly."""
    resp = requests.get(
        f"{WORKER_BASE_URL}/get-srt",
        params={"q": query},
        timeout=60,  # zip download + unzip can take a few seconds
    )
    if resp.status_code != 200:
        try:
            err = resp.json().get("error", resp.text)
        except Exception:
            err = resp.text
        print(f"ERROR: Worker returned {resp.status_code}: {err}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    print(f"Matched: {data['matchedTitle']}", file=sys.stderr)
    print(f"Page: {data['pageUrl']}", file=sys.stderr)
    return data["matchedTitle"], data["srtContent"]


def publish_gist(srt_content: str, filename: str = "subs.srt") -> str:
    """Publish the srt content as a GitHub Gist, return the raw URL."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN environment variable not set (needs 'gist' scope)")

    resp = requests.post(
        "https://api.github.com/gists",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "description": "Auto-fetched subtitle from sub.lk",
            "public": False,
            "files": {filename: {"content": srt_content}},
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["files"][filename]["raw_url"]


def main():
    parser = argparse.ArgumentParser(description="Find and fetch a subtitle from sub.lk (via Cloudflare Worker)")
    parser.add_argument("--query", required=True, help="Search query, e.g. 'Agent Kim Reactivated 2026 S01 E10'")
    parser.add_argument("--out", help="Save raw .srt content to this local file path")
    parser.add_argument("--gist", action="store_true", help="Publish to a GitHub Gist and print the raw URL")
    args = parser.parse_args()

    title, srt_content = fetch_srt_via_worker(args.query)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(srt_content)
        print(f"Saved to {args.out}", file=sys.stderr)

    if args.gist:
        try:
            raw_url = publish_gist(srt_content, filename=re.sub(r"[^\w.-]+", "_", title)[:80] + ".srt")
        except Exception as e:
            print(f"ERROR: Gist publish failed: {e}", file=sys.stderr)
            sys.exit(4)
        print(raw_url)
    elif not args.out:
        print(srt_content)


if __name__ == "__main__":
    main()