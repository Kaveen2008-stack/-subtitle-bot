"""
Uploads a file to Pixeldrain.
Usage: python upload_pixeldrain.py <file_path>
Optional: PIXELDRAIN_API_KEY environment variable (uploads to your account
instead of anonymously - anonymous uploads may be auto-deleted after a while).
"""
import os
import sys
import base64
import requests


def main():
    file_path = sys.argv[1]
    api_key = os.environ.get("PIXELDRAIN_API_KEY", "")

    if not api_key:
        print(
            "WARNING: PIXELDRAIN_API_KEY is not set - uploading anonymously. "
            "Anonymous Pixeldrain files get auto-deleted after a while, which "
            "is why old download links eventually stop working. Set "
            "PIXELDRAIN_API_KEY as a GitHub Actions secret to fix this.",
            file=sys.stderr,
        )

    url = f"https://pixeldrain.com/api/file/{os.path.basename(file_path)}"
    headers = {}
    if api_key:
        auth = base64.b64encode(f":{api_key}".encode()).decode()
        headers["Authorization"] = f"Basic {auth}"

    with open(file_path, "rb") as f:
        resp = requests.put(url, data=f, headers=headers, timeout=1800)

    resp.raise_for_status()
    data = resp.json()

    if not data.get("success", True) and "id" not in data:
        print(f"Pixeldrain upload failed: {data}", file=sys.stderr)
        sys.exit(1)

    file_id = data["id"]
    # Direct-download API endpoint (?download) instead of the HTML viewer
    # page https://pixeldrain.com/u/{id} - so the link actually downloads
    # the file instead of just opening Pixeldrain's page.
    link = f"https://pixeldrain.com/api/file/{file_id}?download"

    with open("pixeldrain_link.txt", "w") as f:
        f.write(link)

    print(link)


if __name__ == "__main__":
    main()
