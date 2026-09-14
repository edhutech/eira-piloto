from __future__ import annotations

from typing import Any, Iterable

from .models import DriveFile, FileChange, FileStatus, ProgramInspection, ProgramRecord, SessionInspection
from .registry import select_programs
from .state import file_fingerprint

DRIVE_FILE_FIELDS = "files(id,name,mimeType,modifiedTime,size,md5Checksum,webViewLink)"


def list_session_files(drive: Any, folder_id: str) -> list[DriveFile]:
    response = drive.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        fields=DRIVE_FILE_FIELDS,
        pageSize=1000,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return [DriveFile.from_api(item) for item in response.get("files", [])]


def _stored_files(state: dict[str, Any], program_id: str, session_id: str) -> dict[str, str]:
    return state.get("programs", {}).get(program_id, {}).get("sessions", {}).get(session_id, {}).get("files", {})


def classify_changes(files: Iterable[DriveFile], previous: dict[str, str]) -> list[FileChange]:
    changes = []
    for file in files:
        fingerprint = file_fingerprint({
            "id": file.file_id,
            "modifiedTime": file.modified_time,
            "md5Checksum": file.md5_checksum,
            "size": file.size,
        })
        old = previous.get(file.file_id)
        status = FileStatus.NEW if old is None else FileStatus.UNCHANGED if old == fingerprint else FileStatus.MODIFIED
        changes.append(FileChange(file=file, status=status, fingerprint=fingerprint, previous_fingerprint=old))
    return changes


def inspect_program(drive: Any, program: ProgramRecord, state: dict[str, Any], program_id: str) -> ProgramInspection:
    inspections = []
    for session in program.sessions:
        files = list_session_files(drive, session.folder_id)
        previous = _stored_files(state, program_id, session.folder_id)
        inspections.append(SessionInspection(session=session, files=files, changes=classify_changes(files, previous)))
    return ProgramInspection(program=program, sessions=inspections)


def select_and_inspect(drive: Any, programs: dict[str, ProgramRecord], state: dict[str, Any], program_id: str | None = None) -> list[ProgramInspection]:
    return [inspect_program(drive, program, state, program.folder_id)
            for program in select_programs(programs, program_id)]


def discover_changes(drive: Any, programs: dict[str, ProgramRecord], state: dict[str, Any]) -> list[ProgramInspection]:
    return select_and_inspect(drive, programs, state)
