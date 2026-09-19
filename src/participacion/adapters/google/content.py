from __future__ import annotations

from typing import Any

from ...core.models import ContentArtifact, SourceArtifact


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

    tabs = document.get("tabs")
    if isinstance(tabs, list) and tabs:
        seen: set[str] = set()

        def visit_tab(tab: Any) -> None:
            if not isinstance(tab, dict):
                return
            tab_id = tab.get("tabProperties", {}).get("tabId")
            if isinstance(tab_id, str) and tab_id in seen:
                return
            if isinstance(tab_id, str):
                seen.add(tab_id)
            title = tab.get("tabProperties", {}).get("title", "")
            parts.append(f"[TAB: {title}]\n")
            visit(tab.get("documentTab", {}).get("body", {}))
            for child in tab.get("childTabs", []) or []:
                visit_tab(child)

        for tab in tabs:
            visit_tab(tab)
    else:
        visit(document.get("body", document))
    return normalize_content("".join(parts))


class DriveContentReader:
    """Adapts Drive/Docs resources into parser-ready text or bytes."""

    DOC_MIME = "application/vnd.google-apps.document"

    def __init__(self, drive: Any, docs: Any | None = None):
        self.drive = drive
        self.docs = docs

    def read(self, file: SourceArtifact) -> ContentArtifact:
        if file.media_type == self.DOC_MIME:
            if self.docs is None:
                raise RuntimeError("Se requiere Google Docs API para leer un documento nativo")
            document = self.docs.documents().get(
                documentId=file.artifact_id, includeTabsContent=True
            ).execute()
            text = extract_google_doc_text(document)
            return ContentArtifact(file, text, "text", content_hash(text))

        from io import BytesIO
        from googleapiclient.http import MediaIoBaseDownload
        request = self.drive.files().get_media(fileId=file.artifact_id, supportsAllDrives=True)
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
