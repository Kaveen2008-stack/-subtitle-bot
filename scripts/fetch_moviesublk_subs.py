#!/usr/bin/env python3
"""
fetch_moviesublk_subs.py
-------------------------
Scrapes moviesublk.com (a Blogger-hosted Sinhala subtitle site) for a
series/movie's Mediafire .ass subtitle links, and optionally resolves
each Mediafire page to its real direct-download URL.

HOW IT WORKS
------------
1. SEARCH: moviesublk.com is a Blogger (blogspot engine) site. Blogger
   exposes a public JSON feed API that lets us search posts WITHOUT
   parsing any HTML:
       https://www.moviesublk.com/feeds/posts/default?q=<query>&alt=json
   This returns candidate post titles + URLs. Much more reliable than
   scraping the site's search page HTML.

2. EXTRACT: Each post page embeds a JS object called `seriesData` inside
   a <script> tag, e.g.:

       const seriesData = {
         1: {
           title: "Season 1",
           totalEpisodes: 12,
           episodes: {
             1: {
               v: "https://archive.org/.../file.mp4",
               v2: "https://drive.google.com/.../preview",
               gd: "https://drive.usercontent.google.com/download?...",
               tg: "https://t.me/Moviesublkfilebot?start=...",
               sb: "https://www.mediafire.com/file/xxxxx/NAME.ass/file",
             },
             ...
           }
         },
         ...
       };

   This is STATIC data embedded server-side (not fetched via AJAX), so a
   plain `requests.get()` + regex is enough - no headless browser/JS
   execution needed. We pull out the `seriesData = {...};` block with a
   regex, then convert the loose JS-object-literal syntax (unquoted
   numeric/bare keys, trailing commas) into valid JSON so it can be
   parsed with the standard `json` module.

3. RESOLVE (optional, --resolve-mediafire): Mediafire's file page embeds
   the real CDN download URL in an anchor tag:
       <a id="downloadButton" href="https://download....mediafire.com/...">
   We fetch that page and regex it out - again, no JS execution needed,
   Mediafire renders this server-side.

USAGE
-----
    # 1. Find candidate posts for a series name
    python fetch_moviesublk_subs.py search "Let's Get Divorced"

    # 2. Dump the mediafire .ass links (+ everything else) from a post
    python fetch_moviesublk_subs.py extract "https://www.moviesublk.com/2026/08/lets-get-divorced-s01-2026-sinhala.html"

    # 3. Also resolve each Mediafire page to its real direct-download URL
    python fetch_moviesublk_subs.py extract "<post_url>" --resolve-mediafire

    # 4. Download the actual .ass files to a local folder
    python fetch_moviesublk_subs.py extract "<post_url>" --resolve-mediafire --download-dir subs_out/

Requires: pip install requests
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.moviesublk.com/",
})

# Matches Blogger's search-page post links, e.g.:
#   <h3 class='post-title entry-title' ...><a href='https://www.moviesublk.com/2026/08/xxx.html'>Title</a></h3>
HTML_SEARCH_LINK_RE = re.compile(
    r'<a[^>]+href=["\'](https://www\.moviesublk\.com/\d{4}/\d{2}/[^"\']+\.html)["\'][^>]*>(.*?)</a>',
    re.DOTALL,
)

SERIES_DATA_RE = re.compile(r"const\s+seriesData\s*=\s*(\{.*?\});", re.DOTALL)
MEDIAFIRE_DOWNLOAD_RE = re.compile(
    r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']'
)
# Fallback pattern - some Mediafire page variants put the attrs in the
# opposite order (href before id), so match either.
MEDIAFIRE_DOWNLOAD_RE_ALT = re.compile(
    r'href=["\']([^"\']+)["\'][^>]*id=["\']downloadButton["\']'
)


# ---------------------------------------------------------------------
# STEP 1: search via Blogger's public JSON feed (no HTML scraping)
# ---------------------------------------------------------------------
def _search_via_feed(query: str, max_results: int):
    """Try Blogger's JSON feed API. Some Blogger sites block this with a
    403 (feed disabled / bot-protection), in which case the caller
    should fall back to _search_via_html_page()."""
    url = "https://www.moviesublk.com/feeds/posts/default"
    params = {"q": query, "alt": "json", "max-results": max_results}
    resp = SESSION.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    entries = data.get("feed", {}).get("entry", [])
    results = []
    for e in entries:
        title = e.get("title", {}).get("$t", "(untitled)")
        published = e.get("published", {}).get("$t", "")
        link = None
        for l in e.get("link", []):
            if l.get("rel") == "alternate":
                link = l.get("href")
                break
        if link:
            results.append({"title": title, "url": link, "published": published})
    return results


def _strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _search_via_html_page(query: str, max_results: int):
    """Fallback: scrape Blogger's own /search?q=... results page and
    regex out post links + titles. Used when the JSON feed is blocked."""
    url = "https://www.moviesublk.com/search"
    resp = SESSION.get(url, params={"q": query}, timeout=20)
    resp.raise_for_status()
    html = resp.text

    seen = set()
    results = []
    for m in HTML_SEARCH_LINK_RE.finditer(html):
        link, title_html = m.group(1), m.group(2)
        title = _strip_html_tags(title_html)
        if not title or link in seen:
            continue
        seen.add(link)
        results.append({"title": title, "url": link, "published": ""})
        if len(results) >= max_results:
            break
    return results


def search_posts(query: str, max_results: int = 8):
    """Search moviesublk.com posts by title. Tries the Blogger JSON feed
    API first (cleaner, no HTML parsing); if that's blocked (403/etc),
    falls back to scraping the site's own /search?q= results page."""
    try:
        results = _search_via_feed(query, max_results)
        if results:
            return results
        # Feed responded fine but had zero hits - still worth trying the
        # HTML search page since Blogger's own search sometimes matches
        # content that the feed's `q=` filter misses.
    except requests.exceptions.HTTPError as e:
        print(f"  (JSON feed search blocked: {e}; falling back to HTML search page)",
              file=sys.stderr)
    except Exception as e:
        print(f"  (JSON feed search failed: {e}; falling back to HTML search page)",
              file=sys.stderr)

    return _search_via_html_page(query, max_results)


