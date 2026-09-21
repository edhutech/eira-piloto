"""Canonical static schemas for the Google Sheets workbook."""

REQUIRED_SHEETS = [
    "Seguimiento", "Seguimiento individual", "Ranking", "Participantes",
    "Control", "Sesiones", "Programa", "Configuración", "Estado",
]

PROGRAM_HEADERS = [
    "program_id", "program_name", "session_count", "participant_mode",
    "source_provider", "source_ref", "source_url", "output_provider", "output_ref",
    "created_at", "follow_up_ruleset_version",
]
SESSION_HEADERS = [
    "session_number", "session_name", "participant_id", "participant", "email",
    "voice_total", "voice_valid", "chat_total", "chat_valid", "ambiguous_total",
    "score", "scoring_complete", "countability_ruleset_version",
]
PARTICIPANT_HEADERS = [
    "participant_id", "nombre", "correo", "aliases", "role", "source", "status",
    "enrollment_status", "start_session", "end_session",
]
RANKING_HEADERS = [
    "rank", "participant_id", "participant", "email", "sessions_with_activity",
    "voice_total", "voice_valid_total", "chat_total", "chat_valid_total",
    "score_total", "ranking_complete",
]
CONTROL_HEADERS = [
    "session_number", "session_name", "folder_id", "transcript_status",
    "chat_status", "processing_status", "last_processed_at", "tracking_eligible",
]
FOLLOW_UP_HEADERS = [
    "participant_id", "Participante", "Email", "Matrícula", "Sesiones elegibles",
    "Participó", "Frecuencia", "Últimas 4", "Última participación", "Score acumulado",
    "Seguimiento", "Desde", "Motivo", "Nota / Acción", "follow_up_ruleset_version",
]
CLOUD_JSON_HEADERS = ["key", "value_json", "updated_at"]

STATIC_HEADERS = {
    "Programa": PROGRAM_HEADERS,
    "Sesiones": SESSION_HEADERS,
    "Participantes": PARTICIPANT_HEADERS,
    "Ranking": RANKING_HEADERS,
    "Control": CONTROL_HEADERS,
    "Seguimiento individual": FOLLOW_UP_HEADERS,
    "Configuración": CLOUD_JSON_HEADERS,
    "Estado": CLOUD_JSON_HEADERS,
}
