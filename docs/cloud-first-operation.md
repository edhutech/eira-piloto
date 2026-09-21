# Cloud-first, agent-operated flow

Eira is designed so a program lives in Google Workspace, not in a user-managed
local project folder.

The repository contains the deterministic engine. Program evidence, live roster
and attendance sources, operational output, and the references that connect
them belong in Google Drive / Google Sheets. Local files are implementation
details or backwards-compatible fallbacks; they are not the product workspace.

## Roles

- **Agent**: conversational operator. It asks for missing information, inspects
  authorized Google sources, proposes mappings, and runs Eira commands.
- **Eira engine**: deterministic runtime. It parses evidence, resolves explicit
  identities, computes Participation, builds longitudinal observations, Signals,
  and Alerts.
- **Google Drive**: cloud location chosen by the user for the Eira program
  output.
- **Live source Sheet**: optional external Google Sheet that can contain the
  official roster, email aliases, attendance, or other structured inputs. Eira
  reads it again on every run; it is not downloaded as the authoritative copy.

## Normal onboarding

The user should be able to start with one instruction:

> Configure Eira for a new program.

The agent then asks for the Google Drive folder where Eira should live.

After validating access, the agent asks whether there are additional live
sources, for example an operational Google Sheet containing participants and
attendance. The user supplies links, not exported CSV/XLSX copies.

The agent inspects only the supplied/authorized sources and asks for
confirmation when a mapping cannot be established deterministically.

A normal onboarding conversation is:

1. Ask for the destination Google Drive folder.
2. Ask for the program name and number/order of sessions when they cannot be
   derived safely.
3. Ask for an official roster / attendance source if one exists.
4. Inspect that Google Sheet and identify candidate tabs and columns.
5. Ask for the session evidence location(s) or discover them under the
   authorized Drive scope.
6. Build explicit session evidence sources.
7. Resolve only exact official identities and explicit aliases.
8. Record known external identities explicitly; never infer them.
9. Create or reuse the Eira Google Sheet in the destination folder.
10. Validate the configuration before processing.

The physical evidence and the roster/attendance source do **not** need to be
inside the destination folder. Eira stores references to them.

## Live roster and attendance

A single operational Google Sheet may serve multiple purposes. For example:

    Program Management Sheet
    ├── PARTICIPANTES          -> official roster
    ├── email_aliases          -> explicit identity aliases
    └── class_participants     -> attendance, updated after every session

This is a first-class scenario.

Attendance must be read from the live Google Sheet at execution time:

    Google Sheet (current values)
        -> generic tabular reader
        -> explicit mapping
        -> AttendanceModule

Do not require the user to export the Sheet to XLSX/CSV for routine operation.

The local XLSX reader remains supported as a compatibility/import path, not as
the preferred operational flow.

## Per-session operation

After a session, the agent should be able to run the program from the existing
cloud configuration:

    new/changed Meet evidence
        -> participacion-sync
        -> Participation persisted to Eira Sheet
        -> Control reconciled
        -> read current live attendance source
        -> eira-pilot --dry-run
        -> Observations -> longitudinal analysis -> Signals -> Alerts

Repeated runs with unchanged evidence must remain idempotent.

An incomplete evidence set must remain INCOMPLETE; missing evidence is never
converted to zero participation.

## Local machine responsibilities

A machine or agent runtime may still have:

- a clone/install of this repository;
- Python dependencies;
- Google OAuth credentials;
- internal cache/state required by the current CLI implementation.

Those are runtime infrastructure. They must not become the only authoritative
copy of program data or force the user to create a per-program local folder.

The intended recovery property is:

> Given the Eira code, Google authorization, and the program's cloud references,
> an operator should be able to continue the program without reconstructing a
> client workspace from exported local files.

## Current implementation boundary

The current runtime still uses local registry/state files internally for
one-shot CLI execution. Treat them as implementation state, not as the user
workspace. Moving all operational registry/state into cloud-backed program
metadata is a separate migration and must preserve deterministic behavior and
idempotency.

The cloud-first rule for new features is:

- prefer live Google references over exported files;
- do not require client-specific local folders;
- keep provider concerns in adapters;
- keep Core/Application provider-neutral;
- fail closed on ambiguous identity or invalid configuration;
- preserve provenance;
- do not use LLM inference inside the deterministic runtime.
