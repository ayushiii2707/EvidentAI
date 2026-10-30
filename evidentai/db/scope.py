"""Workspace-scoped transactions: the only supported way to read or write tenant data.

`workspace_transaction` does three things, in one database transaction:
1. sets the transaction-local `app.workspace_id`, which row-level security policies read;
2. checks that the user is a member of that workspace (and fails closed if not);
3. hands out a repository bound to that scope.

The setting is transaction-local (`set_config(..., true)`), so a pooled connection does
not carry one request's workspace into the next. See docs/data-model.md for what this
does and does not protect against.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Connection, Engine, func, insert, select

from evidentai.db.errors import Forbidden
from evidentai.db.models import Membership, Workspace

if TYPE_CHECKING:
    from evidentai.db.repository import WorkspaceRepository

memberships = Membership.__table__
workspaces = Workspace.__table__


@dataclass(frozen=True)
class WorkspaceScope:
    """Proof that `user_id` was found to be a member of `workspace_id` in this transaction.

    Python cannot stop other code from constructing one directly; repositories therefore
    also rely on row-level security, which reads the database setting, not this object.
    """

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: str


def set_workspace(conn: Connection, workspace_id: uuid.UUID) -> None:
    conn.execute(select(func.set_config("app.workspace_id", str(workspace_id), True)))


@contextmanager
def workspace_transaction(
    engine: Engine, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> Iterator["WorkspaceRepository"]:
    from evidentai.db.repository import WorkspaceRepository

    with engine.begin() as conn:
        set_workspace(conn, workspace_id)
        role = conn.scalar(
            select(memberships.c.role).where(
                memberships.c.workspace_id == workspace_id, memberships.c.user_id == user_id
            )
        )
        if role is None:
            raise Forbidden(f"user {user_id} is not a member of workspace {workspace_id}")
        yield WorkspaceRepository(conn, WorkspaceScope(workspace_id, user_id, role))


def create_workspace(engine: Engine, owner_user_id: uuid.UUID, name: str) -> uuid.UUID:
    """Create a workspace with `owner_user_id` as its owner. The id is generated here so the
    transaction can scope itself to the new workspace before inserting it."""
    workspace_id = uuid.uuid4()
    with engine.begin() as conn:
        set_workspace(conn, workspace_id)
        conn.execute(insert(workspaces).values(id=workspace_id, name=name))
        conn.execute(
            insert(memberships).values(
                workspace_id=workspace_id, user_id=owner_user_id, role="owner"
            )
        )
    return workspace_id
