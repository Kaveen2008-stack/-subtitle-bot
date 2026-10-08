"""
One-time backfill: attach ALREADY-uploaded Internet Archive videos to the site.

For every item named  onlyksub-<drama>_s<season>e<episode>_<quality>  on archive.org
it finds the matching episode row in Supabase and creates the "Server 2" (direct mp4)
row in episode_servers, exactly like the webhook does for new burns.

Run on your own PC:
  pip install requests
  set SUPABASE_URL=https://xxxx.supabase.co
  set SUPABASE_SERVICE_ROLE_KEY=...
  set TMDB_API_KEY=...
  python backfill_archive_servers.py            (dry run - changes nothing, prints the plan)
  python backfill_archive_servers.py --apply    (really writes to Supabase)

Optional: --map manual_map.json   ({"drama_slug_from_archive": tmdb_id, ...})
          for dramas whose Archive name could not be matched to a TMDB name automatically.
"""
import json
import os
import re
import sys
import time
from urllib.parse import quote

import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
TMDB_KEY = os.environ["TMDB_API_KEY"]
APPLY = "--apply" in sys.argv
MAP_FILE = sys.argv[sys.argv.index("--map") + 1] if "--map" in sys.argv else None

SB = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
ITEM_RE = re.compile(r"^onlyksub-(?P<slug>.+)_s(?P<s>\d+)e(?P<e>\d+)_(?P<q>\d{3,4}p)$")


def slugify(name):
    # same rule as the workflow: spaces -> _, keep only A-Za-z0-9_-, then lowercase
    return re.sub(r"[^A-Za-z0-9_-]", "", name.replace(" ", "_")).lower()


def sb_get_all(table, select, extra=""):
    rows, offset = [], 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}?select={select}&limit=1000&offset={offset}{extra}", headers=SB, timeout=60
        )
        r.raise_for_status()
        part = r.json()
        rows += part
        if len(part) < 1000:
            return rows
        offset += 1000


def archive_item_ids():
    q = 'creator:"OnlyKSub" AND mediatype:movies'
    r = requests.get(
        "https://archive.org/advancedsearch.php",
        params={"q": q, "fl[]": "identifier", "rows": 10000, "output": "json"},
        timeout=60,
    )
    r.raise_for_status()
    ids = [d["identifier"] for d in r.json()["response"]["docs"]]
    return sorted(i for i in ids if i.startswith("onlyksub-"))


def original_mp4_name(identifier):
    r = requests.get(f"https://archive.org/metadata/{identifier}", timeout=60)
    r.raise_for_status()
    files = r.json().get("files", [])
    for f in files:
        if f.get("source") == "original" and f.get("name", "").lower().endswith(".mp4"):
            return f["name"]
    for f in files:
        n = f.get("name", "")
        if n.lower().endswith(".mp4") and ".ia." not in n.lower():
            return n
    return None


def main():
    print("Loading episodes from Supabase...")
    episodes = sb_get_all("episodes", "id,tmdb_id,season_number,episode_number,quality")
    ep_index = {(str(e["tmdb_id"]), e["season_number"], e["episode_number"], e["quality"]): e["id"] for e in episodes}

    print("Building drama name -> tmdb_id map from TMDB...")
    slug_to_tmdb = {}
    for tid in sorted({str(e["tmdb_id"]) for e in episodes}):
        r = requests.get(f"https://api.themoviedb.org/3/tv/{tid}", params={"api_key": TMDB_KEY, "language": "en-US"}, timeout=30)
        if r.ok:
            d = r.json()
            for n in {d.get("name"), d.get("original_name")}:
                if n:
                    slug_to_tmdb.setdefault(slugify(n), tid)
        time.sleep(0.05)
    if MAP_FILE:
        slug_to_tmdb.update({k: str(v) for k, v in json.load(open(MAP_FILE)).items()})

    print("Listing Internet Archive items...")
    ids = archive_item_ids()
    print(f"  {len(ids)} items found\n")

    plan, unmatched, no_episode = [], set(), []
    for ident in ids:
        m = ITEM_RE.match(ident)
        if not m:
            continue
        tid = slug_to_tmdb.get(m["slug"])
        if not tid:
            unmatched.add(m["slug"])
            continue
        key = (tid, int(m["s"]), int(m["e"]), m["q"])
        ep_id = ep_index.get(key)
        if not ep_id:
            no_episode.append(ident)
            continue
        plan.append((ident, ep_id, key))

    print(f"Will add Server 2 to {len(plan)} episode rows.")
    if unmatched:
        print("\nCould NOT match these Archive names to a TMDB drama (put them in --map file):")
        for s in sorted(unmatched):
            print("  ", s)
    if no_episode:
        print(f"\n{len(no_episode)} Archive items have no matching episode row in Supabase (skipped):")
        for i in no_episode[:30]:
            print("  ", i)

    if not APPLY:
        print("\nDRY RUN - nothing written. Re-run with --apply to write.")
        return

    done = failed = 0
    for ident, ep_id, key in plan:
        try:
            name = original_mp4_name(ident)
            if not name:
                print("  no mp4 in item:", ident)
                failed += 1
                continue
            url = f"https://archive.org/download/{ident}/{quote(name)}"
            requests.delete(
                f"{SUPABASE_URL}/rest/v1/episode_servers?episode_id=eq.{ep_id}&server_name=in.(Server 2,Internet Archive)",
                headers=SB, timeout=30,
            ).raise_for_status()
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/episode_servers",
                headers={**SB, "Prefer": "return=minimal"},
                json={"episode_id": ep_id, "server_name": "Server 2", "stream_url": url, "server_type": "direct", "priority": 1},
                timeout=30,
            )
            r.raise_for_status()
            done += 1
            print("  +", ident)
            time.sleep(0.2)
        except Exception as e:
            failed += 1
            print("  FAILED", ident, e)
    print(f"\nDone: {done} added, {failed} failed.")


if __name__ == "__main__":
    main()
