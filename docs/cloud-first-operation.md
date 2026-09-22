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
3. Ask for an official roster / attendance source if one exists. The source
   is optional; without an official roster Eira uses deterministic auto mode
   over session evidence.
4. Inspect that Google Sheet and identify candidate tabs and columns when a
   source was provided.
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

After a session, the agent runs the program from the existing cloud
configuration:

    new/changed Meet evidence
        -> eira-run --drive-folder <url>
        -> reload live roster
        -> Participation sync
        -> Control reconciled
        -> reload live Attendance and email aliases
        -> Observations -> longitudinal analysis -> Signals -> Alerts

Repeated runs with unchanged evidence must remain idempotent.

An incomplete evidence set must remain INCOMPLETE; missing evidence is never
converted to zero participation.

## Local machine responsibilities

A machine or agent runtime still needs:

- a clone/install of this repository;
- Python dependencies;
- Google OAuth credentials.

Those are runtime infrastructure. A cloud-first program does not require a
persistent local registry, state file, roster copy, Attendance export, or
per-client workspace.

The intended recovery property is:

> Given the Eira code, Google authorization, and the program's cloud references,
> an operator should be able to continue the program without reconstructing a
> client workspace from exported local files.

## Cloud-authoritative persistence

`eira-setup` creates or reuses the Eira workbook and persists two technical
tabs:

- **Configuración** — versioned program bundle: program/session references,
  live roster source, Pilot configuration, explicit known externals and
  identity policy;
- **Estado** — versioned deterministic sync state and evidence fingerprints.

`eira-run` reconstructs the runtime from those tabs. It may create ephemeral
temporary files internally to reuse legacy components, but they are disposable
and never authoritative.

When configured, the live roster is reconciled before Participation
processing. If the source does not declare optional fields such as aliases or
enrollment bounds, Eira preserves the corresponding existing participant
fields rather than erasing them. Without a roster, auto mode derives
participants from session evidence. Attendance and an optional email-alias tab
are reread on every run.

Workbook discovery is based on the versioned Eira bundle in `Configuración`
and its Drive-folder identity, not on an exact filename. A legacy workbook with
a compatible `Participación - <programa>...` title can be adopted during
setup. If more than one valid Eira workbook belongs to the same folder, the
operator must disambiguate with `--sheet`.

Setup/run also reconcile the configured session list into `Control`.
Missing rows are appended with pending state; existing processing state,
timestamps and `tracking_eligible` remain untouched.

The recovery acceptance test is:

1. start on a different machine/runtime;
2. clone/install Eira;
3. authorize Google;
4. provide the program Drive-folder link;
5. run `eira-run`;
6. continue processing without reconstructing client files or local config.

Legacy `participacion-init`, `participacion-sync`, and
`eira-pilot --config` remain available for backwards compatibility.

The cloud-first rule for new features is:

- prefer live Google references over exported files;
- do not require client-specific local folders;
- keep provider concerns in adapters;
- keep Core/Application provider-neutral;
- fail closed on ambiguous identity or invalid configuration;
- preserve provenance;
- do not use LLM inference inside the deterministic runtime.
