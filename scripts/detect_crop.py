#!/usr/bin/env python3
"""
detect_crop.py - Robust black-bar auto-detection using direct pixel analysis
(NOT ffmpeg's cropdetect filter, which is unreliable on files with broken
keyframe indexes, and whose fixed brightness `limit` fails on bars that
aren't pure (0,0,0) black).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

from PIL import Image


def ffprobe_dims_and_duration(video_path):
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "json", video_path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    w = data["streams"][0]["width"]
    h = data["streams"][0]["height"]
    duration = float(data["format"]["duration"])
    return w, h, duration


def row_is_bar(img_px, y, width, threshold, skip_ranges):
    max_val = 0
    step = max(1, width // 200)
    for x in range(0, width, step):
        skip = False
        for (a, b) in skip_ranges:
            if a <= x <= b:
                skip = True
                break
        if skip:
            continue
        r, g, b = img_px[x, y][:3]
        v = max(r, g, b)
        if v > max_val:
            max_val = v
    return max_val <= threshold


def detect_bars_for_frame(path, threshold):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()

    bottom_skip = [(int(w * 0.15), int(w * 0.85)), (int(w * 0.85), w)]
    top_skip = []

    top = 0
    for y in range(0, h // 2):
        if row_is_bar(px, y, w, threshold, top_skip):
            top = y + 1
        else:
            break

    bottom_margin = 0
    for y in range(h - 1, h // 2, -1):
        if row_is_bar(px, y, w, threshold, bottom_skip):
            bottom_margin = h - y
        else:
            break

    scan_rows = range(top + 10, h - bottom_margin - 10, max(1, (h // 40)))

    def col_is_bar(x):
        hits, checked = 0, 0
        for y in scan_rows:
            checked += 1
            r, g, b = px[x, y][:3]
            if max(r, g, b) <= threshold:
                hits += 1
        return checked > 0 and hits == checked

    left = 0
    for x in range(0, w // 2):
        if col_is_bar(x):
            left = x + 1
        else:
            break

    right_margin = 0
    for x in range(w - 1, w // 2, -1):
        if col_is_bar(x):
            right_margin = w - x
        else:
            break

    return top, bottom_margin, left, right_margin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--threshold", type=int, default=24)
    ap.add_argument("--samples", type=int, default=24)
    ap.add_argument("--apply", metavar="OUTPUT.mp4")
    args = ap.parse_args()

    w, h, duration = ffprobe_dims_and_duration(args.video)
    print(f"Source: {w}x{h}, duration {duration:.1f}s", file=sys.stderr)

    interval = max(duration / args.samples, 0.1)

    with tempfile.TemporaryDirectory() as td:
        pattern = os.path.join(td, "f_%04d.png")
        cmd = [
            "ffmpeg", "-y", "-i", args.video,
            "-vf", f"fps=1/{interval:.3f}",
            "-vsync", "0",
            pattern,
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        frames = sorted(
            os.path.join(td, f) for f in os.listdir(td) if f.endswith(".png")
        )
        if not frames:
            print("ERROR: no frames extracted", file=sys.stderr)
            sys.exit(1)

        print(f"Sampled {len(frames)} frames, analyzing with threshold={args.threshold}...",
              file=sys.stderr)

        tops, bottoms, lefts, rights = [], [], [], []
        for f in frames:
            t, b, l, r = detect_bars_for_frame(f, args.threshold)
            tops.append(t)
            bottoms.append(b)
            lefts.append(l)
            rights.append(r)

    final_top = max(tops)
    final_bottom = max(bottoms)
    final_left = max(lefts)
    final_right = max(rights)

    print(f"Detected -> top:{final_top} bottom:{final_bottom} "
          f"left:{final_left} right:{final_right}", file=sys.stderr)

    if final_top + final_bottom > h * 0.4:
        print(f"WARNING: vertical crop ({final_top}+{final_bottom}) exceeds 40% "
              f"of height ({h}) - refusing to auto-apply. Check --threshold.",
              file=sys.stderr)
        sys.exit(2)
    if final_left + final_right > w * 0.4:
        print(f"WARNING: horizontal crop ({final_left}+{final_right}) exceeds 40% "
              f"of width ({w}) - refusing to auto-apply. Check --threshold.",
              file=sys.stderr)
        sys.exit(2)

    new_w = w - final_left - final_right
    new_h = h - final_top - final_bottom
    if new_w % 2: new_w -= 1
    if new_h % 2: new_h -= 1

    crop_str = f"crop={new_w}:{new_h}:{final_left}:{final_top}"
    print(crop_str)

    if args.apply:
        out_cmd = [
            "ffmpeg", "-y", "-i", args.video,
            "-vf", crop_str,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "copy",
            args.apply,
        ]
        subprocess.run(out_cmd, check=True)
        print(f"Cropped video written to {args.apply}", file=sys.stderr)


if __name__ == "__main__":
    main()
