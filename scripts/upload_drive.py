"""
Uploads a file to Google Drive and prints the shareable link.
Usage: python upload_drive.py <file_path>
Requires: GDRIVE_SA_JSON (path to service account json), GDRIVE_FOLDER_ID (optional)
"""
import os
import sys

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account


def main():
    file_path = sys.argv[1]
    sa_path = os.environ["GDRIVE_SA_JSON"]
    folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip()

    creds = service_account.Credentials.from_service_account_file(
        sa_path, scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=creds)

    metadata = {"name": os.path.basename(file_path)}
    if folder_id:
        metadata["parents"] = [folder_id]

    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=True)
    request = service.files().create(body=metadata, media_body=media, fields="id, webViewLink")

    response = None
    while response is None:
        status, response = request.next_chunk()

    file_id = response["id"]
    service.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()

    with open("drive_link.txt", "w") as f:
        f.write(response["webViewLink"])
    print(response["webViewLink"])


if __name__ == "__main__":
    main()
