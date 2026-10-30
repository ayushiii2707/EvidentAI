"""Document-version lifecycle. The single source of truth for allowed status transitions.

    pending ──► parsing ──► indexing ──► active ──► superseded
       │           │            │          │            │
       │           └──► failed ◄┘          │            │
       │                  │ ▲ retry        │            │
       │                  ▼ └── pending    ▼            ▼
       └────────────────► deleted ◄────────┴────────────┘
                              │
                              ▼
                           purged   (sections/chunks removed; row kept as tombstone)

The migration installs a trigger with the same table, so a status write that skips the
repository (raw SQL, a buggy worker) is still rejected. A test asserts the two agree.
"""

from evidentai.db.models import VersionStatus as S

ALLOWED_TRANSITIONS: dict[S, frozenset[S]] = {
    S.PENDING: frozenset({S.PARSING, S.DELETED}),
    S.PARSING: frozenset({S.INDEXING, S.FAILED}),
    S.INDEXING: frozenset({S.ACTIVE, S.FAILED}),
    S.ACTIVE: frozenset({S.SUPERSEDED, S.DELETED}),
    S.SUPERSEDED: frozenset({S.DELETED}),
    S.FAILED: frozenset({S.PENDING, S.DELETED}),
    S.DELETED: frozenset({S.PURGED}),
    S.PURGED: frozenset(),
}

# Versions whose content may be read by new scope snapshots.
SNAPSHOT_ELIGIBLE = frozenset({S.ACTIVE})
# Versions whose content is still stored (readable by snapshots that already pinned them).
CONTENT_RETAINED = frozenset({S.ACTIVE, S.SUPERSEDED, S.DELETED})


def is_allowed(current: S, target: S) -> bool:
    return target in ALLOWED_TRANSITIONS[current]
