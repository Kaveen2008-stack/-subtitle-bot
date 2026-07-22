"""
Uploads a file to Abyss.to (Hydrax) using their public upload API.
Usage: python upload_abyss.py <file_path>
Requires: ABYSS_API_KEY environment variable.

API docs: https://blog.abyss.to (up.hydrax.net/<API_KEY>)
"""
import os
import sys
import requests


def main():
    file_path = sys.argv[1]
    api_key = os.environ["ABYSS_API_KEY"]

    url = f"https://up.hydrax.net/{api_key}"
    with open(file_path, "rb") as f:
        resp = requests.post(url, files={"file": f}, timeout=1800)

    resp.raise_for_status()
    data = resp.json()

    if not data.get("status"):
        print(f"Abyss upload failed: {data}", file=sys.stderr)
        sys.exit(1)

    slug = data["slug"]
    embed_url = f"https://abyss.to/{slug}"

    with open("abyss_link.txt", "w") as f:
        f.write(embed_url)

    print(embed_url)


if __name__ == "__main__":
    main()