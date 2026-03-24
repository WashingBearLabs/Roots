"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from roots import Roots


async def get_roots(request: Request) -> "Roots":
    """Dependency: retrieve the Roots instance from app state."""
    return request.app.state.roots  # type: ignore[no-any-return]
