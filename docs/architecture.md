# Architecture

The dependency direction is:

    CLI / composition
             |
       Application
          /     \
       Core    Add-ons
          \     /
            Ports
              |
           Adapters

Core contains deterministic domain models and rules and does not know Google,
filesystems, operating systems, or notification implementations. Application
coordinates use cases through neutral contracts. Add-ons extend domain
capability without changing scoring semantics silently. Adapters integrate
providers and infrastructure.

Google Drive, Docs, and Sheets are the current complete provider adapter, not
the architecture. Notification adapters are infrastructure and are not
add-ons. The current add-on is Individual Follow-up. Future source,
persistence, and add-on implementations can be added behind the existing
contracts without changing Core.

The CLI is the composition root. The application is one-shot; an external
scheduler is outside the application.

The shared application ports are `StateStore`, `ContentReader`, and
`SessionResultsStore`. Tracking, notification, parser, roster, and Google
sheet gateways keep use-case-local contracts because they are not shared
application boundaries. Core imports only Core and the standard library;
Application imports Core and Ports; Add-ons do not import adapters; Adapters
do not import the CLI.

The registry accepts legacy bare maps and canonical version 1 documents:

    {"version": 1, "programs": {"<program-id>": {"...": "..."}}}

Legacy reads are never rewritten automatically. Explicit initialization saves
the canonical format atomically.

External structured-data ingestion is kept outside Core. Application owns the
neutral ingestion contracts, mapping, validation, and `CanonicalFact`; tabular
adapters own CSV/XLSX reading. A source-specific JSON configuration selects
physical columns and produces facts identified by external participant and
session references. These facts are transformed into Eira Observations only by
semantic modules. The read-only `eira-pilot` composition consumes persisted
Participation results and mapped structured facts without changing
`participacion-sync`.

The pilot generates one historical snapshot per ordered session. Each snapshot
uses only observations available through its `as_of_session`; future sessions
cannot affect its baseline, recent window, trend, streak, Signal, or Alert.
`RetrospectiveEvaluator` is a separate application component: it compares those
snapshots with normalized outcomes after generation. Outcomes never enter the
SignalEngine or AlertEngine.
