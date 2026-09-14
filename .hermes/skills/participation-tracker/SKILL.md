---
name: participation-tracker
description: Gestiona el seguimiento de participación en Google Drive.
version: 0.1.0
author: Project contributor, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Google Drive, Google Sheets, participación, sesiones]
    related_skills: []
---

# Participation Tracker Skill

Prepara y mantiene el seguimiento de participación de un programa en Google Drive. La fase `init` prepara la estructura y configuración; la fase `sync` descubre participantes desde transcripts y chats y actualiza los datos. Esta skill no usa el LLM durante `init` y no trata el seguimiento como un registro de asistencia.

## When to Use

- Cuando el usuario quiera inicializar un programa de seguimiento de participación.
- Cuando haya que sincronizar participantes descubiertos en transcripts y chats.
- Cuando haya que mantener sesiones y control de participación en Google Sheets.
- No usar para enviar correos, compartir archivos con terceros ni eliminar archivos.

## Prerequisites

- OAuth de Google Workspace configurado con permisos de Drive y Sheets.
- URL de una carpeta de Google Drive accesible por la cuenta autenticada.
- Para importar participantes: archivo CSV, XLSX o Google Sheet accesible desde Drive.
- La skill de Google Workspace debe estar disponible para operaciones de Drive y Sheets.

## Data Model

- `participant_mode=auto`: durante `init` solo guarda este modo. No busca ni infiere participantes.
- `participant_mode=import`: durante `init` importa una lista proporcionada por el usuario.
- Campos mínimos de participante: `nombre` y `correo`.
- `correo` es el identificador estable cuando está disponible. Si no hay correo, conservar el participante con un identificador provisional y no inventar uno.
- La participación no equivale automáticamente a asistencia. No marcar presencia ni calcular participación durante `init`.
- `sync_state.json` vive en `.participation_tracker/` y es estado runtime excluido de Git.
- La capa de lectura adapta Google Docs nativo a texto y archivos de Drive a texto o bytes según el formato; lectura y parsing son responsabilidades separadas.
- En la fase de parsing, `NormalizedEvent` conserva `session_number`, `participant_raw`, `channel`, `timestamp_raw`, `timestamp_seconds` opcional, `raw_text`, `text` y `source_file_id`.
- Los parsers no asignan `participant_id` ni `countability`; esos campos pertenecen a fases posteriores.
- El contrato de `Participant` incluye `participant_id`, `nombre`, `correo`, `aliases`, `role`, `source` y `status`.
- `role` admite `participant`, `facilitator` u `other`; el valor por defecto es `participant` y no se infiere durante la resolución.
- En `auto`, una identidad HUMAN nueva claramente identificable crea un participante provisional persistible con ID estable, correo vacío, `source=auto` y `status=unverified`.
- En `import`, la lista oficial no se amplía automáticamente: se resuelve por ID existente, correo exacto, alias/nombre `strict` normalizados o una única asociación `loose` secundaria; las asociaciones múltiples quedan en `NEEDS_REVIEW`.
- `strict_name_key` conserva diacríticos; `loose_name_key` los elimina y nunca fusiona automáticamente dos participantes existentes que solo coincidan en loose.
- Los eventos `SYSTEM` se ignoran y nunca crean participantes.
- Countability es una capa posterior separada: solo clasifica eventos HUMAN resueltos como `COUNT`, `NO_COUNT` o `AMBIGUOUS`; no evalúa relevancia, calidad ni pertinencia.
- Cada decisión de countability conserva `event_id`, `status`, `reason_code` y `rule_id`; `NO_COUNT` requiere una regla determinista auditable.
- `COUNTABILITY_RULESET_VERSION = 1`; los `rule_id` son estables y versionados, por ejemplo `greeting_only.v1`, `technical_only.v1` y `substantive_default.v1`.
- No usar longitud como único criterio. Si existe contenido humano no descartable, el default conservador es `COUNT`; `AMBIGUOUS` se reserva para casos excepcionales con contexto indispensable explícitamente ausente.
- La etiqueta general `<nombre>'s Presentation` se marca como identidad `SYSTEM`; su nombre base se conserva como `participant_base_raw` y no crea participante ni participación.
- No inferir roles de `HUMAN` (estudiante, facilitador o presentador) durante parsing.

## Init Procedure

1. Recolectar todos los datos antes de escribir:
   - URL completa de la carpeta de Google Drive.
   - Nombre del programa.
   - Número entero positivo de sesiones.
   - Modo de participantes: `auto` o `import`.
   - Archivo/lista de participantes si el modo es `import`.

2. Validar la carpeta de Drive sin crear ni eliminar elementos temporales:
   - Extraer el ID entre `/folders/` y el siguiente separador.
   - Obtener sus metadatos mediante Drive.
   - Confirmar que el ID corresponde a una carpeta accesible.
   - Comprobar mediante los metadatos de Drive las capacidades/permisos de escritura necesarios, como poder añadir elementos hijos y editar el recurso cuando el backend los exponga.
   - Si no se pueden comprobar capacidades de escritura, informar que la escritura real quedará pendiente de `bootstrap`; no crear una carpeta de prueba.
   - Criterio: la carpeta existe, es accesible y el resultado de capacidades/permisos está registrado.

