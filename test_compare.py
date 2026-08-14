"""
Quick test script: Compares Gemini Flash vs Flash-Lite translation quality
on the same sample SRT block, side by side.

Usage:
    export GEMINI_API_KEY="your_key_here"
    python test_compare.py

Requires: pip install google-generativeai
"""
import os
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
   - Example 1: "I have your back" -> "මං ඔයාගේ පැත්තේ ඉන්නවා" (NOT: මම ඔබේ පිටුපස සිටිමි).
   - Example 2: "Are you out of your mind?" -> "උඹට පිස්සු හැදීගෙන එනවද බං?" (NOT: ඔබ ඔබේ මනසින් බැහැරව සිටිනවාද?).

5. SRT FORMATTING PROTECTION (STRICT):
   - Do NOT alter, shift, or delete subtitle index numbers or timestamps.
   - Do NOT wrap timestamps inside code blocks or markdown.
   - ONLY translate the dialogue text underneath the timestamp.
   - Maintain line breaks if a single subtitle has two speakers (e.g., lines starting with "- ").

EXECUTION MODE:
Translate the provided SRT block now adhering strictly to all the rules above. Return ONLY the translated SRT content without any intro or outro text."""

# Sample block - mix of emotional, casual, and idiomatic lines to stress-test quality
SAMPLE_SRT = """1
00:01:12,100 --> 00:01:15,400
- Why didn't you answer my calls?
- I was scared. I didn't know what to say to you.

2
00:01:16,000 --> 00:01:19,200
I tried so hard to forget you, but it was impossible.

3
00:01:20,000 --> 00:01:23,500
Are you out of your mind? You can't just leave like that!

4
00:01:24,000 --> 00:01:27,000
Oppa, I have your back. No matter what happens.

5
00:01:28,000 --> 00:01:31,000
Daebak! You actually did it! Fighting!

6
00:01:32,000 --> 00:01:35,500
Sir, the CEO wants to see you in his office immediately.
"""


def translate_with_model(model_name, srt_text):
    model = genai.GenerativeModel(model_name)
    full_prompt = SYSTEM_PROMPT + "\n\n[SRT BLOCK TO TRANSLATE NOW]\n" + srt_text
    response = model.generate_content(full_prompt)
    return response.text.strip()


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: Set GEMINI_API_KEY environment variable first.")
        return

    genai.configure(api_key=api_key)

    models_to_test = [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]

    for model_name in models_to_test:
        print("=" * 60)
        print(f"MODEL: {model_name}")
        print("=" * 60)
        try:
            result = translate_with_model(model_name, SAMPLE_SRT)
            print(result)
        except Exception as e:
            print(f"FAILED: {e}")
        print()


if __name__ == "__main__":
    main()
