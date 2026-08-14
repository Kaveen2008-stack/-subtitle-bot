"""
Test script: BATCH_SIZE=60 translation test.
Generates a synthetic 60-block SRT sample and sends it as ONE API call,
to check:
  1. Does the response get truncated (output token limit)?
  2. Does the block count match (60 in -> 60 out)?
  3. Rough quality spot-check on a few lines.

Usage:
    export GEMINI_API_KEY="your_key_here"
    python test_batch60.py
"""
import os
import re
import google.generativeai as genai

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

5. SRT FORMATTING PROTECTION (STRICT):
   - Do NOT alter, shift, or delete subtitle index numbers or timestamps.
   - Do NOT wrap timestamps inside code blocks or markdown.
   - ONLY translate the dialogue text underneath the timestamp.
   - Maintain line breaks if a single subtitle has two speakers (e.g., lines starting with "- ").

EXECUTION MODE:
Translate the provided SRT block now adhering strictly to all the rules above. Return ONLY the translated SRT content without any intro or outro text."""

# A rotating pool of realistic English dialogue lines (mix of casual, emotional, formal)
SAMPLE_LINES = [
    "Why didn't you answer my calls?",
    "I was scared. I didn't know what to say to you.",
    "I tried so hard to forget you, but it was impossible.",
    "Are you out of your mind? You can't just leave like that!",
    "Oppa, I have your back. No matter what happens.",
    "Daebak! You actually did it! Fighting!",
    "Sir, the CEO wants to see you in his office immediately.",
    "I can't believe you did this to me.",
    "Let's go, we don't have much time.",
    "What are you talking about? That doesn't make sense.",
    "I love you. I've always loved you.",
    "Get out of my way!",
    "Please, just listen to me for one second.",
    "This isn't over. I'll find a way.",
    "I'm sorry. I really am.",
]


def build_srt_block(n_blocks):
    """Build a synthetic SRT block with n_blocks entries, cycling through sample lines."""
    parts = []
    for i in range(1, n_blocks + 1):
        start = i * 3
        end = start + 2
        line = SAMPLE_LINES[(i - 1) % len(SAMPLE_LINES)]
        parts.append(
            f"{i}\n00:{start:02d}:00,000 --> 00:{end:02d}:00,000\n{line}\n"
        )
    return "\n".join(parts)


def get_index(block):
    m = re.match(r"(\d+)", block)
    return int(m.group(1)) if m else None


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: Set GEMINI_API_KEY environment variable first.")
        return

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-3.5-flash")

    BATCH_SIZE = 350
    srt_input = build_srt_block(BATCH_SIZE)
    input_blocks = [b.strip() for b in re.split(r"\n\s*\n", srt_input.strip()) if b.strip()]

    print(f"Sending {len(input_blocks)} blocks in ONE API call (BATCH_SIZE={BATCH_SIZE})...")
    print()

    full_prompt = SYSTEM_PROMPT + "\n\n[SRT BLOCK TO TRANSLATE NOW]\n" + srt_input
    response = model.generate_content(full_prompt)
    text = response.text.strip()

    # Strip accidental markdown fences
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)

    output_blocks = [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]

    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Blocks sent:     {len(input_blocks)}")
    print(f"Blocks received: {len(output_blocks)}")

    if len(output_blocks) == len(input_blocks):
        print("✅ MATCH — no truncation, all blocks came back.")
    else:
        print("❌ MISMATCH — response was likely truncated or malformed.")

    # Check finish_reason if available (tells us if it hit the token limit)
    try:
        finish_reason = response.candidates[0].finish_reason
        print(f"Finish reason: {finish_reason} (2 = MAX_TOKENS means truncated)")
    except Exception:
        pass

    print()
    print("--- First 3 translated blocks (spot check) ---")
    for b in output_blocks[:3]:
        print(b)
        print()

    print("--- Last 3 translated blocks (checks if the END got cut off) ---")
    for b in output_blocks[-3:]:
        print(b)
        print()

    # Check indexes are sequential and nothing is missing/duplicated
    indexes = [get_index(b) for b in output_blocks]
    expected = list(range(1, len(input_blocks) + 1))
    missing = set(expected) - set(indexes)
    if missing:
        print(f"⚠️  Missing block indexes: {sorted(missing)}")
    else:
        print("✅ All block indexes present and in order.")


if __name__ == "__main__":
    main()