# Participation Sync

One-shot synchronization for registered participation programs.

Uso manual:

    participacion-sync

Programa específico:

    participacion-sync --program "Prueba Participación"

Servicio de usuario:

    systemctl --user status participacion-sync.service
    systemctl --user start participacion-sync.service

Logs:

    journalctl --user -u participacion-sync.service

Deshabilitar:

    systemctl --user disable participacion-sync.service

El servicio es `Type=oneshot`: se ejecuta una vez al iniciar la sesión del
usuario y termina. No usa timer, daemon, polling ni cron.
