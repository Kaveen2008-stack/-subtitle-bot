"""
Converts an .srt file to .ass format, preserving Unicode text exactly
(including ZWJ/conjunct characters), so libass renders it via its
ASS demuxer path (proven correct for Sinhala) instead of its SRT demuxer.

Strips HTML-style tags that SRT files sometimes carry over from their
source (e.g. Netflix/streaming rips wrap every line in
<font color="white">...</font>), since ASS does not understand HTML tags
and would otherwise burn them into the video as literal text. <i> tags
are converted to the equivalent ASS override codes so italics still work.

Usage (backward compatible - old 3-arg calls still work):
    python srt_to_ass.py subs.srt subs.ass "Noto Sans Sinhala"
    python srt_to_ass.py subs.srt subs.ass "Noto Sans Sinhala" --font-size 64 --margin-v 30 --outline 3
"""
import sys
import re
import argparse

# Matches <font ...> and </font> (any attributes, case-insensitive)
FONT_TAG_RE = re.compile(r"</?font[^>]*>", re.IGNORECASE)
# Matches <i> and </i>
ITALIC_OPEN_RE = re.compile(r"<i\s*>", re.IGNORECASE)
ITALIC_CLOSE_RE = re.compile(r"</i\s*>", re.IGNORECASE)
# Matches <b> and </b>
BOLD_OPEN_RE = re.compile(r"<b\s*>", re.IGNORECASE)
BOLD_CLOSE_RE = re.compile(r"</b\s*>", re.IGNORECASE)
# Catch-all for any other stray HTML tag we don't explicitly handle
STRAY_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def clean_line(text):
    """Strip <font> tags entirely, convert <i>/<b> to ASS override codes,
    and remove any other stray HTML tags so nothing leaks into the video
    as literal text."""
    text = FONT_TAG_RE.sub("", text)
    text = ITALIC_OPEN_RE.sub(r"{\\i1}", text)
    text = ITALIC_CLOSE_RE.sub(r"{\\i0}", text)
    text = BOLD_OPEN_RE.sub(r"{\\b1}", text)
    text = BOLD_CLOSE_RE.sub(r"{\\b0}", text)
    text = STRAY_TAG_RE.sub("", text)  # anything else HTML-like left over
    return text


def srt_time_to_ass(t):
    t = t.strip()

    # Standard: H:MM:SS,mmm or H:MM:SS.mmm
    m = re.match(r"^(\d+):(\d+):(\d+)[.,](\d+)$", t)
    if m:
        h, mnt, s, ms = m.groups()
    else:
        # Some fan-sub sources (e.g. certain sub.lk-style SRTs) drop the
        # hours field and/or use ':' instead of ',' before milliseconds,
        # e.g. "00:43:816" meaning MM:SS:mmm rather than H:MM:SS. Since a
        # valid seconds value is always < 60, three colon-separated groups
        # where the last one is >= 60 (or exactly 3 digits, i.e. clearly a
        # millisecond value) can only be MM:SS:mmm with hours omitted.
        m = re.match(r"^(\d+):(\d+):(\d+)$", t)
        if m:
            mnt, s, ms = m.groups()
            h = "0"
        else:
            # MM:SS,mmm / MM:SS.mmm with no hours field at all
            m = re.match(r"^(\d+):(\d+)[.,](\d+)$", t)
            if m:
                mnt, s, ms = m.groups()
                h = "0"
            else:
                raise ValueError(f"Cannot parse timestamp: {t!r}")

    ms = (ms + "000")[:3]  # pad/truncate to exactly 3 digits
    centisec = int(ms) // 10
    return f"{int(h)}:{mnt}:{s}.{centisec:02d}"


def parse_srt(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    blocks = re.split(r"\n\s*\n", content.strip())
    cues = []
    skipped = 0
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) >= 3:
            timing = lines[1]
            if "-->" not in timing:
                skipped += 1
                continue
            start_str, end_str = [x.strip() for x in timing.split("-->")]
            try:
                start = srt_time_to_ass(start_str)
                end = srt_time_to_ass(end_str)
            except ValueError as e:
                # Don't let one malformed cue (bad source timestamp) kill
                # the whole episode's subtitle burn - skip it and keep going.
                print(f"WARNING: skipping cue with unparseable timing ({e}): {timing!r}", file=sys.stderr)
                skipped += 1
                continue
            text_lines = [clean_line(l) for l in lines[2:]]
            text = "\\N".join(text_lines)  # ASS line-break token
            cues.append((start, end, text))
    if skipped:
        print(f"WARNING: skipped {skipped} cue(s) with malformed/missing timing", file=sys.stderr)
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