"""Destructive helpers must refuse anything that is not provably a local *_test database."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from evidentai.config import owner_database_url
from evidentai.db.safety import (
    UnsafeDatabaseError,
    assert_disposable_connection,
    assert_disposable_url,
    recreate_database,
    truncate_all,
)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@127.0.0.1:5433/evidentai",
        "postgresql+psycopg://u:p@127.0.0.1:5433/test_evidentai",
        "postgresql+psycopg://u:p@db.prod.internal:5432/evidentai_test",
        "postgresql+psycopg://u:p@127.0.0.1:5433/",
    ],
)
def test_url_guard_rejects(url):
    with pytest.raises(UnsafeDatabaseError):
        assert_disposable_url(make_url(url))


def test_url_guard_accepts_local_test_database():
    assert_disposable_url(make_url("postgresql+psycopg://u:p@localhost:5433/evidentai_test"))


def test_recreate_refuses_dev_database_before_connecting():
    with pytest.raises(UnsafeDatabaseError):
        recreate_database(owner_database_url())


def test_truncate_refuses_real_connection_to_dev_database(test_url):
    """Connects to the real dev database and proves the guard fires before any TRUNCATE."""
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as conn:
            with pytest.raises(UnsafeDatabaseError):
                truncate_all(conn, ["workspaces"])
            assert conn.scalar(text("SELECT current_database()")) == "evidentai"
    finally:
        engine.dispose()


class _FakeConn:
    """A URL that claims *_test while the server reports another database (alias/proxy)."""

    class engine:  # noqa: N801 - mimics Connection.engine
        url = make_url("postgresql+psycopg://u:p@127.0.0.1:6432/evidentai_test")

    def scalar(self, _):
        return "evidentai"


def test_connection_guard_checks_server_reported_database():
    with pytest.raises(UnsafeDatabaseError, match="connected to 'evidentai'"):
        assert_disposable_connection(_FakeConn())
