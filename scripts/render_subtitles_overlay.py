"""
Renders an SRT file into a transparent-background overlay video (alpha
channel) using Pillow with the raqm text layout engine, which does proper
Unicode complex-script shaping (correct for Sinhala/Tamil/Devanagari
conjuncts and vowel-sign reordering) - unlike libass/ffmpeg's built-in
'subtitles' filter, which has known bugs with these scripts.

Usage:
    python render_subtitles_overlay.py <input_video> <subs.srt> <output_overlay.mov> <font_path>

The resulting overlay.mov has an alpha channel and is meant to be
composited onto the original video with ffmpeg's overlay filter.
"""
import json
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont, features


def get_video_info(video_path):
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "json", video_path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    width = data["streams"][0]["width"]
    height = data["streams"][0]["height"]
    duration = float(data["format"]["duration"])
    return width, height, duration


def srt_time_to_seconds(t):
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    blocks = re.split(r"\n\s*\n", content.strip())
    cues = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) >= 3:
            timing = lines[1]
            start_str, end_str = [x.strip() for x in timing.split("-->")]
            start = srt_time_to_seconds(start_str)
            end = srt_time_to_seconds(end_str)
            text = "\n".join(lines[2:])
            cues.append((start, end, text))
    return cues


def make_font(font_path, size):
    try:
        return ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.RAQM)
    except AttributeError:
        return ImageFont.truetype(font_path, size, layout_engine=ImageFont.LAYOUT_RAQM)


def render_text_frame(width, height, text, font, margin_bottom):
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bbox = draw.multiline_textbbox((0, 0), text, font=font, align="center", stroke_width=2)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (width - text_w) / 2 - bbox[0]
    y = height - margin_bottom - text_h - bbox[1]

    draw.multiline_text(
        (x, y), text, font=font, fill=(255, 255, 255, 255),
        stroke_width=2, stroke_fill=(0, 0, 0, 255), align="center",
    )
    return img


def main():
    video_path, srt_path, output_path, font_path = sys.argv[1:5]

    if not features.check("raqm"):
        print("WARNING: Pillow was not built with raqm support - complex script "
              "shaping (Sinhala) will be INCORRECT. Check libraqm0 is installed.",
              file=sys.stderr)

    width, height, duration = get_video_info(video_path)
    cues = parse_srt(srt_path)

    font_size = max(16, int(height * 0.032))
    margin_bottom = int(height * 0.06)
    font = make_font(font_path, font_size)

    blank = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    frames_dir = "overlay_frames"
    import os
    os.makedirs(frames_dir, exist_ok=True)
    concat_lines = []

    cursor = 0.0
    for i, (start, end, text) in enumerate(cues):
        if start > cursor:
            gap_path = f"{frames_dir}/blank_{i}.png"
            blank.save(gap_path)
            concat_lines.append(f"file '{gap_path}'\nduration {start - cursor:.3f}\n")

        frame = render_text_frame(width, height, text, font, margin_bottom)
        frame_path = f"{frames_dir}/cue_{i}.png"
        frame.save(frame_path)
        concat_lines.append(f"file '{frame_path}'\nduration {end - start:.3f}\n")
        cursor = end

    if duration > cursor:
        tail_path = f"{frames_dir}/blank_tail.png"
        blank.save(tail_path)
        concat_lines.append(f"file '{tail_path}'\nduration {duration - cursor:.3f}\n")

    if concat_lines:
        concat_lines.append(f"file '{concat_lines[-1].split(chr(39))[1]}'\n")

    list_path = "overlay_list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        f.writelines(concat_lines)

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-pix_fmt", "yuva420p", "-c:v", "qtrle",
        output_path,
    ]
    subprocess.run(cmd, check=True)
    print(f"Overlay video written to {output_path}")


if __name__ == "__main__":
    main()