3. Validar los datos recolectados:
   - Conservar literalmente el nombre del programa.
   - Aceptar únicamente un número de sesiones entero mayor que cero.
   - En modo `auto`, guardar solamente `participant_mode=auto`.
   - En modo `import`, leer la lista y reconocer como mínimo `nombre` y `correo`; presentar errores de filas incompletas sin inventar valores.
   - Criterio: todos los parámetros de init están definidos y la lista importada, si corresponde, está validada.

4. Mostrar un único resumen del plan y pedir una sola confirmación. El resumen debe incluir:
   - Carpeta de destino y su ID.
   - Programa y número de sesiones.
   - Modo de participantes.
   - Archivo/lista y cantidad de participantes, si corresponde.
   - Carpetas y Google Sheet que se crearán o reutilizarán.

5. Tras la confirmación, ejecutar todas las escrituras de init:
   - Leer el nombre actual de la carpeta raíz. Si difiere de `PROGRAM_NAME`, renombrarla a `PROGRAM_NAME` conservando el mismo ID y URL; si ya coincide, no modificarla.
   - Crear o reutilizar las carpetas `01 - Sesión 1`, `02 - Sesión 2`, hasta `NN - Sesión N` bajo la carpeta principal.
   - No duplicar una carpeta existente con el mismo nombre dentro de la carpeta principal.
   - Crear o reutilizar el Google Sheet correspondiente al programa.
   - El Sheet debe contener exactamente estas hojas, en este orden:
     1. `Programa`
     2. `Sesiones`
     3. `Participantes`
     4. `Control`
   - No crear pestañas adicionales.
   - `Programa`: configuración del programa, número de sesiones y `participant_mode`.
   - `Sesiones`: solo encabezados durante init; sus filas representan resultados por participante y sesión y las añadirá `sync`.
   - `Participantes`: como mínimo las columnas `nombre` y `correo`; en modo `auto` queda preparada para sincronización posterior.
   - `Control`: una fila por sesión con `session_number` entero (`1`, `2`, `3`, ...), nombre con padding y carpeta de Drive; no calcular participación durante init.
   - En `Sesiones` y `Control`, `session_number` siempre es entero sin padding. El padding de dos dígitos solo aparece en `session_name` y en el nombre de la carpeta.
   - No analizar transcripts ni chats, no descubrir participantes y no invocar el LLM.
   - Criterio: cada carpeta y el Sheet tienen un ID verificable y no se creó ningún duplicado.

6. Verificar el bootstrap después de escribir:
   - Volver a leer la carpeta principal y comprobar todas las carpetas de sesión esperadas.
   - Leer los metadatos del Sheet y confirmar que está en la carpeta principal.
   - Enumerar sus hojas y confirmar que son exactamente `Programa`, `Sesiones`, `Participantes` y `Control`.
   - Leer encabezados y conteos básicos de cada hoja.
   - Informar cualquier elemento reutilizado o cualquier escritura parcial.

## Sync Procedure

`participacion-sync` debe ejecutarse sin interacción y sincronizar todos los programas registrados. Debe aceptar además una selección opcional de programa para ejecuciones manuales.

1. Leer `.participation_tracker/programs.json` y validar que cada programa tenga carpeta raíz, sesiones y Sheet.
2. Leer o crear `.participation_tracker/sync_state.json`; nunca versionarlo.
3. Inspeccionar las carpetas de sesiones y detectar archivos nuevos o modificados mediante `file_id`, `modifiedTime`, checksum/tamaño y hash de contenido cuando sea necesario.
4. No reprocesar una sesión `PROCESSED` si sus archivos relevantes no cambiaron.
5. Seleccionar parsers independientes principalmente por formato: Google Docs, DOCX, VTT, SBV y TXT. Añadir formatos solo ante un caso real.
6. La capa de lectura obtiene Google Docs mediante Google Docs/Drive y descarga DOCX, VTT, SBV y TXT mediante Drive; devuelve texto o bytes y conserva `file_id` y metadatos.
7. Cada parser produce eventos normalizados con el contrato descrito abajo. Debe identificar transcript `voice` y chat `chat` válidos; si falta uno, marcar la sesión `INCOMPLETE`.
8. Aplicar limpieza determinista antes de resolver participantes.
9. En `auto`, una identidad nueva con nombre determinístico crea/reutiliza un participante provisional con `source=auto`, `status=unverified` y correo vacío. Solo usar `NEEDS_REVIEW` si hay más de una asociación posible.
10. En `import`, resolver únicamente contra la lista oficial. Si no hay asociación determinística, marcar `NEEDS_REVIEW`; nunca inventar una asociación.
11. No convertir timestamps a UTC si el archivo no proporciona una zona horaria confiable.
12. No calcular ranking final ni llamar al LLM. Los casos `AMBIGUOUS` de `countability` permanecen pendientes.
13. Preparar resultados en staging y reemplazar/upsertar únicamente las filas de la sesión procesada después de terminar correctamente.
14. Leer de vuelta Sheets y actualizar `sync_state.json` solo tras verificar la escritura.

