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

## Cloud-first operation

The intended product flow is **agent-operated and Google Workspace first**.
A program does not require a user-managed local workspace.

A normal onboarding starts in conversation:

1. Ask the user for the Google Drive folder where Eira should live.
2. Ask whether there is an existing live roster/attendance source.
3. If there is one, accept the Google Sheet link directly and inspect the
   authorized tabs/columns. Do not ask the user to export it to CSV/XLSX.
4. Ask for the session evidence location(s) and build explicit evidence-source
   references.
5. Ask only for mappings or identity decisions that cannot be established
   deterministically.
6. Initialize/reuse the Eira Google Sheet in the destination folder.
7. Persist program configuration and sync state in the Eira workbook itself.
8. Run `eira-run` from the Drive folder link whenever the program must be
   processed again.

The roster/attendance source and Meet evidence may live elsewhere in Drive.
Eira stores references; they do not need to be copied into the destination
folder.

See [docs/cloud-first-operation.md](docs/cloud-first-operation.md).

## Cloud-first CLI

The agent-facing commands do not require a persistent per-program local
workspace.

Authorize Google once on the execution environment:

    participacion-google-auth --client-secret /path/to/client_secret.json

For onboarding, the agent builds the setup spec in memory and pipes it to Eira:

    cat setup.json | eira-setup \
      --drive-folder "https://drive.google.com/drive/folders/..." \
      --spec - --yes

`configs/cloud_setup.example.json` documents the contract. The JSON file in
this example is only a transport format: an agent can send the same JSON over
stdin without persisting it.

After setup, a new machine or agent only needs the code, Google authorization,
and the Drive folder link:

    eira-run --drive-folder "https://drive.google.com/drive/folders/..."

`eira-run` reloads the authoritative roster, Attendance, aliases, program
configuration, evidence references, and sync state from Google Workspace. It
then runs Participation and the Eira pilot. Repeated runs remain idempotent.

Legacy commands remain available for backwards compatibility:

- `participacion-init`
- `participacion-sync`
- `eira-pilot --config ... --dry-run`

They are not the preferred user-facing cloud-first flow.

## Eira pilot

The experimental pilot is a separate, read-only composition over persisted
Participation results and configured structured sources:

    eira-pilot --config <runtime-config> --dry-run

For normal operation, Attendance should point to the **live Google Sheet** that
the organization already updates after each session. Eira reads its current
values on every run through the generic tabular mapping layer. XLSX remains a
backwards-compatible import/testing source, not the preferred live workflow.

The pilot builds Observations, historical as-of-session snapshots,
deterministic Signals, and Alerts. It does not modify Google Sheets and it does
not invoke the existing sync runner. Use `configs/pilot.example.json` and
`configs/session_mapping.example.json` as client-neutral templates.

Runtime config/cache files may exist on the execution machine, but they are
implementation state rather than a user-facing per-program workspace. Program
evidence and authoritative operational sources should remain in Google
Workspace. `BAJAS` is used only by the separate retrospective evaluation and
never generates a Signal or Alert.

## Configuration and state

For `eira-setup` / `eira-run`, the Eira workbook is authoritative:

- `Configuración` stores the cloud program bundle;
- `Estado` stores deterministic processing state and fingerprints;
- `Participantes` is reconciled from the configured live roster on each run;
- Attendance and explicit email aliases are reread from their live Google
  Sheets on each run.

The local registry/state variables below apply only to the legacy CLI:

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
