"""Guards for destructive operations (drop, create, truncate). Test tooling only.

They fail closed: anything that cannot be proven to be a local, disposable database
named `*_test` is refused, both from the URL and from what the server reports for the
open connection (a URL can point through a proxy or alias to another database).
"""

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import URL

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TEST_SUFFIX = "_test"


class UnsafeDatabaseError(RuntimeError):
    pass


def assert_disposable_url(url: URL) -> None:
    if not url.database or not url.database.endswith(TEST_SUFFIX):
        raise UnsafeDatabaseError(
            f"refusing destructive operation: database {url.database!r} does not end in "
            f"{TEST_SUFFIX!r}"
        )
    if url.host not in LOCAL_HOSTS:
        raise UnsafeDatabaseError(
            f"refusing destructive operation: host {url.host!r} is not a local host"
        )


def assert_disposable_connection(conn: Connection) -> None:
    assert_disposable_url(conn.engine.url)
    actual = conn.scalar(text("SELECT current_database()"))
    if actual != conn.engine.url.database or not str(actual).endswith(TEST_SUFFIX):
        raise UnsafeDatabaseError(
            f"refusing destructive operation: connected to {actual!r}, "
            f"expected {conn.engine.url.database!r}"
        )


def recreate_database(url: URL) -> None:
    """Drop and create the database named in `url` (which must be disposable)."""
    assert_disposable_url(url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            name = url.database
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()


def truncate_all(conn: Connection, tables: list[str]) -> None:
    assert_disposable_connection(conn)
    conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
