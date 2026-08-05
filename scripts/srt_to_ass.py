"""
Converts an .srt file to .ass format, preserving Unicode text exactly
(including ZWJ/conjunct characters), so libass renders it via its
ASS demuxer path (proven correct for Sinhala) instead of its SRT demuxer.
"""
import sys
import re


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
    srt_path, ass_path, font_name = sys.argv[1:4]
    cues = parse_srt(srt_path)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},42,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,0,2,10,10,14,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        for start, end, text in cues:
            f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")

    print(f"Converted {len(cues)} cues to {ass_path}")


if __name__ == "__main__":
    main()
