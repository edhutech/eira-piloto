# Contributing

Supported Python versions are 3.10 through the current supported release.
Create a virtual environment and install development dependencies:

    python -m venv .venv
    . .venv/bin/activate
    pip install -e ".[dev,google,xlsx]"

Run focused tests while working, then run the full suite before a pull
request:

    python -m unittest discover -s tests -q

Keep Core provider-neutral. Application may depend on Core and ports, but not
Google SDKs or openpyxl. Adapters contain provider and operating-system details.
Add-ons use generic contracts and must not silently alter Core scoring rules.

Use synthetic identities and reserved example domains in fixtures. Never add
real credentials, OAuth tokens, transcripts, chats, or participant data.
Architecture tests should be updated when a boundary changes.
