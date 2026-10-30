"""Alembic environment. Migrations always run as the schema owner role."""

from alembic import context
from sqlalchemy import create_engine, pool

from evidentai.config import owner_database_url
from evidentai.db.models import Base

target_metadata = Base.metadata


def _url():
    # Tests pass an explicit URL via `config.attributes["url"]`; otherwise use the environment.
    return context.config.attributes.get("url") or owner_database_url()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
