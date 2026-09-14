from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FileStatus(str, Enum):
    NEW = "NEW"
    MODIFIED = "MODIFIED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True)
class SessionRecord:
    session_number: int
    session_name: str
    folder_id: str


@dataclass(frozen=True)
class ProgramRecord:
    program_name: str
    folder_id: str
    folder_url: str
    session_count: int
    participant_mode: str
    sheet_id: str
    sessions: tuple[SessionRecord, ...]


@dataclass(frozen=True)
class DriveFile:
    file_id: str
    name: str
    mime_type: str = ""
    modified_time: str = ""
    size: str = ""
    md5_checksum: str = ""
    web_view_link: str = ""

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> "DriveFile":
        return cls(
            file_id=value["id"], name=value.get("name", ""),
            mime_type=value.get("mimeType", ""), modified_time=value.get("modifiedTime", ""),
            size=str(value.get("size", "")), md5_checksum=value.get("md5Checksum", ""),
            web_view_link=value.get("webViewLink", ""),
        )


@dataclass(frozen=True)
class FileChange:
    file: DriveFile
    status: FileStatus
    fingerprint: str
    previous_fingerprint: str | None = None


@dataclass
class SessionInspection:
    session: SessionRecord
    files: list[DriveFile] = field(default_factory=list)
    changes: list[FileChange] = field(default_factory=list)

    @property
    def requires_processing(self) -> bool:
        return any(change.status != FileStatus.UNCHANGED for change in self.changes)


@dataclass
class ProgramInspection:
    program: ProgramRecord
    sessions: list[SessionInspection]

    @property
    def sessions_requiring_processing(self) -> list[SessionInspection]:
        return [session for session in self.sessions if session.requires_processing]
