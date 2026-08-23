"""taskq.security — NFR-04 secret-shaped content helpers.

Leaf package of pure (stdlib-only) helpers that scrub sensitive
substrings before they reach persistence or logs. Lives outside
``taskq.errors`` so the ``fr01-config-errors-independence`` contract
between ``taskq.api`` and ``taskq.errors`` is not transitively violated
through ``taskq.service.runner``.
"""
