"""
Clean manually-sourced/downloaded .ass subtitle files before burning:
  1. Removes Dialogue lines that are site credit / watermark / ad content
     (same marker list as clean_srt.py - sub.lk, cineru.lk, moviesublk, etc.)
  2. Forces every [V4+ Styles] "Style:" line's Fontname to the installed
     Sinhala font, so text never falls back to a missing font (tofu boxes).
  3. Strips any inline \\fnXxxx font-override tags inside Dialogue text so
     a per-line font pointing at something not installed on the runner
     can't override the style default either.
  4. Applies the dashboard's font size / margin-v / outline to every
     style line, so .ass uploads render consistently with .srt-derived
     ones across quality tiers.
  5. Ensures PlayResX/PlayResY exist in [Script Info] (defaults to
     1280x720) so scaling behaves the same as srt_to_ass.py output.

Usage:
    python clean_ass.py input.ass output.ass "Noto Sans Sinhala" \
        --font-size 42 --margin-v 14 --outline 1
"""
import re
import argparse

CREDIT_MARKERS = [
    r"sub\.lk",
    r"cineru\.lk",
    r"moviesub\s*lk",
    r"movies\s*ub\s*lk",
    r"w\s*w\s*w\s*\.\s*s\s*u\s*b\s*\.\s*l\s*k",
    r"w\s*w\s*w\s*\.\s*moviesublk\s*\.\s*com",
    r"පරිවර්තනය හා උපසිරැසි ගැන්වීම",
    r"වෙබ් අඩවිය වෙනුවෙන්",
    r"මෙම සිංහල උපසිරසිය",
    r"නොමිලේ නිකුත් කර ඇති",
    r"වෙබ් අඩවියට පිවිසෙන්න",
    r"Facebook\s*Page",
    r"Zoom,?\s*Facebook",
    r"Telegram",
    r"t\.me/",
    r"facebook\.com",
    r"G-?Drive",
]
CREDIT_REGEX = re.compile("|".join(CREDIT_MARKERS), re.IGNORECASE)

OVERRIDE_BLOCK_RE = re.compile(r"\{[^}]*\}")
FN_TAG_RE = re.compile(r"\\fn[^\\}]*")


def strip_overrides_for_matching(text):
    plain = OVERRIDE_BLOCK_RE.sub("", text)
    plain = plain.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
    return plain


def is_credit_line(text):
    return bool(CREDIT_REGEX.search(strip_overrides_for_matching(text)))


def strip_font_overrides(text):
    def _clean_block(m):
        cleaned = FN_TAG_RE.sub("", m.group(0))
        return "" if cleaned == "{}" else cleaned
    return OVERRIDE_BLOCK_RE.sub(_clean_block, text)


def parse_events_format(fmt_line):
    return [f.strip() for f in fmt_line.split(":", 1)[1].split(",")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path")
    parser.add_argument("output_path")
    parser.add_argument("font_name")
    parser.add_argument("--font-size", type=int, default=42)
    parser.add_argument("--margin-v", type=int, default=14)
    parser.add_argument("--outline", type=int, default=1)
    args = parser.parse_args()

    with open(args.input_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        raw = f.read()

    lines = raw.splitlines()
    out_lines = []
    section = None
    events_text_idx = None
    removed = 0
    kept = 0
    style_count = 0
    has_playresx = False
    has_playresy = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.lower()
            out_lines.append(line)
            continue

        if section == "[script info]":
            if stripped.lower().startswith("playresx"):
                has_playresx = True
            if stripped.lower().startswith("playresy"):
                has_playresy = True
            out_lines.append(line)
            continue

        if section in ("[v4+ styles]", "[v4 styles]"):
            if stripped.lower().startswith("style:"):
                fields = stripped.split(":", 1)[1].split(",")
                if len(fields) >= 22:
                    fields[1] = f" {args.font_name}"
                    fields[2] = f" {args.font_size}"
                    fields[16] = f" {args.outline}"
                    fields[21] = f" {args.margin_v}"
                    out_lines.append("Style:" + ",".join(fields))
                    style_count += 1
                    continue
            out_lines.append(line)
            continue

        if section == "[events]":
            if stripped.lower().startswith("format:"):
                fmt_fields = parse_events_format(stripped)
                if "Text" in fmt_fields:
                    events_text_idx = fmt_fields.index("Text")
                out_lines.append(line)
                continue

            if stripped.lower().startswith("dialogue:") and events_text_idx is not None:
                _, _, rest = stripped.partition(":")
                parts = rest.split(",", events_text_idx)
                text = parts[-1] if len(parts) > events_text_idx else ""

                if is_credit_line(text):
                    removed += 1
                    continue

                parts[-1] = strip_font_overrides(text)
                out_lines.append("Dialogue:" + ",".join(parts))
                kept += 1
                continue

            out_lines.append(line)
            continue

        out_lines.append(line)

    if not has_playresx or not has_playresy:
        for i, l in enumerate(out_lines):
            if l.strip().lower() == "[script info]":
                insert_at = i + 1
                if not has_playresx:
                    out_lines.insert(insert_at, "PlayResX: 1280")
                    insert_at += 1
                if not has_playresy:
                    out_lines.insert(insert_at, "PlayResY: 720")
                break

    with open(args.output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")

    print(f"clean_ass: {style_count} style(s) forced to font '{args.font_name}' "
          f"(size={args.font_size}, outline={args.outline}, marginv={args.margin_v})")
    print(f"clean_ass: removed {removed} credit/ad dialogue line(s), kept {kept}")


if __name__ == "__main__":
    main()