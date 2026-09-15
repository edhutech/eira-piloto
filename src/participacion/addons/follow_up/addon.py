from __future__ import annotations

from typing import Any

from ...application.events import AddonResult, ApplicationEvent, ProgramAddonContext


class IndividualFollowUpAddon:
    addon_id = "individual_follow_up"

    def __init__(self, repository: Any, sessions: Any):
        self.repository = repository
        self.sessions = sessions

    def run(self, context: ProgramAddonContext) -> AddonResult:
        try:
            result = self.repository.refresh(
                self.sessions,
                context.session_statuses,
                official=context.program.participant_mode == "official",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return AddonResult(self.addon_id, errors=(f"{type(exc).__name__}: {exc}",))
        transitions = int(getattr(result, "critical_transitions", 0))
        events = ()
        if transitions:
            events = (ApplicationEvent(
                "follow_up.became_critical",
                context.program.program_id,
                context.program.program_name,
                {"count": transitions},
            ),)
        return AddonResult(
            self.addon_id,
            changed=str(getattr(result, "status", "NOOP")) != "NOOP",
            events=events,
        )
