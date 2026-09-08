"""Pure target resolution without session-selection behavior."""

from __future__ import annotations

from .model import Target
from .registry import TargetRegistry


class TargetNotFoundError(LookupError):
    """Raised when a requested target does not exist."""


def resolve_target(
    registry: TargetRegistry,
    target_id: str | None = None,
    *,
    default_target_id: str = "default",
) -> Target:
    """Resolve an explicit target or the configured default, without mutation."""

    resolved_id = target_id if target_id is not None else default_target_id
    try:
        return registry.get(resolved_id)
    except KeyError as exc:
        raise TargetNotFoundError(f"target not found: {resolved_id}") from exc
