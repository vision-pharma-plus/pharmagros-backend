"""
Request-scoped context carried in a ContextVar.

Model-layer audit signals need to know *who* performed a change and from
where, but threading a request object down through every service and model
method would couple the domain layer to HTTP. A ContextVar keeps that
plumbing out of the domain code while remaining correct under both threaded
and async execution — unlike threading.local(), which leaks across await
boundaries in ASGI.

Outside a request (Celery task, management command) the context is simply
empty, and audit entries are attributed to the system.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuditContext:
    user: Any = None
    username: str = "system"
    role: str = ""
    ip_address: str | None = None
    user_agent: str = ""
    request_id: str = ""
    extra: dict = field(default_factory=dict)


_ctx: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


def set_context(ctx: AuditContext):
    return _ctx.set(ctx)


def get_context() -> AuditContext:
    return _ctx.get() or AuditContext()


def reset_context(token) -> None:
    _ctx.reset(token)


def clear_context() -> None:
    _ctx.set(None)
