class RepositoryError(Exception):
    code = "repository_error"


class NotFound(RepositoryError):
    """The object does not exist *in the caller's workspace*. Objects in other workspaces
    are reported the same way, so callers cannot probe for their existence."""

    code = "not_found"


class Forbidden(RepositoryError):
    code = "forbidden"


class IllegalTransition(RepositoryError):
    code = "illegal_transition"


class ConcurrentModification(RepositoryError):
    """The row's status changed between read and write (another worker got there first)."""

    code = "concurrent_modification"


class VersionPinned(RepositoryError):
    """The version is referenced by an open scope snapshot and cannot be purged yet."""

    code = "version_pinned"
