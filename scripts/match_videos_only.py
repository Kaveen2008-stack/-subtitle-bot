"""
Matches video files inside an extracted season archive against a list of
episode numbers that the caller already has subtitles for (subtitles are
supplied individually per-episode by the dashboard, not as a zip).

Usage:
    python match_videos_only.py <videos_dir> <episode_numbers_csv> <output.json>
    (episode_numbers_csv example: "1,2,3,4,5")
"""
import os
import re
import sys
import json

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".webm", ".m4v")

EP_PATTERNS = [
    re.compile(r"[Ss]\d{1,2}[._\- ]?[Ee](\d{1,3})"),
    re.compile(r"[Ee]pisode[._\- ]?(\d{1,3})", re.IGNORECASE),
    re.compile(r"[._\- ][Ee](\d{1,3})[._\- ]"),
]


def find_episode_number(filename):
    for pattern in EP_PATTERNS:
        m = pattern.search(filename)
        if m:
            return int(m.group(1))
    return None


def collect_videos(root_dir):
    found = {}
    for dirpath, _, files in os.walk(root_dir):
        for f in files:
            if f.lower().endswith(VIDEO_EXTS):
                ep = find_episode_number(f)
                if ep is not None:
                    found[ep] = os.path.join(dirpath, f)
    return found


def main():
    videos_dir, ep_csv, output_path = sys.argv[1], sys.argv[2], sys.argv[3]
    wanted_episodes = [int(x) for x in ep_csv.split(",") if x.strip()]

    videos = collect_videos(videos_dir)
    print(f"Found {len(videos)} video file(s) in archive.", file=sys.stderr)

    matched, missing = [], []
    for ep in wanted_episodes:
        if ep in videos:
            matched.append({"episode_number": ep, "video_path": videos[ep]})
        else:
            missing.append(ep)

    print(f"\n=== MATCH REPORT ===", file=sys.stderr)
    print(f"Requested episodes: {wanted_episodes}", file=sys.stderr)
    print(f"Matched ({len(matched)}):", file=sys.stderr)
    for m in matched:
        print(f"  Episode {m['episode_number']} -> {os.path.basename(m['video_path'])}", file=sys.stderr)
    if missing:
        print(f"MISSING - no video found for episodes: {missing}", file=sys.stderr)
        print(f"(these will be SKIPPED - subtitle was provided but no matching video in the archive)",
              file=sys.stderr)

    if not matched:
        print("ERROR: No episodes matched any video in the archive. "
              "Check filenames contain a recognizable episode number (e.g. S01E08) "
              "and that the archive URL actually points to the right season.",
              file=sys.stderr)
        sys.exit(1)

    with open(output_path, "w") as f:
        json.dump(matched, f, indent=2)

    print(f"\nMatched {len(matched)}/{len(wanted_episodes)} episode(s) -> {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()