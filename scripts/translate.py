"""
Standalone English -> Sinhala SRT translator, run inside the GitHub Actions
job. Uses the "MasterSub AI" persona prompt for natural, colloquial,
emotionally-adapted Sri Lankan Sinhala subtitle translation.

v2: Multi-key rotation (up to 5 Gemini API keys) + larger batch size to
maximize daily throughput on the free tier while keeping Gemini 3.5 Flash
for translation quality (Flash-Lite was tested and rejected - it produced
occasional wrong-script/glitch characters in output).

Usage:
    python translate.py input.srt output.srt

Requires GEMINI_API_KEY_1 .. GEMINI_API_KEY_5 in the environment
(at least GEMINI_API_KEY_1 must be set; unset keys are simply skipped).
"""
import os
import re
import sys
import time

TARGET_CALLS = 3          # aim for this many API calls per episode - batch size is
                           # calculated from actual block count (min 60, max 300 - the
                           # tested-safe range; 350+ caused missing blocks in testing)
MIN_BATCH_SIZE = 60
MAX_BATCH_SIZE = 300
MODEL_NAME = "gemini-3.5-flash"
MAX_RETRIES_PER_KEY = 2   # retries on the SAME key before rotating to the next one
COOLDOWN_SECONDS = 3      # brief pause between retries

SYSTEM_PROMPT = """You are "MasterSub AI" — Sri Lanka's premiere K-Drama subtitle adapter, dubbing scriptwriter, and master native Sinhala storyteller.

YOUR GOAL:
Translate English SRT subtitle lines into 100% natural, colloquial, deeply emotional, spoken Sinhala (කතාබස් කරන ස්වාභාවික සිංහල). The resulting subtitles must feel as if they were hand-crafted by a passionate Sri Lankan fan editor, completely devoid of any mechanical, robotic, or formal dictionary language.

==================================================
CORE RULES OF ENGAGEMENT
==================================================

1. STRICT BAN ON "BOOK SINHALA" (පොත් භාෂාව තහනම්):
   - NEVER USE: මම, ඔබ, ඔහු, ඇය, ඔවුන්, මා, පවසයි, පැමිණියේය, කළෙමි, වන්නේය, නොවේ.
   - ALWAYS USE: මං, ඔයා, එයා, ඒගොල්ලෝ, කියනවා, ආවා, කළා, නෑ / නෙවෙයි.

2. EMOTIONAL CONTEXT ADAPTATION (හැඟීම් සහ සම්බන්ධතා):
   - Romantic / Heartbreak: Use warm, tender, intimate particles ("අනේ...", "...තේරුණාද?", "...නේද?", "අනේ මන්දා").
   - Deep Sadness / Crying: Capture hesitation and breath pauses ("මට... මට ඒක කරන්න බැරි වුණා...", "ආයේ නම් එපා...").
   - Close Friends / Squad: Use casual Sri Lankan friendship tone ("බං", "මචං", "උඹ", "යකෝ", "පිස්සුද බං").
   - Angry / Conflict: Use sharp, conversational Sinhala ("මොකක්ද කිව්වේ?", "තමුසෙට පිස්සුද?", "කට වහගන්නවා!").
   - Elders / Bosses / Authority: Use respectful spoken Sinhala ("සර්", "ඇන්ටි", "අංකල්", "මහත්තයා").

3. CULTURAL & K-DRAMA GLOSSARY INJECTION:
   - "Oppa" (when romantic/older boy): "අයියා" / "ඔප්පා" (Keep context-sensitive).
   - "Sunbae" (senior): "සර්" / "සුන්බේ" / "අයියා".
   - "Ahjumma" / "Ahjussi": "ඇන්ටි" / "අංකල්".
   - "Daebak!": "පට්ට!" / "නියමයි!" / "පිස්සු හැදෙනවා!".
   - "Fighting! / Aja!": "ගැම්මෙන්ම කරමු!" / "ජය වේවා!".
   - "Otoke?": "දැන් මොකද කරන්නේ?" / "අනේ දෙවියනේ...".

4. NATURAL SRI LANKAN IDIOMATIC REWRITING:
   - Do not translate word-for-word. Translate the MEANING and EMOTION of the sentence.
   - Example 1: "I have your back" -> "මං ඔයාගේ පැත්තේ ඉන්නවා" (NOT: මම ඔබේ පිටුපස සිටිමි).
   - Example 2: "Are you out of your mind?" -> "උඹට පිස්සු හැදීගෙන එනවද බං?" (NOT: ඔබ ඔබේ මනසින් බැහැරව සිටිනවාද?).

5. SRT FORMATTING PROTECTION (STRICT):
   - Do NOT alter, shift, or delete subtitle index numbers or timestamps.
   - Do NOT wrap timestamps inside code blocks or markdown.
   - ONLY translate the dialogue text underneath the timestamp.
   - Maintain line breaks if a single subtitle has two speakers (e.g., lines starting with "- ").

==================================================
OUTPUT EXAMPLE QUALITY BENCHMARK
==================================================

[INPUT SRT BLOCK]
45
00:03:12,100 --> 00:03:15,400
- Why didn't you answer my calls?
- I was scared. I didn't know what to say to you.

46
00:03:16,000 --> 00:03:19,200
I tried so hard to forget you, but it was impossible.

[YOUR REQUIRED OUTPUT]
45
00:03:12,100 --> 00:03:15,400
- ඇයි ඔයා මගේ Call වලට Answer නොකළේ?
- මට බය හිතුණා... ඔයාට මොනවා කියන්නද කියලා මං දැනන් හිටියේ නෑ.

46
00:03:16,000 --> 00:03:19,200
මං ඔයාව අමතක කරන්න නොකරපු දෙයක් නෑ... ඒත් මට ඒක කරන්නම බැරි වුණා අනේ...

==================================================
EXECUTION MODE:
Translate the provided SRT block now adhering strictly to all the rules above. Return ONLY the translated SRT content without any intro or outro text."""


