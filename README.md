# Eira Pilot

Eira Pilot is an experimental, deterministic, and auditable early-signal system for human review. It uses observable participation evidence and structured operational sources to build longitudinal history, Signals, and Alerts without turning missing data into risk or predicting dropout.

No LLM is required at runtime.

## What Eira does

Eira turns program evidence into explainable operational signals:

```text
Evidence
  -> Participation
  -> Observations
  -> Longitudinal analysis
  -> SignalEngine
  -> AlertEngine
  -> OperationalView
  -> Google Sheets derived views
  -> Human review
```

Participation and Attendance are separate dimensions. No participation does not mean absence, and missing evidence is never converted into zero participation.

## What Eira does not do

Eira does not:

- predict dropout;
- diagnose students;
- infer attendance from participation;
- grade learning;
- assign probabilities or risk scores;
- use fuzzy or LLM-based identity matching inside the deterministic runtime.

## Alerts

The alert module is active and connected to the cloud-first flow.

`PilotRunner` sends longitudinal analyses to `SignalEngine`, then sends the resulting Signal set to `AlertEngine`. The latest operational snapshot is converted into `OperationalCase` records and persisted to the derived Google Sheets views.

The current `pilot.v1` policy enables only:

```text
participation_silence_streak
minimum_streak = 2
-> OBSERVAR
```

`NORMAL` means that the required evaluation had enough data and no alert rule was activated. `INSUFFICIENT_DATA` remains separate. The legacy `CRÍTICO` follow-up logic belongs to `participacion-sync` and is not part of the canonical `eira-run` alert flow.

## Cloud-first operation

The intended operating model is agent-operated and Google Workspace first. A program does not require a persistent local client workspace.

A normal onboarding flow is:

1. Provide the Google Drive folder where Eira should live.
2. Provide an optional live roster and Attendance Google Sheet.
3. Provide session evidence references or authorized Drive containers.
4. Resolve only deterministic mappings and identity decisions.
5. Run `eira-setup`.
6. Run `eira-run` whenever new or changed evidence must be processed.

The roster, Attendance source, aliases, and evidence may live outside the Eira destination folder. Eira stores references to those sources.

After setup, the authoritative program configuration and processing state live in the Eira workbook:

- `Configuración` stores the versioned cloud bundle.
- `Estado` stores deterministic processing state and fingerprints.
- `Participantes` contains the reconciled participant view.
- `Control` contains session processing state.
- `Seguimiento` and `Seguimiento individual` are derived operational views, not sources of truth.

A new machine only needs the code, Google authorization, and the program Drive-folder link.

## Installation

This project is not published on PyPI. Install it from a clone:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[google,xlsx]"
```

For development:

```bash
pip install -e ".[dev,google,xlsx]"
```

## Google authorization

Create a Desktop OAuth client in a Google Cloud project and enable the Google Drive, Google Sheets, and Google Docs APIs.

Authorize once on the execution environment:

```bash
participacion-google-auth --client-secret /path/to/client_secret.json
```

The authorized-user token is stored at `~/.config/participacion/google_token.json` by default. Override it with `--token` or `PARTICIPACION_GOOGLE_TOKEN`. Never commit OAuth tokens or client secrets.

## Canonical commands

Create or update the cloud configuration:

```bash
cat setup.json | eira-setup \
  --drive-folder "https://drive.google.com/drive/folders/..." \
  --spec - --yes
```

`configs/cloud_setup.example.json` documents the setup contract. The JSON file is only a transport format; an agent can send the same JSON through stdin without persisting it.

Run the program:

```bash
eira-run --drive-folder "https://drive.google.com/drive/folders/..."
```

If more than one valid Eira workbook exists in the same folder, use `--sheet` to select the intended workbook.

## Terminal results

Eira uses the terminal as its runtime feedback channel. Desktop notifications are not part of the pilot runtime.

At the end of a successful run, the CLI prints an explicit `RESULT: SUCCESS` message. When a run fails, it prints `RESULT: ERROR` together with the error type and message. Session-level warnings and errors are printed with the session summary so failures remain visible in the same execution context.

Unexpected programming errors are not silently swallowed; they are surfaced to the terminal for debugging.

## Evidence and sessions

Supported evidence formats include Google Docs, DOCX, VTT, SBV, and TXT.

Scoring remains intentionally simple:

- valid voice event = 1;
- valid chat event = 0.5.

A future session may be configured as `planned` without evidence. A planned session with no source does not advance the operational `as_of` session. Evidence that is present but incomplete remains `INCOMPLETE`; it is never converted to zero participation.

Container discovery is deterministic. Ambiguous evidence requires human review rather than an automatic guess.

## Attendance and identity

Attendance is read from the live configured source on every run:

```text
Google Sheet
  -> explicit mapping
  -> CanonicalFact
  -> AttendanceModule
  -> Observation
```

Identity resolution is deterministic and fail-closed. Ambiguous or unresolved identities become review cases. Auto mode can operate without a roster for Participation, but Attendance requires a resolvable deterministic identity path.

## Legacy compatibility

These commands remain available for backwards compatibility:

- `participacion-init`
- `participacion-sync`
- `eira-pilot --config ... --dry-run`

They are not the preferred cloud-first flow.

The legacy follow-up add-on remains isolated from `eira-run`.

## Scheduling

Eira is a one-shot application. Scheduling is external to the application.

See [docs/scheduling.md](docs/scheduling.md).

## Development

The pilot keeps a deliberately lean regression suite focused on core scoring, identity, evidence parsing, cloud orchestration, Signals/Alerts, and operational persistence.

Run:

```bash
python -m unittest discover -s tests -q
ruff check src tests
mypy
python -m compileall -q src/participacion
python -m build
```

## Privacy and security

Transcripts and chats may contain personal data. Keep program evidence and operational sources in authorized Google Workspace locations and apply appropriate access, retention, and deletion policies.

The repository must not contain real participant data, OAuth tokens, client secrets, transcripts, chats, or client-specific exports.

See [SECURITY.md](SECURITY.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
