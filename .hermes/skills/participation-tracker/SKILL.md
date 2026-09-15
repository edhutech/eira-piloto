---
name: participation-tracker
description: Gestiona el seguimiento determinista de participación en Google Workspace.
version: 0.2.0
author: Project contributor, Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Google Drive, Google Sheets, participación, sesiones]
---

# Participation Tracker

Optional Hermes development context for `participacion-agent`. README.md and
docs/ are the canonical product documentation.

## Boundaries

- Core contains provider-neutral models and deterministic identity,
  countability, scoring, and ranking rules.
- Application orchestrates use cases through generic ports and events.
- Add-ons extend capabilities; Individual Follow-up is the current add-on.
- Adapters contain Google Drive/Docs/Sheets, filesystem, parsers, roster
  sources, and notification implementations.
- Google is the current provider adapter, not the architecture.
- Notifications are event-driven infrastructure, separate from add-ons.
- The runtime is portable and one-shot; external scheduling is optional.

## Current behavior

`participacion-init` validates inputs, shows one plan, asks for one
confirmation, then creates or reuses the seven-sheet workbook: `Seguimiento`,
`Seguimiento individual`, `Ranking`, `Participantes`, `Control`, `Sesiones`,
and `Programa`. It performs no transcript analysis or LLM processing.

`participacion-sync` reads registered programs and processes Google Docs,
DOCX, VTT, SBV, and TXT evidence. Official mode uses strict deterministic
matching; ambiguity is `NEEDS_REVIEW`. `SYSTEM` labels are ignored. Valid
voice counts as 1 and valid chat as 0.5; this is not attendance or grading.

Individual Follow-up requires an official roster and eligible sessions. It
summarizes frequency over the last four sessions as Normal, Observar, or
Crítico, solely as a human-review signal.

## Commands

- `participacion-google-auth` authorizes a Google Desktop OAuth client.
- `participacion-init` bootstraps a program after confirmation.
- `participacion-sync` runs one-shot synchronization.
- Notifications use `none` by default, or `stdout`/`notify-send` explicitly.

Portable configuration uses `PARTICIPACION_CONFIG_DIR`,
`PARTICIPACION_STATE_DIR`, `PARTICIPACION_GOOGLE_TOKEN`,
`PARTICIPACION_GOOGLE_CLIENT_SECRET`, and `PARTICIPACION_NOTIFIER`.

Do not use this skill as a substitute for the product docs. Never use real
credentials or participant data in examples, never assume Google writes are
idempotent without reading first, and never issue destructive operations.