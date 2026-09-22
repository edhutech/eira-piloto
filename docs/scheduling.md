# Scheduling

The canonical Eira Piloto one-shot command is:

    /path/to/eira-piloto/.venv/bin/eira-run --drive-folder https://drive.google.com/drive/folders/FOLDER_ID

It does not daemonize or poll. Run it manually, from CI, or with the host
scheduler. `participacion-sync` is retained for legacy scheduling only.

## Linux systemd user service

Create a user unit manually with placeholders:

    [Service]
    Type=oneshot
    WorkingDirectory=/path/to/eira-piloto
    Environment=PARTICIPACION_GOOGLE_TOKEN=/path/to/google_token.json
    Environment=PARTICIPACION_NOTIFIER=none
    ExecStart=/path/to/eira-piloto/.venv/bin/eira-run --drive-folder https://drive.google.com/drive/folders/FOLDER_ID

Pair it with a user timer if recurring execution is desired. `notify-send` is
an optional Linux desktop choice; it is not required.

## cron

A cron entry can invoke the installed command and an explicit environment:

    /path/to/eira-piloto/.venv/bin/eira-run --drive-folder https://drive.google.com/drive/folders/FOLDER_ID

## macOS and Windows

Use launchd on macOS or Task Scheduler on Windows to invoke the same installed
`eira-run --drive-folder <URL>` command. Store credentials in the platform's
protected configuration, not in the repository. Manual and CI execution are
equally supported.

## Legacy scheduling

Existing installations may continue to schedule `participacion-sync`; it keeps
the legacy follow-up behavior and is not the cloud-first Eira flow.
