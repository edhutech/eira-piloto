# Scheduling

`participacion-sync` is a one-shot command. It does not daemonize, poll, or
install a scheduler. Run it manually, from CI, or with the host scheduler.

## Linux systemd user service

Create a user unit manually with placeholders:

    [Service]
    Type=oneshot
    WorkingDirectory=/path/to/participacion-agent
    Environment=PARTICIPACION_GOOGLE_TOKEN=/path/to/google_token.json
    Environment=PARTICIPACION_NOTIFIER=none
    ExecStart=/path/to/venv/bin/participacion-sync

Pair it with a user timer if recurring execution is desired. `notify-send` is
an optional Linux desktop choice; it is not required.

## cron

A cron entry can invoke the installed command and an explicit environment:

    PARTICIPACION_GOOGLE_TOKEN=/path/to/google_token.json PARTICIPACION_NOTIFIER=none /path/to/venv/bin/participacion-sync

## macOS and Windows

Use launchd on macOS or Task Scheduler on Windows to invoke the same installed
one-shot command. Store credentials in the platform's protected configuration,
not in the repository. Manual and CI execution are equally supported.