def load_api_keys():
    """Collect GEMINI_API_KEY_1 .. GEMINI_API_KEY_5 (skip unset ones).
    Falls back to plain GEMINI_API_KEY if none of the numbered ones exist,
    so this stays compatible with older single-key setups."""
    keys = []
    for i in range(1, 6):
        key = os.environ.get(f"GEMINI_API_KEY_{i}")
        if key:
            keys.append(key)
    if not keys:
        single = os.environ.get("GEMINI_API_KEY")
        if single:
            keys.append(single)
    if not keys:
        print("ERROR: No Gemini API keys found in environment "
              "(expected GEMINI_API_KEY_1..GEMINI_API_KEY_5 or GEMINI_API_KEY).",
              file=sys.stderr)
        sys.exit(1)
    print(f"Loaded {len(keys)} Gemini API key(s) for rotation.", file=sys.stderr)
    return keys


def parse_srt_blocks(path):
    """Return list of raw SRT block strings (index+timing+text), unmodified."""
    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()
    blocks = re.split(r"\n\s*\n", content.strip())
    return [b.strip() for b in blocks if b.strip()]


def get_index(block):
    m = re.match(r"(\d+)", block)
    return int(m.group(1)) if m else None


def translate_chunk(model, blocks_chunk):
    """Send a chunk of raw SRT blocks, get back translated raw SRT blocks."""
    joined = "\n\n".join(blocks_chunk)
    full_prompt = SYSTEM_PROMPT + "\n\n[SRT BLOCK TO TRANSLATE NOW]\n" + joined

    response = model.generate_content(
        full_prompt,
        request_options={"timeout": 300},  # allow up to 5 min for large batches
    )
    text = response.text.strip()

    # Strip accidental markdown code fences if the model adds them
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)

    result_blocks = re.split(r"\n\s*\n", text.strip())
    return [b.strip() for b in result_blocks if b.strip()]


