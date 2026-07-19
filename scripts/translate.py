"""
Standalone English -> Sinhala SRT translator, run inside the GitHub Actions
job (not on the bot server). Usage:
    python translate.py input.srt output.srt
Requires GEMINI_API_KEY in the environment.
"""
import os
import re
import sys


def parse_srt(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    blocks = re.split(r"\n\s*\n", content.strip())
    parsed = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) >= 3:
            parsed.append((lines[0], lines[1], "\n".join(lines[2:])))
    return parsed


def write_srt(path, blocks):
    with open(path, "w", encoding="utf-8") as f:
        for idx, timing, text in blocks:
            f.write(f"{idx}\n{timing}\n{text}\n\n")


def main():
    input_path, output_path = sys.argv[1], sys.argv[2]
    api_key = os.environ["GEMINI_API_KEY"]

    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

    blocks = parse_srt(input_path)
    if not blocks:
        print("No subtitle blocks found.", file=sys.stderr)
        sys.exit(1)

    joined = "\n".join(f"[{i}] {text}" for i, (_, _, text) in enumerate(blocks))
    prompt = (
        "Translate the following numbered subtitle lines from English to natural, "
        "conversational Sinhala. Keep the [N] markers exactly as they are, one per line, "
        "and do not merge or split lines.\n\n" + joined
    )
    response = model.generate_content(prompt)

    translated = {}
    for line in response.text.strip().split("\n"):
        m = re.match(r"\[(\d+)\]\s?(.*)", line)
        if m:
            translated[int(m.group(1))] = m.group(2)

    new_blocks = [
        (idx, timing, translated.get(i, text))
        for i, (idx, timing, text) in enumerate(blocks)
    ]
    write_srt(output_path, new_blocks)
    print(f"Translated {len(new_blocks)} blocks -> {output_path}")


if __name__ == "__main__":
    main()
