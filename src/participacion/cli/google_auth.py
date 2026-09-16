from __future__ import annotations

import argparse
import sys

from ..adapters.google.auth import authorize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Autoriza acceso OAuth a Google Workspace")
    parser.add_argument("--client-secret", help="ruta al OAuth Desktop client secret")
    parser.add_argument("--token", help="ruta donde guardar el token autorizado")
    args = parser.parse_args(argv)
    try:
        destination = authorize(args.client_secret, args.token)
        print(f"Token OAuth guardado en: {destination}")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
