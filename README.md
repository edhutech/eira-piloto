# participacion-agent

An open-source deterministic CLI application for one-shot participation tracking from session evidence
(voice and chat) into auditable results, cumulative follow-up, and optional
longitudinal signals.

It currently includes a Google Workspace adapter and requires no LLM at
runtime. Supported Python versions are 3.10 through 3.14.

## What it is

- A deterministic participation tracker.
- Provider-neutral Core and Application layers.
- Google Workspace is the currently included source/storage adapter.
- No LLM is required at runtime.

## What it is not

It does not infer attendance, grade, assess learning, or diagnose or predict
dropout. A valid voice or chat event is evidence of participation only.

## How it works

    evidence -> parse -> normalize -> identity -> countability -> score
             -> results -> ranking/tracking -> optional add-ons

Supported evidence formats: Google Docs, DOCX, VTT, SBV, and TXT.

Scoring is intentionally simple: valid voice = 1, valid chat = 0.5, and a
program score is the accumulated sum. There is no relevance or quality metric.

Participant modes are `auto`, `import`, and `official`. Official rosters use
strict deterministic matching and surface ambiguity for human review.

## Follow-up add-on

The optional Individual Follow-up add-on uses an official roster, eligible
sessions, frequency over the last four sessions, and the levels Normal,
Observar, and Crítico. It is a human-review signal, not an assessment or
attendance decision.

## Installation

This project is not published on PyPI. From a clone:

    python -m venv .venv
    . .venv/bin/activate             # Windows: .venv\\Scripts\\activate
    pip install -e ".[google,xlsx]"

For development, install `.[dev,google,xlsx]`.

## Google setup

Create a Desktop OAuth client in a Google Cloud project, enable Google Drive,
Google Sheets, and Google Docs APIs, then authorize:

    participacion-google-auth --client-secret /path/to/client_secret.json

The command stores an authorized-user token at
`~/.config/participacion/google_token.json` by default. Override it with
`--token` or `PARTICIPACION_GOOGLE_TOKEN`. The client secret can also be set
with `PARTICIPACION_GOOGLE_CLIENT_SECRET`. Never commit either file.

## Quick start

1. Run `participacion-google-auth`.
2. Run `participacion-init` and confirm its dry-run plan once.
3. Put or upload session evidence in the prepared folders.
4. Run `participacion-sync`.

If sync reports that the `Participantes` sheet requires migration, run
`participacion-init` and confirm its plan. Planning and cancellation are
read-only; migration occurs only after confirmation.

Commands:

- `participacion-google-auth` — OAuth Desktop installed-app flow.
- `participacion-init` — validate and initialize a program.
- `participacion-sync` — process one or all registered programs.
- `participacion-sync --notifier none|stdout|notify-send` — notification backend.

All commands support `--help` without making Google calls. Google operations
require `participacion-agent[google]`; XLSX roster input requires
`participacion-agent[xlsx]`.

## Eira pilot

The experimental pilot is a separate, read-only composition over the existing
Participation results and a configured structured source:

    eira-pilot --config configs/local/pilot.json --dry-run

It reads persisted Participation results after `participacion-sync`, imports
the configured XLSX/CSV source through the generic reader and mapping, builds
Observations, historical as-of-session snapshots, deterministic Signals, and
Alerts. It does not modify Google Sheets and it does not invoke the existing
sync runner. Use `configs/pilot.example.json` and
`configs/session_mapping.example.json` as client-neutral templates. Real paths,
Google IDs, mappings, source files, outcomes, and pilot results belong under
ignored local paths. `BAJAS` is used only by the separate retrospective
evaluation and never generates a Signal or Alert.

## Configuration and state

- `PARTICIPACION_CONFIG_DIR` — default `~/.config/participacion`.
- `PARTICIPACION_STATE_DIR` — default `~/.local/state/participacion`.
- `PARTICIPACION_GOOGLE_TOKEN` — authorized-user token path.
- `PARTICIPACION_GOOGLE_CLIENT_SECRET` — Desktop client secret path.
- `PARTICIPACION_NOTIFIER` — `none` (default), `stdout`, or `notify-send`.

The application is one-shot. External scheduling is optional; see
[docs/scheduling.md](docs/scheduling.md).

## Architecture

Core contains provider-neutral models and deterministic rules. Application
orchestrates use cases and ports. Add-ons extend capabilities through generic
contracts. Adapters implement Google, filesystem, parser, and notification
integration. See [docs/architecture.md](docs/architecture.md).

## Notifications and privacy

Notification events contain aggregate, non-PII data only. Transcripts and
chats can contain sensitive personal information; users are responsible for
access and retention policies. OAuth tokens are credentials and must be
protected. No LLM processes evidence at runtime.

## Development

    pip install -e ".[dev,google,xlsx]"
    python -m unittest discover -s tests -q

Architecture rules are tested. Do not place provider SDKs, credentials, or
personal data in Core, Application, or fixtures.

## License

Apache-2.0. See [LICENSE](LICENSE).