### Parser contract

```python
class Parser(Protocol):
    def can_parse(self, file_metadata: FileMetadata) -> bool: ...
    def parse(self, file_metadata: FileMetadata, content: str | bytes, session_number: int) -> ParseResult: ...
```

```python
class NormalizedEvent:
    session_number: int
    participant_raw: str
    channel: Literal["voice", "chat"]
    timestamp_raw: str | None
    timestamp_seconds: float | None
    raw_text: str
    text: str
    source_file_id: str
```

`ParseResult` debe indicar validez, canal, eventos, parser, advertencias y errores. Un parser inválido no produce eventos válidos parciales.

### Processing states

Cada sesión usa exclusivamente estos estados:

`PENDING` → `PROCESSING` → `PROCESSED`

Estados alternativos: `INCOMPLETE`, `NEEDS_REVIEW`, `FAILED`.

- `INCOMPLETE`: falta transcript o chat válido.
- `NEEDS_REVIEW`: existen varias asociaciones posibles o una resolución oficial insuficiente.
- `FAILED`: error técnico o de parser; conserva el último resultado confirmado.

## Idempotency

- Buscar primero por ID y por nombre dentro de la carpeta principal antes de crear recursos.
- Reutilizar carpetas de sesión existentes con el nombre exacto correspondiente.
- Reutilizar el Sheet correspondiente si ya existe; nunca crear otro por una segunda ejecución de `init`.
- Si el Sheet existente carece de una de las cuatro hojas requeridas, corregirlo solo dentro de la única confirmación de init y sin crear hojas adicionales.
- `sync_state.json` registra fingerprints por archivo y sesión; una sesión sin cambios no se reprocesa.
- Actualizar Sheets mediante reemplazo/upsert del conjunto de filas de una sesión, nunca mediante append ciego.
- Si el proceso falla después de escribir Sheets pero antes de guardar el estado, la siguiente ejecución reemplaza/upserta las mismas filas y no duplica resultados.
- No duplicar participantes: en `auto`, reutilizar por nombre/alias determinístico; en `import`, reconciliar por correo contra la lista oficial.
- No modificar los datos confirmados de una sesión hasta que el nuevo procesamiento completo haya terminado correctamente.
- Ante recursos ambiguos o duplicados previos, detenerse y reportar los candidatos en vez de elegir a ciegas.

## Quick Reference

Todas las operaciones se ejecutan mediante la skill `google-workspace` y el `terminal` de Hermes:

- Leer carpeta: `drive get FOLDER_ID`.
- Buscar hijos: `drive search QUERY --raw-query`.
- Crear carpeta: `drive create-folder NAME --parent FOLDER_ID`.
- Crear Sheet: `sheets create --title TITLE --sheet-name Programa`.
- Escribir rangos: `sheets update SHEET_ID RANGE --values JSON`.
- Añadir filas: `sheets append SHEET_ID RANGE --values JSON`.

La interfaz prevista de sync es `participacion-sync` para todos los programas y una opción explícita de selección manual para un solo programa.

Comprobar la ayuda del CLI antes de usar una opción no verificada. No asumir que una operación del CLI es idempotente sin leer primero el estado existente.

## Safety Rules

- Recolectar primero todos los parámetros y pedir una sola confirmación antes de cualquier escritura de init.
- No crear ni eliminar carpetas temporales para validar permisos.
- No eliminar recursos existentes ni compartir archivos.
- No sobrescribir datos existentes sin incluir el cambio en el resumen y la confirmación única.
- Ante un error parcial, detenerse, informar los IDs creados/reutilizados y no reintentar a ciegas.
- En sync, conservar el resultado anterior ante fallos y no marcar `PROCESSED` hasta verificar el upsert completo.

## Verification

Una ejecución solo se considera completada cuando:

- La carpeta principal fue leída y sus capacidades/permisos quedaron comprobados o marcados como pendientes del bootstrap.
- Todas las carpetas de sesión esperadas existen con el formato `NN - Sesión N`.
- Existe un único Sheet correspondiente al programa.
- El Sheet contiene exactamente las cuatro hojas requeridas y en el orden indicado.
- `init` no analizó transcripts ni chats, no calculó participación y no utilizó el LLM.
- Las lecturas posteriores confirman los encabezados, IDs, ubicación y conteos esperados.
- `sync_state.json` no está versionado y los estados/fingerprints se actualizan solo después de verificar Sheets.
