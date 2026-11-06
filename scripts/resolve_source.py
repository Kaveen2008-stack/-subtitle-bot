#!/usr/bin/env python3
"""
resolve_source.py - download a video (or archive) from many kinds of links.

Usage:
    python scripts/resolve_source.py <url> <out_path> [--kind video|archive]
                                     [--min-mb 1] [--attempts 3]

Supported sources:
    * Pixeldrain   (pixeldrain.com/u/ID  -> /api/file/ID, API key optional)
    * Google Drive (file/d/ID, open?id=, uc?id=)  via gdown
    * Mega         (mega.nz/file/..., mega.nz/#!...)  via megatools
    * MediaFire    (scrapes the real download link)
    * Dropbox      (forces dl=1)
    * Anything else that is a plain direct link (curl)

Optional environment variables:
    PIXELDRAIN_API_KEY   used for pixeldrain downloads (higher limits)
    MEGA_EMAIL / MEGA_PASSWORD   log in to Mega (avoids the anonymous
                                 transfer quota on shared GitHub IPs)

On failure the script exits non-zero and writes a short machine-readable
reason code into `download_error.txt` (e.g. DRIVE_QUOTA, DRIVE_PRIVATE,
MEGA_QUOTA, NOT_FOUND, HTML_PAGE, TOO_SMALL, INVALID_VIDEO, UNKNOWN) so the
workflow can report it to the dashboard / Telegram.
"""
import argparse
import base64
import os
import random
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse, parse_qs

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class DownloadError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# ---------------------------------------------------------------- helpers
def log(msg):
    print(msg, file=sys.stderr, flush=True)


def run(cmd, timeout=None):
    """Run a command, return (returncode, combined_output)."""
    log("$ " + " ".join(redact(c) for c in cmd))
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors="replace", timeout=timeout)
    out = p.stdout or ""
    if out.strip():
        log(out[-3000:])
    return p.returncode, out


def redact(s):
    # never print secrets in the Actions log
    for name in ("MEGA_PASSWORD", "PIXELDRAIN_API_KEY"):
        v = os.environ.get(name)
        if v:
            s = s.replace(v, "***")
    return s


def detect_source(url):
    host = (urlparse(url).netloc or "").lower()
    if "pixeldrain" in host:
        return "pixeldrain"
    if "drive.google.com" in host or "docs.google.com" in host \
            or "drive.usercontent.google.com" in host:
        return "drive"
    if host.endswith("mega.nz") or host.endswith("mega.io") or host.endswith("mega.co.nz"):
        return "mega"
    if "mediafire.com" in host:
        return "mediafire"
    if "dropbox.com" in host:
        return "dropbox"
    return "direct"


def drive_file_id(url):
    m = re.search(r"/file/d/([A-Za-z0-9_-]+)", url) \
        or re.search(r"/folders/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    q = parse_qs(urlparse(url).query)
    if "id" in q:
        return q["id"][0]
    return None


def pixeldrain_id(url):
    m = re.search(r"pixeldrain\.[a-z]+/(?:u|api/file)/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


# ------------------------------------------------------------ downloaders
def curl_download(url, out_path, extra_headers=None, referer=None):
    cmd = ["curl", "-fL", "-A", UA, "--connect-timeout", "30",
           "--retry", "2", "--retry-delay", "5", "-o", out_path]
    if referer:
        cmd += ["-e", referer]
    for h in extra_headers or []:
        cmd += ["-H", h]
    cmd.append(url)
    rc, out = run(cmd)
    if rc != 0:
        low = out.lower()
        if "404" in low:
            raise DownloadError("NOT_FOUND", "link returned 404 (file deleted?)")
        if "403" in low or "401" in low:
            raise DownloadError("FORBIDDEN", "link returned 401/403 (private or blocked)")
        if "429" in low:
            raise DownloadError("RATE_LIMITED", "429 too many requests")
        raise DownloadError("UNKNOWN", f"curl failed (exit {rc})")


def dl_pixeldrain(url, out_path):
    fid = pixeldrain_id(url)
    if not fid:
        raise DownloadError("NOT_FOUND", "could not read pixeldrain file id")
    headers = []
    key = os.environ.get("PIXELDRAIN_API_KEY", "")
    if key:
        auth = base64.b64encode(f":{key}".encode()).decode()
        headers.append(f"Authorization: Basic {auth}")
    curl_download(f"https://pixeldrain.com/api/file/{fid}", out_path,
                  extra_headers=headers, referer="https://pixeldrain.com/")


def dl_drive(url, out_path):
    fid = drive_file_id(url)
    if not fid:
        raise DownloadError("DRIVE_PRIVATE", "could not read Google Drive file id")
    if not shutil.which("gdown"):
        run([sys.executable, "-m", "pip", "install", "--quiet", "gdown"])
    rc, out = run(["gdown", "--id", fid, "-O", out_path, "--continue"])
    low = out.lower()
    if rc == 0 and os.path.exists(out_path):
        return
    if "too many users" in low or "quota" in low or "cannot retrieve the public link" in low:
        raise DownloadError("DRIVE_QUOTA",
                            "Google Drive download quota exceeded for this file "
                            "(try again later, or make a copy in your own Drive)")
    if "permission" in low or "access denied" in low or "sign in" in low \
            or "private" in low or "you need access" in low:
        raise DownloadError("DRIVE_PRIVATE",
                            "Drive file is not public ('Anyone with the link')")
    if "404" in low or "not found" in low:
        raise DownloadError("NOT_FOUND", "Drive file not found")
    raise DownloadError("UNKNOWN", "gdown failed")


def dl_mega(url, out_path):
    if not shutil.which("megatools"):
        run(["sudo", "apt-get", "install", "-y", "-qq", "megatools"])
    if not shutil.which("megatools"):
        raise DownloadError("UNKNOWN", "megatools is not installed")
    tmp_dir = out_path + ".mega_tmp"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir)
    cmd = ["megatools", "dl", "--path", tmp_dir]
    email, pw = os.environ.get("MEGA_EMAIL"), os.environ.get("MEGA_PASSWORD")
    if email and pw:
        cmd += ["--username", email, "--password", pw]
    cmd.append(url)
    rc, out = run(cmd)
    low = out.lower()
    files = [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir)
             if not f.endswith(".megatmp") and not f.startswith(".megatmp")]
    if rc == 0 and files:
        biggest = max(files, key=os.path.getsize)
        shutil.move(biggest, out_path)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return
    shutil.rmtree(tmp_dir, ignore_errors=True)
    if "bandwidth" in low or "quota" in low or "over quota" in low or "509" in low:
        raise DownloadError("MEGA_QUOTA",
                            "Mega transfer quota exceeded (set MEGA_EMAIL/MEGA_PASSWORD "
                            "secrets or retry in a few hours)")
    if "not found" in low or "enoent" in low or "removed" in low or "terms of service" in low:
        raise DownloadError("NOT_FOUND", "Mega link not found / removed")
    raise DownloadError("UNKNOWN", "megatools failed")


