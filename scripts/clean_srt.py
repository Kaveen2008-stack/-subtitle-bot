"""
Clean manually-sourced Sinhala SRT files before burning:
  1. Removes entire blocks that are site credit / watermark / ad content
     (matched by known site names and phrases - sub.lk, cineru.lk, etc.)
  2. Strips <font color="..."> tags from remaining blocks but KEEPS the
     dialogue text inside them (title cards, dramatic reveals like "K",
     "Babylon" are real subtitle content, not ads).
  3. Renumbers the remaining blocks sequentially (1, 2, 3...) so index
     numbers stay valid after removals.

Usage:
    python clean_srt.py input.srt output.srt
"""
import re
import sys

# Add more site names/phrases here as you encounter new sources.
# Match is case-insensitive and matches ANYWHERE in the block's text.
CREDIT_MARKERS = [
    r"sub\.lk",
    r"cineru\.lk",
    r"w\s*w\s*w\s*\.\s*s\s*u\s*b\s*\.\s*l\s*k",   # handles spaced-out "W W W . S U B . L K"
    r"පරිවර්තනය හා උපසිරැසි ගැන්වීම",
    r"වෙබ් අඩවිය වෙනුවෙන්",
    r"මෙම සිංහල උපසිරසිය",
    r"නොමිලේ නිකුත් කර ඇති",
    r"Zoom,?\s*Facebook",
    r"Telegram",
    r"t\.me/",
    r"facebook\.com",
]

CREDIT_REGEX = re.compile("|".join(CREDIT_MARKERS), re.IGNORECASE)
FONT_TAG_REGEX = re.compile(r"</?font[^>]*>", re.IGNORECASE)


def parse_blocks(content):
    """Split into raw blocks, return list of (index_line, timing_line, text_lines)."""
    raw_blocks = re.split(r"\r?\n\s*\r?\n", content.strip())
    parsed = []
    for raw in raw_blocks:
        raw = raw.strip()
        if not raw:
            continue
        lines = raw.splitlines()
        if len(lines) < 2:
            continue  # malformed / empty block, skip
        index_line = lines[0].strip()
        timing_line = lines[1].strip()
        text_lines = lines[2:]
        parsed.append((index_line, timing_line, text_lines))
    return parsed


def is_credit_block(text_lines):
    joined = " ".join(text_lines)
    return bool(CREDIT_REGEX.search(joined))


def clean_text_line(line):
    """Strip font tags but keep the text inside them."""
    return FONT_TAG_REGEX.sub("", line).strip()


def main():
    if len(sys.argv) != 3:
        print("Usage: python clean_srt.py input.srt output.srt")
        sys.exit(1)

    input_path, output_path = sys.argv[1], sys.argv[2]

    with open(input_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        content = f.read()

    blocks = parse_blocks(content)
    print(f"Total blocks found: {len(blocks)}")

    kept_blocks = []
    removed_count = 0

    for index_line, timing_line, text_lines in blocks:
        if is_credit_block(text_lines):
            removed_count += 1
            print(f"  Removing credit block (was #{index_line}): {' '.join(text_lines)[:80]}")
            continue

        cleaned_text_lines = [clean_text_line(l) for l in text_lines]
        # Drop any lines that became empty after stripping tags
        cleaned_text_lines = [l for l in cleaned_text_lines if l]

        if not cleaned_text_lines:
            # Block had only tags/whitespace, nothing left - drop it too
            removed_count += 1
            continue

        kept_blocks.append((timing_line, cleaned_text_lines))

    print(f"Removed {removed_count} blocks (credit/ads/empty).")
    print(f"Kept {len(kept_blocks)} blocks.")

    with open(output_path, "w", encoding="utf-8") as f:
        for new_index, (timing_line, text_lines) in enumerate(kept_blocks, start=1):
            f.write(f"{new_index}\n")
            f.write(f"{timing_line}\n")
            for line in text_lines:
                f.write(f"{line}\n")
            f.write("\n")

    print(f"Cleaned SRT written to: {output_path}")


if __name__ == "__main__":
    main()