# ---------------------------------------------------------------------
# STEP 2: extract the embedded seriesData JS object from a post page
# ---------------------------------------------------------------------
def _js_object_literal_to_json(js_text: str) -> str:
    """Best-effort conversion of a loose JS object literal (bare/numeric
    keys, single quotes, trailing commas) into strict JSON text."""
    text = js_text

    # Quote bare/numeric object keys:  1: {  ->  "1": {   |   v2: "..." -> "v2": "..."
    text = re.sub(r'(?<=[{,\s])([A-Za-z0-9_]+)\s*:', r'"\1":', text)

    # Convert single-quoted strings to double-quoted (simple cases only -
    # this site's data doesn't appear to use single quotes, but just in
    # case a future page does).
    text = re.sub(r":\s*'([^']*)'", lambda m: ': "%s"' % m.group(1).replace('"', '\\"'), text)

    # Remove trailing commas before a closing } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    return text


def extract_series_data(post_html: str) -> dict:
    """Pull the `seriesData` JS object out of a post's raw HTML and
    parse it into a Python dict: {season_num: {title, totalEpisodes,
    episodes: {ep_num: {v, v2, gd, tg, sb}}}}"""
    m = SERIES_DATA_RE.search(post_html)
    if not m:
        raise ValueError(
            "Couldn't find `const seriesData = {...};` in the page. "
            "The site's template may have changed, or this post uses a "
            "different structure (e.g. a single movie, not a series)."
        )
    raw_obj = m.group(1)
    json_text = _js_object_literal_to_json(raw_obj)
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Found seriesData but couldn't parse it as JSON after cleanup: {e}\n"
            f"--- cleaned text around error ---\n"
            f"{json_text[max(0, e.pos - 200):e.pos + 200]}"
        )


