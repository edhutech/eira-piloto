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
