"""
Converts an .srt file to .ass format, preserving Unicode text exactly
(including ZWJ/conjunct characters), so libass renders it via its
ASS demuxer path (proven correct for Sinhala) instead of its SRT demuxer.

Usage (backward compatible - old 3-arg calls still work):
    python srt_to_ass.py subs.srt subs.ass "Noto Sans Sinhala"
    python srt_to_ass.py subs.srt subs.ass "Noto Sans Sinhala" --font-size 64 --margin-v 30 --outline 3
"""
import sys
import re
import argparse


def srt_time_to_ass(t):
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    centisec = int(ms) // 10
    return f"{int(h)}:{m}:{s}.{centisec:02d}"


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
            start = srt_time_to_ass(start_str)
            end = srt_time_to_ass(end_str)
            text = "\\N".join(lines[2:])  # ASS line-break token
            cues.append((start, end, text))
    return cues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("srt_path")
    parser.add_argument("ass_path")
    parser.add_argument("font_name")
    # Defaults match the original hardcoded values - if the admin dashboard
    # settings fetch fails for any reason, output looks the same as before.
    parser.add_argument("--font-size", type=int, default=42)
    parser.add_argument("--margin-v", type=int, default=14)
    parser.add_argument("--outline", type=int, default=1)
    args = parser.parse_args()

    cues = parse_srt(args.srt_path)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{args.font_name},{args.font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{args.outline},0,2,10,10,{args.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    with open(args.ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        for start, end, text in cues:
            f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")

    print(f"Converted {len(cues)} cues to {args.ass_path} (font={args.font_size}, margin={args.margin_v}, outline={args.outline})")


if __name__ == "__main__":
    main()
