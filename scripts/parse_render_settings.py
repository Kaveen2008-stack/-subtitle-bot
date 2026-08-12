"""
Reads a Supabase render_settings API response (JSON file) and prints
GITHUB_ENV-format lines (KEY=value). Used by burn_batch.yml right before
the per-episode loop, so the whole batch run uses one fetched settings row
(quality doesn't change within a single batch dispatch).

Usage: python3 parse_render_settings.py /tmp/render_settings.json
"""
import json
import sys

DEFAULTS = {
    "RENDER_FONT_SIZE": 42,
    "RENDER_MARGIN_V": 14,
    "RENDER_OUTLINE": 1,
    "RENDER_WATERMARK_TEXT": "OnlyKSub",
    "RENDER_WATERMARK_OPACITY": 0.6,
    "RENDER_WATERMARK_FONTSIZE": 16,
    "RENDER_CRF": 23,
    "RENDER_AUDIO_BITRATE": "128k",
}


def main():
    path = sys.argv[1]
    values = dict(DEFAULTS)

    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        if not rows:
            raise ValueError("empty response - no settings row for this quality")
        s = rows[0]
        values["RENDER_FONT_SIZE"] = s.get("font_size", DEFAULTS["RENDER_FONT_SIZE"])
        values["RENDER_MARGIN_V"] = s.get("margin_v", DEFAULTS["RENDER_MARGIN_V"])
        values["RENDER_OUTLINE"] = s.get("outline_width", DEFAULTS["RENDER_OUTLINE"])
        values["RENDER_WATERMARK_TEXT"] = s.get("watermark_text", DEFAULTS["RENDER_WATERMARK_TEXT"])
        values["RENDER_WATERMARK_OPACITY"] = s.get("watermark_opacity", DEFAULTS["RENDER_WATERMARK_OPACITY"])
        values["RENDER_WATERMARK_FONTSIZE"] = s.get("watermark_fontsize", DEFAULTS["RENDER_WATERMARK_FONTSIZE"])
        values["RENDER_CRF"] = s.get("crf", DEFAULTS["RENDER_CRF"])
        values["RENDER_AUDIO_BITRATE"] = s.get("audio_bitrate", DEFAULTS["RENDER_AUDIO_BITRATE"])
    except Exception as e:
        print(f"WARNING: could not parse render settings ({e}), using defaults", file=sys.stderr)

    for key, val in values.items():
        print(f"{key}={val}")


if __name__ == "__main__":
    main()
