from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .models import DriveFile


@dataclass(frozen=True)
class ContentArtifact:
    file: DriveFile
    content: str | bytes
    content_kind: Literal["text", "binary"]
    content_hash: str


def normalize_content(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def content_hash(content: str | bytes) -> str:
    import hashlib
    value = normalize_content(content) if isinstance(content, str) else content
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def extract_google_doc_text(document: dict[str, Any]) -> str:
    parts: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            text_run = value.get("textRun")
            if isinstance(text_run, dict) and isinstance(text_run.get("content"), str):
                parts.append(text_run["content"])
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document.get("body", document))
    return normalize_content("".join(parts))


class DriveContentReader:
    """Adapts Drive/Docs resources into parser-ready text or bytes."""

    DOC_MIME = "application/vnd.google-apps.document"

    def __init__(self, drive: Any, docs: Any | None = None):
        self.drive = drive
        self.docs = docs

    def read(self, file: DriveFile) -> ContentArtifact:
        if file.mime_type == self.DOC_MIME:
            if self.docs is None:
                raise RuntimeError("Se requiere Google Docs API para leer un documento nativo")
            document = self.docs.documents().get(documentId=file.file_id).execute()
            text = extract_google_doc_text(document)
            return ContentArtifact(file, text, "text", content_hash(text))

        from io import BytesIO
        from googleapiclient.http import MediaIoBaseDownload
        request = self.drive.files().get_media(fileId=file.file_id, supportsAllDrives=True)
        buffer = BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        raw = buffer.getvalue()
        is_docx = file.name.casefold().endswith(".docx")
        if is_docx:
            return ContentArtifact(file, raw, "binary", content_hash(raw))
        text = raw.decode("utf-8-sig")
        text = normalize_content(text)
        return ContentArtifact(file, text, "text", content_hash(text))
