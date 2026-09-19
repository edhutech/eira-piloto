---
name: eira-piloto
description: Opera el piloto determinista de señales tempranas.
version: 0.1.0
author: Project contributor, Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Eira, pilot, participation, attendance, signals, alerts]
    related_skills: []
---

# Eira Piloto

Eira Piloto es un sistema experimental de señales tempranas para revisión humana.
No es un predictor de deserción, no diagnostica personas y no usa un modelo de
probabilidad para decidir alertas. README.md y docs/ son la documentación
canónica del producto; este Skill describe la operación del repositorio.

## Arquitectura

```text
fuentes → readers/adapters → CanonicalFact → Modules → Observation
        → Longitudinal Core → SignalEngine → AlertEngine → revisión humana
```

Participation y Attendance son dimensiones distintas. El Core longitudinal es
neutral y los Signals/Alerts son deterministas, explicables, explícitos y
versionados.

## Principios no negociables

- Participation != Attendance.
- No participation != absence.
- `NO_DATA` != cero.
- `NOT_APPLICABLE` != `NO_DATA`.
- Una fila ausente != ausencia.
- La identidad ambigua o no encontrada produce `NEEDS_REVIEW`.
- Toda Observation conserva provenance.
- El Core no conoce proveedores, XLSX, CSV, Google ni clientes.
- Los readers solo leen estructura física; el mapping es explícito.
- El agente no inventa mappings ni hace matching fuzzy silencioso.
- Las reglas de Signal y Alert tienen thresholds y versiones explícitos.
- El agente no decide que una persona desertará.
- `BAJAS` nunca entra en el AlertEngine ni en los snapshots históricos.
- No convertir estados faltantes o incompletos en cero.
- No realizar escrituras destructivas ni persistir PII en el repositorio.

## Operación legacy

Autoriza Google con:

```text
participacion-google-auth --client-secret /ruta/local/client_secret.json
```

Ejecuta el flujo existente sin modificarlo:

```text
participacion-sync
```

`participacion-sync` procesa Participation, persiste sus resultados y conserva
sus reglas actuales. No convertirlo en Eira ni ejecutar el flujo nuevo dentro
de `ProgramRunner`.

## Operación del piloto

El piloto consume en modo read-only los resultados persistidos de Participation
y una fuente tabular configurada:

```text
eira-pilot --config configs/local/pilot.json --dry-run
```

La configuración local puede incluir rutas, IDs de Google, hojas, columnas y
mappings de sesiones. Usa como base:

```text
configs/pilot.example.json
configs/session_mapping.example.json
```

Los archivos reales deben estar bajo `configs/local/`, que está ignorado por
Git. Attendance siempre sigue:

```text
reader genérico → mapping → CanonicalFact → AttendanceModule
```

No crear adapters con nombres de clientes ni copiar archivos reales al repo.

## Sesiones e identidad

La identidad externa se resuelve contra el roster oficial mediante coincidencia
exacta y determinista. Nunca se crean participantes nuevos durante el piloto.

La sesión externa debe mapearse explícitamente a:

```text
external_session_id → internal session_id → session_order
```

No asumir que un `meeting_code`, una fecha aislada o el formato de un ID
identifican una sesión.

La applicability usa únicamente campos administrativos explícitos del roster,
como `enrollment_status`, `start_session` y `end_session`. Si no son confiables,
el resultado es `UNKNOWN` y `NEEDS_REVIEW`.

## Signals y Alerts

`pilot.v1` es experimental y no está validado científicamente. La configuración
inicial activa únicamente:

```text
participation_silence_streak
metric = participation_score
minimum_streak = 2
→ OBSERVAR
```

`OBSERVED(0)` cuenta para el streak. `NO_DATA`, `INCOMPLETE` y
`NOT_APPLICABLE` no cuentan. Participation decline, Attendance signals y
recovery permanecen desactivados hasta que exista una decisión explícita.

`NORMAL` solo representa una evaluación suficiente sin regla activada.
`INSUFFICIENT_DATA` es un estado separado y usa `level = None`.

## Dry-run y resultados

El dry-run debe informar de forma agregada:

- CanonicalFacts válidos e issues por categoría;
- identidades resueltas, no encontradas y ambiguas;
- sesiones resueltas y pendientes de revisión;
- Observations por módulo y estado;
- análisis longitudinales;
- cantidad de snapshots históricos, primera y última sesión;
- participantes con evaluación suficiente por sesión;
- Signals por tipo;
- Alerts por nivel y `INSUFFICIENT_DATA`.

No imprimir nombres, emails, filas completas, tokens ni transcripciones.
Los resultados explicables incluyen Signal, evidencia, regla, versión y
provenance mínimo.

## Evaluación retrospectiva

`RetrospectiveEvaluator` es independiente del `PilotRunner`. Consume snapshots
históricos y outcomes normalizados. Cada snapshot se calcula solo con datos
hasta su `as_of_session`; nunca usa sesiones futuras para baseline, ventanas,
trend, streak, Signal o Alert.

`BAJAS` solo se incorpora después de generar snapshots y únicamente para medir:

- cobertura retrospectiva;
- anticipación observada;
- casos con señal o alerta previa;
- casos sin detección previa;
- alertas sin outcome posterior;
- datos insuficientes.

No usar `accuracy`, `precision` predictiva ni `probability`, y nunca ajustar
thresholds automáticamente con outcomes.

## Comportamiento del agente

El agente puede inspeccionar fuentes estructurales, proponer mappings,
ejecutar readers y comandos existentes, explicar Signals/Alerts y preparar
contexto para revisión humana.

El agente no puede:

- inventar equivalencias de columnas;
- seleccionar silenciosamente una métrica ambigua;
- modificar thresholds sin aprobación;
- convertir faltantes en cero;
- usar outcomes para generar alertas;
- escribir Google Sheets durante el dry-run;
- versionar credenciales, PII, archivos reales o resultados del piloto;
- hacer commit sin autorización explícita.
