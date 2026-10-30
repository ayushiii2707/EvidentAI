"""Runtime configuration from environment variables, with local-development defaults."""

import os

from sqlalchemy.engine import URL, make_url

DEFAULT_OWNER_URL = "postgresql+psycopg://evidentai_owner:owner_dev_only@127.0.0.1:5433/evidentai"
DEFAULT_APP_URL = "postgresql+psycopg://evidentai_app:app_dev_only@127.0.0.1:5433/evidentai"
DEFAULT_TEST_URL = (
    "postgresql+psycopg://evidentai_owner:owner_dev_only@127.0.0.1:5433/evidentai_test"
)
APP_ROLE = "evidentai_app"


def owner_database_url() -> URL:
    """Schema owner. Used for migrations only; bypasses row-level security."""
    return make_url(os.environ.get("EVIDENTAI_DATABASE_URL", DEFAULT_OWNER_URL))


def app_database_url() -> URL:
    """Application role. Subject to row-level security."""
    return make_url(os.environ.get("EVIDENTAI_APP_DATABASE_URL", DEFAULT_APP_URL))


def test_database_url() -> URL:
    return make_url(os.environ.get("EVIDENTAI_TEST_DATABASE_URL", DEFAULT_TEST_URL))