def fetch_post(url: str) -> str:
    resp = SESSION.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def collect_mediafire_links(series_data: dict):
    """Flatten seriesData into a list of
    {season, episode, mediafire_url, title} for every episode that has
    a non-empty `sb` (Sinhala sub / mediafire) field."""
    out = []
    for season_key, season in series_data.items():
        episodes = season.get("episodes", {})
        for ep_key, ep in episodes.items():
            mf = ep.get("sb")
            if mf and mf.strip() and mf.strip() != "#" and "VIDEO_LINK_HERE" not in mf:
                out.append({
                    "season": int(season_key),
                    "episode": int(ep_key),
                    "mediafire_url": mf.strip(),
                    "video_url": ep.get("v") or ep.get("v2") or ep.get("gd"),
                })
    out.sort(key=lambda x: (x["season"], x["episode"]))
    return out


# ---------------------------------------------------------------------
# STEP 3 (optional): resolve a Mediafire page to its real download URL
# ---------------------------------------------------------------------
def resolve_mediafire(mediafire_page_url: str) -> str:
    """Fetch a mediafire.com/file/... page and extract the real CDN
    direct-download URL from the #downloadButton anchor."""
    resp = SESSION.get(mediafire_page_url, timeout=30)
    resp.raise_for_status()
    html = resp.text

    m = MEDIAFIRE_DOWNLOAD_RE.search(html) or MEDIAFIRE_DOWNLOAD_RE_ALT.search(html)
    if not m:
        raise ValueError(
            f"Couldn't find #downloadButton href in {mediafire_page_url} "
            f"(page may have changed, been taken down, or needs a CAPTCHA)."
        )
    return m.group(1)


def download_file(url: str, dest_path: str):
    resp = SESSION.get(url, timeout=120, stream=True)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def cmd_search(args):
    results = search_posts(args.query, max_results=args.max_results)
    if not results:
        print("No matching posts found.", file=sys.stderr)
        sys.exit(1)
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['title']}")
        print(f"   {r['url']}")


def cmd_extract(args):
    print(f"Fetching post: {args.post_url}", file=sys.stderr)
    html = fetch_post(args.post_url)

    series_data = extract_series_data(html)
    links = collect_mediafire_links(series_data)

    if not links:
        print("No mediafire (Sinhala Sub) links found in this post.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(links)} episode(s) with mediafire subtitle links.", file=sys.stderr)

    if args.download_dir:
        os.makedirs(args.download_dir, exist_ok=True)

    for item in links:
        label = f"S{item['season']:02d}E{item['episode']:02d}"
        resolved = None

        if args.resolve_mediafire or args.download_dir:
            try:
                resolved = resolve_mediafire(item["mediafire_url"])
            except Exception as e:
                print(f"  {label}: FAILED to resolve mediafire link: {e}", file=sys.stderr)

        print(f"{label}\tmediafire_page={item['mediafire_url']}"
              + (f"\tdirect={resolved}" if resolved else ""))

        if args.download_dir and resolved:
            # Guess a filename from the mediafire page URL slug
            slug = urllib.parse.unquote(item["mediafire_url"].rstrip("/").split("/")[-2])
            ext = ".ass" if slug.lower().endswith(".ass") else ".srt"
            fname = f"{label}{ext}" if not slug.lower().endswith((".ass", ".srt")) else f"{label}_{slug}"
            dest = os.path.join(args.download_dir, fname)
            try:
                download_file(resolved, dest)
                print(f"  -> saved to {dest}", file=sys.stderr)
            except Exception as e:
                print(f"  -> FAILED to download: {e}", file=sys.stderr)


def _pick_best_match(results, query):
    """Very simple best-match picker: prefer a title that contains the
    full query (case-insensitive), else fall back to the first result
    (Blogger's own search relevance ranking)."""
    q = query.strip().lower()
    for r in results:
        if q in r["title"].lower():
            return r
    return results[0]


