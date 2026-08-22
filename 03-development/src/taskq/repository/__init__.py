"""taskq.repository — persistence boundary.

[FR-01] Repository is the only layer that touches SQLAlchemy directly
(NFR-06). Citations: SAD.md §4 repository layer; SPEC.md §3 FR-01.
"""