class KeyRotator:
    """Holds a list of API keys and hands out a configured genai model,
    rotating to the next key whenever the current one is rate-limited
    or otherwise fails."""

    def __init__(self, api_keys, model_name):
        import google.generativeai as genai
        self._genai = genai
        self.api_keys = api_keys
        self.model_name = model_name
        self.current_idx = 0
        self._configure_current()

    def _configure_current(self):
        self._genai.configure(api_key=self.api_keys[self.current_idx])
        self.model = self._genai.GenerativeModel(self.model_name)

    def rotate(self):
        self.current_idx = (self.current_idx + 1) % len(self.api_keys)
        print(f"  Rotating to API key #{self.current_idx + 1}/{len(self.api_keys)}",
              file=sys.stderr)
        self._configure_current()

    def translate_with_rotation(self, blocks_chunk, expected_indexes):
        """Try translating with the current key; on failure, retry a couple
        times, then rotate through remaining keys until one works or all
        are exhausted.

        Uses INDEX-BASED alignment rather than requiring an exact block
        count match: Gemini occasionally merges/splits a line (e.g. 272
        blocks sent, 271 or 273 come back) even though the actual
        translation content is fine. Matching by each block's own leading
        index number is far more robust than counting blocks."""
        expected_set = set(expected_indexes)
        keys_tried = 0
        while keys_tried < len(self.api_keys):
            for attempt in range(MAX_RETRIES_PER_KEY):
                try:
                    result = translate_chunk(self.model, blocks_chunk)
                    result_by_idx = {}
                    for block in result:
                        idx = get_index(block)
                        if idx is not None and idx in expected_set:
                            result_by_idx[idx] = block

                    missing = expected_set - set(result_by_idx.keys())
                    coverage = len(result_by_idx) / len(expected_set)

                    if not missing:
                        return result_by_idx  # perfect - every expected index accounted for
                    if coverage >= 0.95:
                        # Good enough - a stray line or two got merged/split.
                        # Original English will be used as fallback for the
                        # handful of missing indexes (filled in by caller).
                        print(f"  Accepting partial result: {len(result_by_idx)}/{len(expected_set)} "
                              f"blocks matched by index ({len(missing)} will fall back to original text).",
                              file=sys.stderr)
                        return result_by_idx

                    print(f"  Too many blocks unmatched ({len(missing)}/{len(expected_set)} missing), "
                          f"retrying on same key...", file=sys.stderr)
                except Exception as e:
                    err_str = str(e)
                    is_rate_limit = "429" in err_str or "quota" in err_str.lower() or "rate" in err_str.lower()
                    print(f"  API call failed (attempt {attempt + 1}/{MAX_RETRIES_PER_KEY}) "
                          f"on key #{self.current_idx + 1}: {e}", file=sys.stderr)
                    if is_rate_limit:
                        break  # don't waste retries on a rate-limited key, rotate immediately
                time.sleep(COOLDOWN_SECONDS)

            keys_tried += 1
            if keys_tried < len(self.api_keys):
                self.rotate()

        return {}  # every key failed for this chunk - caller falls back to original text


def main():
    input_path, output_path = sys.argv[1], sys.argv[2]

    api_keys = load_api_keys()
    rotator = KeyRotator(api_keys, MODEL_NAME)

    all_blocks = parse_srt_blocks(input_path)
    if not all_blocks:
        print("No subtitle blocks found.", file=sys.stderr)
        sys.exit(1)

    # Work out batch size from the actual episode length, so every episode
    # takes roughly TARGET_CALLS API calls regardless of how long it is.
    import math
    batch_size = math.ceil(len(all_blocks) / TARGET_CALLS)
    batch_size = max(MIN_BATCH_SIZE, min(MAX_BATCH_SIZE, batch_size))

    print(f"Total blocks: {len(all_blocks)} | Target calls: {TARGET_CALLS} | "
          f"Computed BATCH_SIZE: {batch_size} | "
          f"Actual calls needed: {-(-len(all_blocks) // batch_size)}", file=sys.stderr)

    translated_by_index = {}

    for start in range(0, len(all_blocks), batch_size):
        chunk = all_blocks[start:start + batch_size]
        expected_indexes = [get_index(b) for b in chunk]

        print(f"Translating blocks {start + 1}-{start + len(chunk)} of {len(all_blocks)}...",
              file=sys.stderr)

        result_by_idx = rotator.translate_with_rotation(chunk, expected_indexes)

        for idx, block in zip(expected_indexes, chunk):
            if idx in result_by_idx:
                translated_by_index[idx] = result_by_idx[idx]
            else:
                # Not matched even after retries/rotation - keep original
                # text for just this one block rather than losing it.
                translated_by_index[idx] = block

        matched = sum(1 for idx in expected_indexes if idx in result_by_idx)
        if matched < len(expected_indexes):
            print(f"  WARNING: {len(expected_indexes) - matched} block(s) in this chunk "
                  f"kept original text (untranslated).", file=sys.stderr)
            for idx, original_block in zip(expected_indexes, chunk):
                translated_by_index[idx] = original_block

    # Write out in original order
    with open(output_path, "w", encoding="utf-8") as f:
        for block in all_blocks:
            idx = get_index(block)
            final_block = translated_by_index.get(idx, block)
            f.write(final_block + "\n\n")

    print(f"Translated {len(all_blocks)} blocks -> {output_path}")


if __name__ == "__main__":
    main()