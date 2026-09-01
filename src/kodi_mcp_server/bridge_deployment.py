"""Verification contract shared by bridge deployment tooling and tests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable


class BridgeDeploymentMismatch(RuntimeError):
    """Raised when the running bridge does not match the intended build."""


@dataclass(frozen=True)
class DeploymentObservation:
    bridge_version: str | None
    addon_version: str | None
    bridge_healthy: bool
    jsonrpc_healthy: bool
    gui_state_ok: bool
    build_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_expected_bridge(
    expected_version: str,
    observation: DeploymentObservation,
    *,
    expected_build_fingerprint: str | None = None,
) -> DeploymentObservation:
    problems: list[str] = []
    if observation.bridge_version != expected_version:
        problems.append(f"bridge reported {observation.bridge_version!r}")
    if observation.addon_version != expected_version:
        problems.append(f"Kodi addon metadata reported {observation.addon_version!r}")
    if (
        expected_build_fingerprint is not None
        and observation.build_fingerprint != expected_build_fingerprint
    ):
        problems.append(
            "build fingerprint reported "
            f"{observation.build_fingerprint!r}, expected {expected_build_fingerprint!r}"
        )
    if not observation.bridge_healthy:
        problems.append("bridge health failed")
    if not observation.jsonrpc_healthy:
        problems.append("JSON-RPC health failed")
    if not observation.gui_state_ok:
        problems.append("GUI state failed")
    if problems:
        raise BridgeDeploymentMismatch(
            f"expected {expected_version}; " + "; ".join(problems)
        )
    return observation


@dataclass(frozen=True)
class DeploymentResult:
    action: str
    expected_version: str
    before: DeploymentObservation
    after: DeploymentObservation
    observations: tuple[DeploymentObservation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "expected_version": self.expected_version,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "observations": [item.to_dict() for item in self.observations],
        }


async def deploy_expected_bridge(
    expected_version: str,
    *,
    probe: Callable[[], Awaitable[DeploymentObservation]],
    install: Callable[[], Awaitable[None]],
    attempts: int = 30,
    poll_interval_seconds: float = 1.0,
    expected_build_fingerprint: str | None = None,
) -> DeploymentResult:
    import asyncio

    before = await probe()
    try:
        verify_expected_bridge(
            expected_version,
            before,
            expected_build_fingerprint=expected_build_fingerprint,
        )
    except BridgeDeploymentMismatch:
        pass
    else:
        return DeploymentResult(
            action="already_current",
            expected_version=expected_version,
            before=before,
            after=before,
            observations=(before,),
        )

    action = (
        "bootstrap"
        if before.bridge_version is None and before.addon_version is None
        else "upgrade"
    )
    await install()

    observations: list[DeploymentObservation] = []
    last_error: BridgeDeploymentMismatch | None = None
    for attempt in range(max(1, attempts)):
        observation = await probe()
        observations.append(observation)
        try:
            verify_expected_bridge(
                expected_version,
                observation,
                expected_build_fingerprint=expected_build_fingerprint,
            )
        except BridgeDeploymentMismatch as exc:
            last_error = exc
            if attempt + 1 < max(1, attempts):
                await asyncio.sleep(max(0.0, poll_interval_seconds))
            continue
        return DeploymentResult(
            action=action,
            expected_version=expected_version,
            before=before,
            after=observation,
            observations=tuple(observations),
        )

    raise BridgeDeploymentMismatch(
        f"deployment did not converge after {max(1, attempts)} probe(s): {last_error}"
    )
