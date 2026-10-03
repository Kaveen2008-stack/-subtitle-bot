"""
Uploads a file to Pixeldrain.
Usage: python upload_pixeldrain.py <file_path>
Optional: PIXELDRAIN_API_KEY environment variable (uploads to your account
instead of anonymously - anonymous uploads may be auto-deleted after a while).

Retries: many matrix legs (and several series) upload at the same time, so
Pixeldrain sometimes answers 429 / 5xx or drops the connection mid-upload.
Those are temporary, so we retry with a growing, randomised wait instead of
failing the whole leg. Permanent errors (bad API key, file too big, ...) are
NOT retried. The real error message is always printed so it shows in the log.
"""
import os
import sys
import time
import random
import base64
import requests

BASE_URL = os.environ.get("PIXELDRAIN_BASE_URL", "https://pixeldrain.com").rstrip("/")
MAX_ATTEMPTS = int(os.environ.get("PIXELDRAIN_MAX_ATTEMPTS", "6"))
WAIT_SCALE = float(os.environ.get("PIXELDRAIN_WAIT_SCALE", "1"))  # tests set this small
BASE_WAITS = [20, 45, 90, 180, 300]  # seconds, before attempt 2, 3, 4, ...
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def wait_before_retry(attempt, retry_after=None):
    base = BASE_WAITS[min(attempt - 1, len(BASE_WAITS) - 1)]
    if retry_after:
        base = max(base, min(retry_after, 600))
    wait = (base + random.uniform(0, 15)) * WAIT_SCALE
    print(f"  waiting {wait:.0f}s before retry...", file=sys.stderr)
    time.sleep(wait)


def parse_retry_after(resp):
    try:
        return int(resp.headers.get("Retry-After", ""))
    except ValueError:
        return None


def main():
    file_path = sys.argv[1]
    api_key = os.environ.get("PIXELDRAIN_API_KEY", "")

    if not api_key:
        print(
            "WARNING: PIXELDRAIN_API_KEY is not set - uploading anonymously. "
            "Anonymous Pixeldrain files get auto-deleted after a while, which "
            "is why old download links eventually stop working. Set "
            "PIXELDRAIN_API_KEY as a GitHub Actions secret to fix this.",
            file=sys.stderr,
        )

    url = f"{BASE_URL}/api/file/{os.path.basename(file_path)}"
    headers = {}
    if api_key:
        auth = base64.b64encode(f":{api_key}".encode()).decode()
        headers["Authorization"] = f"Basic {auth}"

    size_mb = os.path.getsize(file_path) / 1048576
    data = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"Pixeldrain upload attempt {attempt}/{MAX_ATTEMPTS} ({size_mb:.0f} MB)", file=sys.stderr)
        try:
            # File must be re-opened for every attempt (the stream is consumed).
            with open(file_path, "rb") as f:
                resp = requests.put(url, data=f, headers=headers, timeout=1800)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            print(f"  network error: {e!r}", file=sys.stderr)
            if attempt == MAX_ATTEMPTS:
                sys.exit(1)
            wait_before_retry(attempt)
            continue

        if resp.ok:
            try:
                data = resp.json()
            except ValueError:
                print(f"  200 but response is not JSON: {resp.text[:300]}", file=sys.stderr)
                if attempt == MAX_ATTEMPTS:
                    sys.exit(1)
                wait_before_retry(attempt)
                continue
            break

        # Not OK: always show what Pixeldrain actually said.
        print(f"  HTTP {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
        if resp.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
            wait_before_retry(attempt, parse_retry_after(resp))
            continue
        sys.exit(1)  # permanent error (bad key, too large, ...) or out of attempts

    if not data.get("success", True) and "id" not in data:
        print(f"Pixeldrain upload failed: {data}", file=sys.stderr)
        sys.exit(1)

    file_id = data["id"]
    # Direct-download API endpoint (?download) instead of the HTML viewer
    # page https://pixeldrain.com/u/{id} - so the link actually downloads
    # the file instead of just opening Pixeldrain's page.
    link = f"https://pixeldrain.com/api/file/{file_id}?download"

    with open("pixeldrain_link.txt", "w") as f:
        f.write(link)

    print(link)


if __name__ == "__main__":
    main()
