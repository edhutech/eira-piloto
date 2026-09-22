# Contributing

The package declares compatibility with Python 3.10 and newer. Pilot CI
currently validates Python 3.12 on Ubuntu; other supported Python versions are
not continuously tested by CI.
Create a virtual environment and install development dependencies:

    python -m venv .venv
    . .venv/bin/activate
    pip install -e ".[dev,google,xlsx]"

Run focused tests while working, then run the full suite before a pull
request:

    python -m unittest discover -s tests -q
    ruff check src tests
    mypy
    python -m compileall -q src/participacion

Keep Core provider-neutral. Application may depend on Core and ports, but not
Google SDKs or openpyxl. Adapters contain provider and operating-system details.
Add-ons use generic contracts and must not silently alter Core scoring rules.

Use synthetic identities and reserved example domains in fixtures. Never add
real credentials, OAuth tokens, transcripts, chats, or participant data.
Architecture tests should be updated when a boundary changes.

Mypy checks Core, the stable registry and shared-port contracts, plus the cloud
orchestration modules `src/participacion/cli/cloud.py`, `cloud_store.py`, and
`control.py` included by the project configuration.
