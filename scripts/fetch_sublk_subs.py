#!/usr/bin/env python3
"""
fetch_sublk_subs.py
--------------------
Finds a subtitle on sub.lk for a given series + episode, via our own
Cloudflare Worker (sublk-finder) which does the actual sub.lk scraping.

WHY --series/--episode INSTEAD OF ONE LONG QUERY: sending a long,
highly-specific search query (e.g. "Agent Kim Reactivated 2026 S01 E01")
directly to sub.lk's search API intermittently triggers a redirect loop
on their end (site-side bug). Searching just the SERIES NAME (short,
reliable) and then matching the exact episode from the results list
locally avoids that entirely.

NOTE ON RETRIES: sub.lk's search backend is intermittently unstable -
the exact same request can fail with a redirect loop one moment and
succeed the next. MAX_RETRIES is set high with a moderate delay so the
script rides out these transient windows instead of giving up early.

USAGE:
    python scripts/fetch_sublk_subs.py --series "Agent Kim Reactivated 2026" --episode E01 --out subs_raw.srt
    python scripts/fetch_sublk_subs.py --series "Agent Kim Reactivated 2026" --episode E01 --gist

Requires: pip install requests
Gist mode requires env var GITHUB_TOKEN (a PAT with "gist" scope).
"""

import argparse
import os
import re
import sys
import time

import requests

WORKER_BASE_URL = "https://sublk-finder.banujadewmina.workers.dev"
MAX_RETRIES = 12
RETRY_DELAY_SECONDS = 10


def fetch_srt_via_worker(series: str, episode: str):
    """Calls our Cloudflare Worker's /get-srt-episode endpoint: does a
    broad series search + matches the episode locally (avoids sub.lk's
    intermittent redirect-loop bug on long/specific search queries).
    Retries many times since sub.lk's origin occasionally returns
    transient errors (525, 502, 503, redirect loops) that usually
    succeed on a later attempt - this is a site-side instability, not
    something fixable by changing the request itself."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                f"{WORKER_BASE_URL}/get-srt-episode",
                params={"series": series, "episode": episode},
                timeout=60,
            )
        except requests.RequestException as e:
            last_error = str(e)
            print(f"Attempt {attempt}/{MAX_RETRIES} failed (network error): {e}", file=sys.stderr)
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        if resp.status_code == 200:
            data = resp.json()
            print(f"Matched: {data['matchedTitle']} (source: {data.get('source', '?')})", file=sys.stderr)
            print(f"Page: {data['pageUrl']}", file=sys.stderr)
            return data["matchedTitle"], data["srtContent"]

        try:
            body = resp.json()
            err = body.get("error", resp.text)
            available = body.get("availableTitles")
        except Exception:
            err = resp.text
            available = None
        last_error = f"{resp.status_code}: {err}"

        if resp.status_code in (404, 400):
            print(f"ERROR: Worker returned {last_error}", file=sys.stderr)
            if available:
                print("Available titles for this series:", file=sys.stderr)
                for t in available:
                    print(f"  - {t}", file=sys.stderr)
            sys.exit(1)

        print(f"Attempt {attempt}/{MAX_RETRIES} failed: {last_error}", file=sys.stderr)
        if attempt < MAX_RETRIES:
            print(f"Retrying in {RETRY_DELAY_SECONDS}s...", file=sys.stderr)
            time.sleep(RETRY_DELAY_SECONDS)

    print(f"ERROR: All {MAX_RETRIES} attempts failed. Last error: {last_error}", file=sys.stderr)
    sys.exit(1)


def publish_gist(srt_content: str, filename: str = "subs.srt") -> str:
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
    parser.add_argument("--series", required=True, help="Series/movie name, e.g. 'Agent Kim Reactivated 2026'")
    parser.add_argument("--episode", required=True, help="Episode marker, e.g. 'E01' or 'S01 E01'")
    parser.add_argument("--out", help="Save raw .srt content to this local file path")
    parser.add_argument("--gist", action="store_true", help="Publish to a GitHub Gist and print the raw URL")
    args = parser.parse_args()

    title, srt_content = fetch_srt_via_worker(args.series, args.episode)

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