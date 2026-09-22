# Scheduling

Eira Pilot is a one-shot application. The canonical command is:

    /path/to/eira-piloto/.venv/bin/eira-run --drive-folder https://drive.google.com/drive/folders/FOLDER_ID

The runtime does not daemonize or poll. Run it manually, from CI, or with the host scheduler. Completion and failure information is written directly to the terminal.

## Linux systemd user service

Example user service:

    [Service]
    Type=oneshot
    WorkingDirectory=/path/to/eira-piloto
    Environment=PARTICIPACION_GOOGLE_TOKEN=/path/to/google_token.json
    ExecStart=/path/to/eira-piloto/.venv/bin/eira-run --drive-folder https://drive.google.com/drive/folders/FOLDER_ID

Pair it with a user timer if recurring execution is needed.

## cron

A cron entry can invoke the installed command directly:

    /path/to/eira-piloto/.venv/bin/eira-run --drive-folder https://drive.google.com/drive/folders/FOLDER_ID

Capture stdout and stderr with the scheduler if you want an execution log.

## macOS and Windows

Use launchd on macOS or Task Scheduler on Windows to invoke the same `eira-run --drive-folder <URL>` command. Store credentials in the platform's protected configuration, not in the repository.

## Legacy scheduling

Existing installations may continue to schedule `participacion-sync`. It keeps the legacy follow-up behavior and is not the canonical cloud-first Eira flow.
