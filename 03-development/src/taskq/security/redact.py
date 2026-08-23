"""taskq.security.redact — NFR-04 secret-shaped substring scrubber.

[FR-02 / SAD §6 T-10] Subprocess stdout/stderr MAY carry secret-shaped
strings (sk-… tokens, Bearer … JWTs, postgres:// connection URLs).
Before those bytes are persisted to ``task_results.stdout_tail`` /
``stderr_tail`` (FR-07 v3 schema) and surfaced via GET
``/v1/tasks/{id}/runs``, every line MUST be scrubbed via the regex
patterns declared here.

The regex set is intentionally conservative: any token-shaped substring
that *looks like* a credential is replaced with the literal marker
``[REDACTED]``. False positives are an acceptable cost — false
negatives (a real credential that survives persistence) are not.

Layer contract: this module is a leaf helper imported by
``taskq.service.runner._decode`` BEFORE the result dict is handed to
``TaskResultRepository``. It owns NO SQL and depends only on the
standard library so the import cannot fail at runtime.

Lives in ``taskq.security`` rather than ``taskq.errors`` so that
``taskq.api`` (which depends on ``taskq.service``) does not transitively
import ``taskq.errors`` — preserving the
``fr01-config-errors-independence`` import-linter contract.
"""
from __future__ import annotations

# pragma: no error-handling

import re
from typing import Iterable

# Conservative regex set covering the secret-shaped substrings that
# operators worry about in stdout/stderr:
#
#   * ``sk-…`` — OpenAI / Anthropic-style API keys (8+ chars after prefix)
#   * ``Bearer …`` — JWT bearer tokens in HTTP headers / log lines
#   * ``postgres://`` / ``postgresql://`` — DSNs with embedded passwords
#   * ``password=…`` — common ``key=value`` URL/INI leak pattern
#
# All patterns are case-sensitive (the secret prefixes are canonical)
# and applied line-by-line so partial matches inside legitimate text
# still get scrubbed (e.g. a line like ``using sk-abc123 locally``).
_PATTERNS: Iterable[re.Pattern[str]] = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"postgres(?:ql)?://[^\s\"'<>]+"),
    re.compile(r"(?i)\bpassword\s*=\s*[^\s\"'<>]+"),
)

_REDACTION_MARKER = "[REDACTED]"


def redact_text(value: str) -> str:
    """Return ``value`` with every secret-shaped substring replaced.

    Empty / None-ish input returns empty string (defensive — the runner
    hands us raw subprocess bytes that have already been decoded). The
    function is pure (no side effects, no logging) so it is safe to
    call on every line of every task result.
    """
    if not value:
        return ""
    for pattern in _PATTERNS:
        value = pattern.sub(_REDACTION_MARKER, value)
    return value


__all__ = ["redact_text", "_REDACTION_MARKER"]