def cmd_auto(args):
    """One-shot: name -> search -> best match -> extract -> resolve -> download ALL.
    If --url is given, search is skipped entirely and that post is used directly
    (useful when moviesublk.com blocks search/feed requests from this network/IP
    but a direct page fetch still works)."""
    if args.url:
        chosen = {"title": args.query or args.url, "url": args.url}
        print(f"Using direct URL (search skipped): {chosen['url']}\n", file=sys.stderr)
    else:
        print(f"Searching moviesublk.com for: {args.query!r}", file=sys.stderr)
        results = search_posts(args.query, max_results=args.max_results)
        if not results:
            print("No matching posts found. Try a shorter/simpler name, or pass --url "
                  "with the post link directly if search is blocked on this network.",
                  file=sys.stderr)
            sys.exit(1)

        print(f"Found {len(results)} candidate post(s):", file=sys.stderr)
        for i, r in enumerate(results, 1):
            print(f"  {i}. {r['title']}", file=sys.stderr)

        if args.pick:
            idx = args.pick - 1
            if idx < 0 or idx >= len(results):
                print(f"--pick {args.pick} is out of range (1-{len(results)}).", file=sys.stderr)
                sys.exit(1)
            chosen = results[idx]
        else:
            chosen = _pick_best_match(results, args.query)

        print(f"\nUsing: {chosen['title']}\n  {chosen['url']}\n", file=sys.stderr)

    html = fetch_post(chosen["url"])
    series_data = extract_series_data(html)
    links = collect_mediafire_links(series_data)

    if not links:
        print("No mediafire (Sinhala Sub) links found on this post.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(links)} episode(s) with subtitle links. Downloading all...\n", file=sys.stderr)

    # Build a safe folder name from the post title
    safe_name = re.sub(r"[^\w\-]+", "_", chosen["title"]).strip("_")[:80]
    out_dir = args.download_dir or os.path.join("subs_out", safe_name)
    os.makedirs(out_dir, exist_ok=True)

    ok_count, fail_count = 0, 0
    for item in links:
        label = f"S{item['season']:02d}E{item['episode']:02d}"
        try:
            resolved = resolve_mediafire(item["mediafire_url"])
        except Exception as e:
            print(f"  {label}: FAILED to resolve mediafire link ({e})", file=sys.stderr)
            fail_count += 1
            continue

        slug = urllib.parse.unquote(item["mediafire_url"].rstrip("/").split("/")[-2])
        ext = os.path.splitext(slug)[1] or ".ass"
        fname = f"{label}{ext}"
        dest = os.path.join(out_dir, fname)

        try:
            download_file(resolved, dest)
            print(f"  {label}: OK -> {dest}", file=sys.stderr)
            ok_count += 1
        except Exception as e:
            print(f"  {label}: FAILED to download ({e})", file=sys.stderr)
            fail_count += 1

    print(f"\nDone. {ok_count} downloaded, {fail_count} failed. Folder: {out_dir}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="Search moviesublk.com for a series/movie by name")
    p_search.add_argument("query")
    p_search.add_argument("--max-results", type=int, default=8)
    p_search.set_defaults(func=cmd_search)

    p_extract = sub.add_parser("extract", help="Extract mediafire subtitle links from a post URL")
    p_extract.add_argument("post_url")
    p_extract.add_argument("--resolve-mediafire", action="store_true",
                            help="Also resolve each mediafire page to its real direct-download URL")
    p_extract.add_argument("--download-dir", default=None,
                            help="If set, download+save each resolved .ass/.srt file here")
    p_extract.set_defaults(func=cmd_extract)

    p_auto = sub.add_parser("auto", help="One-shot: give a name (or --url), get ALL episode subtitle files downloaded")
    p_auto.add_argument("query", nargs="?", default=None,
                         help="Movie/series name, e.g. \"Let's Get Divorced\" (omit if using --url)")
    p_auto.add_argument("--url", default=None,
                         help="Skip search entirely and use this post URL directly "
                              "(needed if search/feed requests are blocked from your network/IP)")
    p_auto.add_argument("--max-results", type=int, default=8,
                         help="How many search candidates to consider (default 8)")
    p_auto.add_argument("--pick", type=int, default=None,
                         help="1-based index of which search result to use, if the auto-pick guesses wrong "
                              "(run 'search' first to see the numbered list)")
    p_auto.add_argument("--download-dir", default=None,
                         help="Where to save files (default: subs_out/<series_name>/)")
    p_auto.set_defaults(func=cmd_auto)

    args = parser.parse_args()
    if args.cmd == "auto" and not args.query and not args.url:
        parser.error("auto requires either a query (series name) or --url")
    args.func(args)


if __name__ == "__main__":
    main()