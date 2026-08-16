"""
Uploads each matched episode's raw video to Pixeldrain (to get a stable
direct-download URL usable by the later matrix "burn" job, since each
matrix job runs on a separate runner and can't share local disk), and
writes out the final episode plan JSON (a GitHub Actions matrix array).

Usage:
    python plan_season_uploads.py matched_episodes.json episode_plan.json
Requires PIXELDRAIN_API_KEY in the environment.
"""
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def upload_to_pixeldrain(video_path):
    """Calls the existing upload_pixeldrain.py as a subprocess (same way
    burn.yml already does), then reads the link it writes out."""
    link_file = "pixeldrain_link.txt"
    if os.path.exists(link_file):
        os.remove(link_file)
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, "upload_pixeldrain.py"), video_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not os.path.exists(link_file):
        raise RuntimeError(f"upload_pixeldrain.py failed: {result.stderr.strip()}")
    with open(link_file) as f:
        return f.read().strip()


def main():
    input_path, output_path = sys.argv[1], sys.argv[2]

    with open(input_path) as f:
        matched = json.load(f)

    plan = []
    for ep in matched:
        ep_num = ep["episode_number"]
        video_path = ep["video_path"]
        print(f"Uploading episode {ep_num} raw video to Pixeldrain...", file=sys.stderr)

        try:
            link = upload_to_pixeldrain(video_path)
        except Exception as e:
            print(f"  WARNING: upload failed for episode {ep_num}: {e} - SKIPPING", file=sys.stderr)
            continue

        plan.append({
            "episode_number": ep_num,
            "video_url": link,
        })
        print(f"  Episode {ep_num} -> {link}", file=sys.stderr)

    if not plan:
        print("ERROR: No episodes were successfully uploaded.", file=sys.stderr)
        sys.exit(1)

    with open(output_path, "w") as f:
        json.dump(plan, f)

    print(f"Planned {len(plan)} episode(s) -> {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()