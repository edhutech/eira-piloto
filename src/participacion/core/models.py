from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Mapping

EvidenceType = Literal["transcript", "chat"]
Channel = Literal["voice", "chat"]


@dataclass(frozen=True)
class EvidenceContext:
    evidence_type: EvidenceType


def channel_for_evidence_type(evidence_type: EvidenceType) -> Channel:
    return "voice" if evidence_type == "transcript" else "chat"


class FileStatus(str, Enum):
    NEW = "NEW"
    MODIFIED = "MODIFIED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True)
class NormalizedEvent:
    session_number: int
    participant_raw: str
    channel: Literal["voice", "chat"]
    timestamp_raw: str | None
    timestamp_seconds: float | None
    raw_text: str
    text: str
    source_artifact_id: str
    source_locator: str
    event_id: str
    identity_type: Literal["HUMAN", "SYSTEM"] = "HUMAN"
    participant_base_raw: str | None = None


@dataclass(frozen=True)
class DiscardedDocumentEvent:
    participant_raw: str
    source_locator: str
    reason: str


@dataclass(frozen=True)
class ProviderRef:
    provider: str
    ref: str
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class EvidenceSourceRef:
    provider: str
    kind: Literal["container", "artifact"]
    ref: str
    evidence_type: EvidenceType | None = None


@dataclass(frozen=True)
class SessionRecord:
    session_number: int
    session_name: str
    source_ref: str = ""
    session_id: str = ""
    evidence_sources: tuple[EvidenceSourceRef, ...] = ()
    planned: bool = False

    @property
    def folder_id(self) -> str:
        """Legacy state key; the application treats this as source_ref."""
        return self.source_ref

    @property
    def state_key(self) -> str:
        """Stable state key while preserving legacy source_ref keys."""
        return self.source_ref or self.session_id


@dataclass(frozen=True)
class ProgramRecord:
    program_id: str
    program_name: str
    session_count: int
    participant_mode: str
    source: ProviderRef
    output: ProviderRef
    sessions: tuple[SessionRecord, ...]
    known_external_path: str = ""

    @property
    def folder_id(self) -> str:
        return self.source.ref

    @property
    def folder_url(self) -> str:
        return str((self.source.metadata or {}).get("url", ""))

    @property
    def sheet_id(self) -> str:
        return self.output.ref


@dataclass(frozen=True)
class SourceArtifact:
    artifact_id: str
    name: str
    media_type: str = ""
    modified_at: str = ""
    size: str = ""
    checksum: str = ""
    locator: str | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ResolvedEvidence:
    artifact: SourceArtifact
    evidence_context: EvidenceContext | None = None


@dataclass(frozen=True)
class ContentArtifact:
    artifact: SourceArtifact
    content: str | bytes
    content_kind: Literal["text", "binary"]
    content_hash: str


@dataclass(frozen=True)
class ParticipantSnapshot:
    participant_id: str
    participant: str
    email: str = ""


@dataclass(frozen=True)
class FileChange:
    file: SourceArtifact
    status: FileStatus
    fingerprint: str
    previous_fingerprint: str | None = None


@dataclass
class SessionInspection:
    session: SessionRecord
    files: list[SourceArtifact] = field(default_factory=list)
    changes: list[FileChange] = field(default_factory=list)
    evidence_contexts: dict[str, EvidenceContext] = field(default_factory=dict)
    discovery_status: str | None = None
    auto_discovery: bool = False

    @property
    def requires_processing(self) -> bool:
        return bool(not self.files and (self.session.evidence_sources or self.session.source_ref)) or any(
            change.status != FileStatus.UNCHANGED for change in self.changes
        )


@dataclass
class ProgramInspection:
    program: ProgramRecord
    sessions: list[SessionInspection]

    @property
    def sessions_requiring_processing(self) -> list[SessionInspection]:
        return [session for session in self.sessions if session.requires_processing]
