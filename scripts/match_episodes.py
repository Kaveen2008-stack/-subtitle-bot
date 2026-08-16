"""
Matches video files to subtitle files by SxxExx (or ExNN) episode number
found in their filenames. Used by burn_season.yml as the first step of the
season batch pipeline.

Usage:
    python match_episodes.py <videos_dir> <srts_dir> <output.json>
"""
import os
import re
import sys
import json

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".webm", ".m4v")
SRT_EXTS = (".srt",)

# Matches "S01E08", "S1E8", "s01.e08" etc. Falls back to bare "E08" / "Episode 08".
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


def collect_files(root_dir, extensions):
    found = {}
    for dirpath, _, files in os.walk(root_dir):
        for f in files:
            if f.lower().endswith(extensions):
                ep = find_episode_number(f)
                if ep is not None:
                    found[ep] = os.path.join(dirpath, f)
    return found


def main():
    videos_dir, srts_dir, output_path = sys.argv[1], sys.argv[2], sys.argv[3]

    videos = collect_files(videos_dir, VIDEO_EXTS)
    srts = collect_files(srts_dir, SRT_EXTS)

    print(f"Found {len(videos)} video(s), {len(srts)} subtitle(s).", file=sys.stderr)

    matched = []
    unmatched_videos = []
    for ep_num, video_path in sorted(videos.items()):
        if ep_num in srts:
            matched.append({
                "episode_number": ep_num,
                "video_path": video_path,
                "srt_path": srts[ep_num],
            })
        else:
            unmatched_videos.append((ep_num, video_path))

    if unmatched_videos:
        print(f"WARNING: {len(unmatched_videos)} video(s) had no matching subtitle "
              f"and will be SKIPPED:", file=sys.stderr)
        for ep_num, path in unmatched_videos:
            print(f"  Episode {ep_num}: {path}", file=sys.stderr)

    if not matched:
        print("ERROR: No video/subtitle pairs matched. Check filenames contain "
              "a recognizable episode number (e.g. S01E08).", file=sys.stderr)
        sys.exit(1)

    with open(output_path, "w") as f:
        json.dump(matched, f, indent=2)

    print(f"Matched {len(matched)} episode(s) -> {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()