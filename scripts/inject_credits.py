"""
Injects a colorful, multi-line "OnlyKsub" credit card into a finished
subs.ass file TWICE - once at the very start (0:00:00.00 - 0:00:03.00)
and once at the very end (video_duration - 3.00 - video_duration), the
same way sites like moviesublk.com burn their own branding into every
episode's opening and closing few seconds.

Runs LAST, after subs.ass already has its final [V4+ Styles] Default
style (installed font, dashboard font size/margin/outline already
applied by srt_to_ass.py or clean_ass.py) - this script only adds one
extra Style ("Credit") + two extra Dialogue lines, it never touches the
existing dialogue.

Usage:
    python inject_credits.py subs.ass <video_duration_seconds> "Noto Sans Sinhala" [--seconds 3] [--font-size 48]
"""
import argparse

# ASS BGR hex colours (not RGB - &HBBGGRR&)
COLOR_RED = r"\c&H4040FF&"     # bright red
COLOR_BLUE = r"\c&HFF6B35&"    # bright blue
COLOR_YELLOW = r"\c&H4DE1FF&"  # bright yellow
COLOR_GREEN = r"\c&H4DFF4D&"   # bright green

DEFAULT_LINES = [
    f"{{{COLOR_RED}}}Facebook Page {{{COLOR_BLUE}}}\u27a4 OnlyKsub",
    f"{{{COLOR_YELLOW}}}සිංහල උපසිරසි සමග Movies & TV Series",
    f"{{{COLOR_YELLOW}}}Online නරඹන්න සහ Download කරගන්න",
    f"{{{COLOR_GREEN}}}\u27a4 www.onlyksub.xyz",
]


def sec_to_ass_time(seconds):
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ass_path")
    parser.add_argument("video_duration", type=float, help="Total video duration in seconds (from ffprobe)")
    parser.add_argument("font_name")
    parser.add_argument("--seconds", type=float, default=3.0, help="How long each credit card is shown")
    parser.add_argument("--font-size", type=int, default=48)
    args = parser.parse_args()

    with open(args.ass_path, "r", encoding="utf-8") as f:
        content = f.read()

    if "[V4+ Styles]" not in content:
        raise SystemExit("ERROR: subs.ass has no [V4+ Styles] section - run srt_to_ass.py/clean_ass.py first")

    credit_style = (
        f"Style: Credit,{args.font_name},{args.font_size},"
        "&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,"
        "100,100,0,0,1,2,1,5,10,10,10,1"
    )

    lines = content.split("\n")
    out_lines = []
    inserted_style = False
    for line in lines:
        out_lines.append(line)
        if line.strip().lower().startswith("style:") and not inserted_style:
            out_lines.append(credit_style)
            inserted_style = True

    text = r"\N".join(DEFAULT_LINES)

    # Start credit: 0:00:00.00 -> args.seconds
    start_end_time = sec_to_ass_time(args.seconds)
    start_dialogue = f"Dialogue: 1,0:00:00.00,{start_end_time},Credit,,0,0,0,,{text}"

    # End credit: (duration - seconds) -> duration. If the video is
    # shorter than 2x the credit length, skip the end card so it
    # doesn't overlap the intro one.
    end_dialogue = None
    if args.video_duration > (args.seconds * 2):
        end_start = sec_to_ass_time(args.video_duration - args.seconds)
        end_end = sec_to_ass_time(args.video_duration)
        end_dialogue = f"Dialogue: 1,{end_start},{end_end},Credit,,0,0,0,,{text}"

    final_lines = []
    inserted_event = False
    for line in out_lines:
        final_lines.append(line)
        if line.strip().lower().startswith("format:") and "text" in line.lower() and not inserted_event:
            final_lines.append(start_dialogue)
            if end_dialogue:
                final_lines.append(end_dialogue)
            inserted_event = True

    with open(args.ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(final_lines))
        if not final_lines[-1].endswith("\n"):
            f.write("\n")

    if end_dialogue:
        print(f"inject_credits: inserted OnlyKsub credit card at start (0.00s-{args.seconds:.2f}s) "
              f"and end ({args.video_duration - args.seconds:.2f}s-{args.video_duration:.2f}s)")
    else:
        print(f"inject_credits: inserted OnlyKsub credit card at start only "
              f"(video too short for a separate end card, duration={args.video_duration:.2f}s)")


if __name__ == "__main__":
    main()