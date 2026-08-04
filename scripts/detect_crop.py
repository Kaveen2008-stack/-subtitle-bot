#!/usr/bin/env python3
"""
detect_crop.py - Robust black-bar auto-detection using direct pixel analysis
(NOT ffmpeg's cropdetect filter, which is unreliable on files with broken
keyframe indexes, and whose fixed brightness `limit` fails on bars that
aren't pure (0,0,0) black).

Handles:
  - Bars of any thickness (thin or thick), independent per video.
  - Bars that are "near black" but not pure black (e.g. (18,20,22)).
  - Burnt-in subtitle text / watermark logos INSIDE the bar (which would
    normally fool a naive brightness scan) by sampling multiple safe
    x-columns per row and aggregating across many frames.

Usage:
    python detect_crop.py input.mp4 [--threshold 24] [--samples 24]

Output:
    Prints a single line:  crop=W:H:X:Y
    (matches ffmpeg's -vf crop=W:H:X:Y syntax directly)
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


def extract_sample_frames(video_path, n_samples, out_dir):
    """
    Extract evenly-spaced frames by DECODING SEQUENTIALLY (select filter),
    not by seeking (-ss). Seeking is unreliable on files with broken/sparse
    keyframe indexes (common with screen-recorded / re-muxed files) and can
    silently return the wrong or a corrupt frame.
    """
    pattern = os.path.join(out_dir, "f_%04d.png")
    # fps filter grabs 1 frame every (duration/n_samples) seconds, decoding
    # forward from the start - always reliable regardless of keyframes.
    vf = f"fps=1/SAMPLE_INTERVAL"
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf,
        "-vsync", "0",
        pattern,
    ]
    return cmd  # placeholder, built properly in main() once duration known


def row_is_bar(img_px, y, width, threshold, skip_ranges):
    """
    Check if row y is part of a black bar: max brightness across the row,
    EXCLUDING columns inside skip_ranges (e.g. where subtitle text / a
    watermark logo may sit), must be below threshold.
    """
    max_val = 0
    step = max(1, width // 200)  # sample ~200 points across the row, fast enough
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

    # Skip the middle-bottom (subtitle text) and bottom-right corner
    # (watermark logos) when scanning for the bar - a burnt-in white/bright
    # subtitle or logo inside a black bar would otherwise be mistaken for
    # "real video content" and stop the crop early.
    bottom_skip = [(int(w * 0.15), int(w * 0.85)), (int(w * 0.85), w)]
    top_skip = []  # top bars rarely have text; scan full width

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

    # Left/right (pillarbox) - scan sparse rows across the FULL height
    # (excluding the already-known top/bottom bars and the subtitle zone)
    # so a single dark patch of real video content can't fake a pillarbox.
    scan_rows = range(top + 10, h - bottom_margin - 10, max(1, (h // 40)))

    def col_is_bar(x):
        hits, checked = 0, 0
        for y in scan_rows:
            checked += 1
            r, g, b = px[x, y][:3]
            if max(r, g, b) <= threshold:
                hits += 1
        # Require the ENTIRE sampled column to be black, not just some rows -
        # a real pillarbox is black at every y; a dark scene patch is not.
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
    ap.add_argument("--threshold", type=int, default=24,
                     help="Max RGB brightness (0-255) still considered 'black'. "
                          "Raise this if bars are dark-gray, not pure black.")
    ap.add_argument("--samples", type=int, default=24,
                     help="Number of frames to sample across the video.")
    ap.add_argument("--apply", metavar="OUTPUT.mp4",
                     help="If given, also run ffmpeg to produce the cropped video.")
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

    # Take the MAXIMUM seen across samples for each edge: subtitle-free
    # frames reveal the bar's true (largest) extent; frames with bright
    # text/logo near the boundary would only ever UNDER-report it.
    final_top = max(tops)
    final_bottom = max(bottoms)
    final_left = max(lefts)
    final_right = max(rights)

    print(f"Detected -> top:{final_top} bottom:{final_bottom} "
          f"left:{final_left} right:{final_right}", file=sys.stderr)

    # Safety guard: never crop more than 40% of either dimension - if the
    # numbers are that large, something's wrong (or threshold too high)
    # rather than silently producing a mangled video.
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
    # even dimensions required by most codecs
    if new_w % 2: new_w -= 1
    if new_h % 2: new_h -= 1

    crop_str = f"crop={new_w}:{new_h}:{final_left}:{final_top}"
    print(crop_str)  # <- machine-readable stdout line, used by callers

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