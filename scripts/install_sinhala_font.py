"""
Downloads Noto Sans Sinhala (a variable font) and produces a genuine
static Bold (wght=700) instance for burning subtitles.

Why this exists: downloading the raw variable font file and just naming
it "...-Bold.ttf" does NOT make it bold. Variable fonts ship at their
DEFAULT axis position unless you explicitly instantiate a different one,
and Noto Sans Sinhala's default wght is 400 (Regular). Feeding that file
to libass with ASS "Bold=-1" only gets you libass's synthetic/faux bold
(skewed/thickened outline), which looks noticeably thinner and weaker
than a real bold face - this is why burned subtitles looked "thin" even
though the ASS style requested bold.

This script uses fontTools' variable-font instancer to bake a real
wght=700 static font, then fixes its name table and OS/2/head style bits
so libass (and fontconfig, if libass is built with it) recognize it as
an actual Bold face for the "Noto Sans Sinhala" family - not just a
heavier-looking Regular file.

Usage:
    python install_sinhala_font.py <output_dir>

Writes <output_dir>/NotoSansSinhala-Bold.ttf
"""
import sys
import os
import subprocess
import urllib.request

VARIABLE_FONT_URL = (
    "https://github.com/google/fonts/raw/main/ofl/notosanssinhala/"
    "NotoSansSinhala%5Bwdth%2Cwght%5D.ttf"
)


def download_with_retries(url, dest, attempts=5, delay=3):
    last_err = None
    for i in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as out:
                out.write(resp.read())
            return
        except Exception as e:
            last_err = e
            print(f"  download attempt {i}/{attempts} failed: {e}", file=sys.stderr)
            if i < attempts:
                import time
                time.sleep(delay)
    raise RuntimeError(f"Failed to download font after {attempts} attempts: {last_err}")


def main():
    if len(sys.argv) != 2:
        print("Usage: python install_sinhala_font.py <output_dir>")
        sys.exit(1)

    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    variable_path = os.path.join(out_dir, "_variable_raw.ttf")
    static_path = os.path.join(out_dir, "NotoSansSinhala-Bold.ttf")

    print("Downloading variable font...")
    download_with_retries(VARIABLE_FONT_URL, variable_path)

    from fontTools.ttLib import TTFont

    try:
        f = TTFont(variable_path)
    except Exception as e:
        print(f"ERROR: downloaded file is not a valid font: {e}", file=sys.stderr)
        sys.exit(1)

    if "fvar" not in f:
        # Not actually a variable font (Google changed the file) - it's
        # already static, just use it directly instead of instancing.
        print("Downloaded font is already static, skipping instancing.")
        f.save(static_path)
    else:
        print("Instantiating static Bold (wght=700) from variable font...")
        subprocess.run(
            [
                sys.executable, "-m", "fontTools.varLib.instancer",
                variable_path, "wght=700", "wdth=100",
                "-o", static_path,
            ],
            check=True,
        )

        # Fix name table + style bits so libass/fontconfig recognize this
        # as a real Bold face, not a heavier-looking "Regular".
        sf = TTFont(static_path)
        FAMILY, SUBFAMILY = "Noto Sans Sinhala", "Bold"
        for rec in sf["name"].names:
            if rec.nameID == 1:
                rec.string = FAMILY
            elif rec.nameID == 2:
                rec.string = SUBFAMILY
            elif rec.nameID == 4:
                rec.string = f"{FAMILY} {SUBFAMILY}"
            elif rec.nameID == 6:
                rec.string = "NotoSansSinhala-Bold"
            elif rec.nameID == 16:
                rec.string = FAMILY
            elif rec.nameID == 17:
                rec.string = SUBFAMILY

        sf["head"].macStyle |= 0b01  # bold bit
        os2 = sf["OS/2"]
        os2.usWeightClass = 700
        os2.fsSelection = (os2.fsSelection & ~0b1000000) | 0b00100000  # clear REGULAR, set BOLD
        sf.save(static_path)

    os.remove(variable_path)

    # Sanity check
    check = TTFont(static_path)
    if "fvar" in check:
        print("ERROR: output still a variable font, instancing failed", file=sys.stderr)
        sys.exit(1)
    print(f"OK: wrote static Bold font -> {static_path} "
          f"(weight={check['OS/2'].usWeightClass}, "
          f"subfamily={check['name'].getDebugName(2)})")


if __name__ == "__main__":
    main()