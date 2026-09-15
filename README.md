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

Seguimiento individual longitudinal

Los programas en modo `official` usan una nómina oficial y generan la pestaña
`Seguimiento individual`. La importación de nómina debe ejecutarse primero en
DRY-RUN y solo después, con revisión humana explícita, en APPLY. La
reconciliación usa correo exacto, nombre exacto y alias exacto; las
ambigüedades producen `NEEDS_REVIEW` y nunca se fusionan automáticamente.

En `Seguimiento individual`:

- `●` significa participación registrada en una sesión elegible procesada.
- `○` significa que no existe participación registrada; no significa que la
  persona haya asistido y no haya participado.
- `Normal`, `Observar` y `Crítico` son señales de participación para priorizar
  revisión humana. No son calificaciones, aprobación, sanción, evaluación de
  habilidades ni diagnóstico o predictor de deserción.

Sin una nómina oficial no se genera una clasificación longitudinal para los
programas `auto`; el estado operativo es `WAITING_FOR_OFFICIAL_ROSTER`.
