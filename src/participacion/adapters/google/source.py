from __future__ import annotations

from typing import Any, Iterable

from ...core.models import (EvidenceContext, EvidenceSourceRef, ResolvedEvidence, SourceArtifact, FileChange, FileStatus,
                             ProgramInspection, ProgramRecord, SessionInspection)
from ...application.ports.evidence import EvidenceResolver
from ...application.registry import select_programs
from ..filesystem.state import file_fingerprint

DRIVE_FILE_FIELDS = "files(id,name,mimeType,modifiedTime,size,md5Checksum,webViewLink)"
DRIVE_ARTIFACT_FIELDS = "id,name,mimeType,modifiedTime,size,md5Checksum,webViewLink"


def list_session_files(drive: Any, folder_id: str) -> list[SourceArtifact]:
    response = drive.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        fields=DRIVE_FILE_FIELDS,
        pageSize=1000,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return [_artifact_from_api(item) for item in response.get("files", [])]


def get_file(drive: Any, file_id: str) -> SourceArtifact:
    item = drive.files().get(
        fileId=file_id,
        fields=DRIVE_ARTIFACT_FIELDS,
        supportsAllDrives=True,
    ).execute()
    return _artifact_from_api(item)


class GoogleDriveEvidenceResolver(EvidenceResolver):
    def __init__(self, drive: Any):
        self.drive = drive

    def resolve(self, sources: tuple[EvidenceSourceRef, ...]) -> list[ResolvedEvidence]:
        return resolve_evidence_sources_with_context(self.drive, sources)


def resolve_evidence_sources(drive: Any, sources: tuple[EvidenceSourceRef, ...]) -> list[SourceArtifact]:
    return [item.artifact for item in resolve_evidence_sources_with_context(drive, sources)]


def resolve_evidence_sources_with_context(drive: Any, sources: tuple[EvidenceSourceRef, ...]) -> list[ResolvedEvidence]:
    artifacts: list[ResolvedEvidence] = []
    contexts: dict[str, EvidenceContext | None] = {}
    for source in sources:
        if source.provider != "google_drive":
            raise ValueError(f"Proveedor de evidencia no soportado: {source.provider}")
        resolved = (list_session_files(drive, source.ref)
                    if source.kind == "container" else [get_file(drive, source.ref)])
        for artifact in resolved:
            context = (EvidenceContext(source.evidence_type)
                       if source.evidence_type is not None else None)
            previous = contexts.get(artifact.artifact_id, ...)
            if previous is not ... and previous != context:
                raise ValueError(f"INVALID: conflicto de evidence_type para artifact_id {artifact.artifact_id}")
            if previous is ...:
                contexts[artifact.artifact_id] = context
                artifacts.append(ResolvedEvidence(artifact, context))
    return artifacts


def _artifact_from_api(value: dict[str, Any]) -> SourceArtifact:
    return SourceArtifact(
        artifact_id=value["id"], name=value.get("name", ""),
        media_type=value.get("mimeType", ""), modified_at=value.get("modifiedTime", ""),
        size=str(value.get("size", "")), checksum=value.get("md5Checksum", ""),
        locator=value.get("webViewLink"), metadata=dict(value),
    )


def _stored_files(state: dict[str, Any], program_id: str, session_id: str) -> dict[str, str]:
    return state.get("programs", {}).get(program_id, {}).get("sessions", {}).get(session_id, {}).get("files", {})


def classify_changes(files: Iterable[SourceArtifact], previous: dict[str, str]) -> list[FileChange]:
    changes = []
    for file in files:
        fingerprint = file_fingerprint({
            "id": file.artifact_id,
            "modifiedTime": file.modified_at,
            "md5Checksum": file.checksum,
            "size": file.size,
        })
        old = previous.get(file.artifact_id)
        status = FileStatus.NEW if old is None else FileStatus.UNCHANGED if old == fingerprint else FileStatus.MODIFIED
        changes.append(FileChange(file=file, status=status, fingerprint=fingerprint, previous_fingerprint=old))
    return changes


def inspect_program(drive: Any, program: ProgramRecord, state: dict[str, Any], program_id: str) -> ProgramInspection:
    inspections = []
    for session in program.sessions:
        sources = session.evidence_sources or (EvidenceSourceRef("google_drive", "container", session.source_ref),)
        resolved = (resolve_evidence_sources_with_context(drive, sources)
                    if session.evidence_sources else
                    [ResolvedEvidence(item, None) for item in resolve_evidence_sources(drive, sources)])
        files = [item.artifact for item in resolved]
        evidence_contexts = {item.artifact.artifact_id: item.evidence_context
                             for item in resolved if item.evidence_context is not None}
        previous = _stored_files(state, program_id, session.state_key)
        inspections.append(SessionInspection(session=session, files=files,
                                             changes=classify_changes(files, previous),
                                             evidence_contexts=evidence_contexts))
    return ProgramInspection(program=program, sessions=inspections)


def select_and_inspect(drive: Any, programs: dict[str, ProgramRecord], state: dict[str, Any], program_id: str | None = None) -> list[ProgramInspection]:
    return [inspect_program(drive, program, state, program.program_id)
            for program in select_programs(programs, program_id)]


def discover_changes(drive: Any, programs: dict[str, ProgramRecord], state: dict[str, Any]) -> list[ProgramInspection]:
    return select_and_inspect(drive, programs, state)
