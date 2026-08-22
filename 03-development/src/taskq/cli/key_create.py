"""CLI — create a new API key.

[FR-03] Invoked as

    python -m taskq.cli.key_create key create --scope <scope>

with the in-process entry point ``main(argv: list[str]) -> int``.

The plaintext token is generated locally, hashed via ``hash_api_key``
(sha256), persisted as ``key_hash`` (AC-3.2 / AC-3.4), and printed to
stdout exactly once. Nothing else — no logs, no metrics, no error body —
exposes the plaintext (NFR-04).

Citations: SPEC.md §3 FR-03, §8 #18; AC-3.4; NFR-02 / NFR-04 (no
plaintext on the wire / in logs / metrics); SAD.md §4 cli layer.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from typing import Sequence

from taskq.repository.keys import APIKeyRepository, hash_api_key


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taskq.cli.key_create",
        description="Create a new taskq API key.",
    )
    sub = parser.add_subparsers(dest="group", required=True)
    # ``key`` is the SPEC-mandated top-level verb
    # (``python -m taskq.cli.key_create key create --scope <scope>``).
    key = sub.add_parser("key", help="API key operations.")
    key_sub = key.add_subparsers(dest="verb", required=True)
    create = key_sub.add_parser("create", help="Create a new API key.")
    create.add_argument(
        "--scope",
        required=True,
        help="Capability scope for the new key (read / write / admin).",
    )
    return parser


def _generate_plaintext() -> str:
    """Return a fresh, urlsafe-base64 plaintext token (>= 16 chars).

    32 random bytes -> ~43 chars before padding strip.
    """
    return secrets.token_urlsafe(32)


def main(argv: Sequence[str]) -> int:
    """Parse ``argv``, generate a key, persist its hash, print plaintext once.

    Returns 0 on success, 2 on argparse / validation failure.
    """
    parser = _build_parser()
    args = parser.parse_args(list(argv))

    # Hash locally BEFORE we hand anything to the repository so the
    # plaintext never crosses the persistence boundary (NFR-04 / AC-3.4).
    plaintext = _generate_plaintext()
    key_hash = hash_api_key(plaintext)

    APIKeyRepository().create(scope=args.scope, key_hash=key_hash)

    # The plaintext is printed exactly once on stdout.
    sys.stdout.write(plaintext + "\n")
    sys.stdout.flush()
    return 0


__all__ = ["main"]