def dl_mediafire(url, out_path):
    page = out_path + ".page.html"
    curl_download(url, page)
    try:
        html = open(page, "r", errors="replace").read()
    finally:
        try:
            os.remove(page)
        except OSError:
            pass
    m = re.search(r'https?://download[0-9]*\.mediafire\.com/[^"\'\s<>]+', html)
    if not m:
        raise DownloadError("NOT_FOUND", "could not find the MediaFire direct link")
    curl_download(m.group(0), out_path)


def dl_dropbox(url, out_path):
    url = re.sub(r"[?&]dl=0", "", url)
    url += ("&" if "?" in url else "?") + "dl=1"
    curl_download(url, out_path)


def dl_direct(url, out_path):
    curl_download(url, out_path)


DOWNLOADERS = {
    "pixeldrain": dl_pixeldrain,
    "drive": dl_drive,
    "mega": dl_mega,
    "mediafire": dl_mediafire,
    "dropbox": dl_dropbox,
    "direct": dl_direct,
}


# ------------------------------------------------------------- validation
def validate(path, kind, min_mb):
    if not os.path.exists(path):
        raise DownloadError("UNKNOWN", "output file missing after download")
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(1024).lower()
    if b"<html" in head or b"<!doctype html" in head:
        raise DownloadError("HTML_PAGE",
                            "got an HTML page instead of the file "
                            "(link is private, a share page, or needs login)")
    if size < min_mb * 1024 * 1024:
        raise DownloadError("TOO_SMALL", f"file is only {size} bytes")
    if kind == "video" and shutil.which("ffprobe"):
        rc, out = run(["ffprobe", "-v", "error", "-show_entries",
                       "format=duration", "-of", "csv=p=0", path])
        try:
            dur = float(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            dur = 0.0
        if rc != 0 or dur <= 1:
            raise DownloadError("INVALID_VIDEO", "downloaded file is not a valid video")


# ------------------------------------------------------------------- main
def write_error(code):
    try:
        with open("download_error.txt", "w") as f:
            f.write(code)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out_path")
    ap.add_argument("--kind", choices=["video", "archive"], default="video")
    ap.add_argument("--min-mb", type=float, default=1.0)
    ap.add_argument("--attempts", type=int, default=3)
    args = ap.parse_args()

    url = args.url.strip()
    source = detect_source(url)
    log(f"Source detected: {source}")
    fn = DOWNLOADERS[source]

    # permanent problems are not worth retrying
    permanent = {"NOT_FOUND", "DRIVE_PRIVATE", "FORBIDDEN", "HTML_PAGE"}
    last = None
    for attempt in range(1, args.attempts + 1):
        try:
            if os.path.exists(args.out_path):
                os.remove(args.out_path)
            fn(url, args.out_path)
            validate(args.out_path, args.kind, args.min_mb)
            size_mb = os.path.getsize(args.out_path) / 1024 / 1024
            log(f"OK: {source} -> {args.out_path} ({size_mb:.1f} MB)")
            return 0
        except DownloadError as e:
            last = e
            log(f"Attempt {attempt}/{args.attempts} failed: [{e.code}] {e.message}")
            if e.code in permanent or attempt == args.attempts:
                break
            wait = min(30 * attempt, 120) + random.uniform(0, 10)
            log(f"Waiting {wait:.0f}s before retry...")
            time.sleep(wait)
        except subprocess.TimeoutExpired:
            last = DownloadError("UNKNOWN", "download timed out")

    code = last.code if last else "UNKNOWN"
    # prefix with the source so the dashboard shows e.g. DRIVE_QUOTA / MEGA_QUOTA
    write_error(code)
    print(f"::error::Download failed ({source}): [{code}] {last.message if last else ''}")
    if os.path.exists(args.out_path):
        os.remove(args.out_path)
    return 1


if __name__ == "__main__":
    sys.exit(main())
