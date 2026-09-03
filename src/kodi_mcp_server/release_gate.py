"""Offline release-readiness checks for source and installed artifacts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
import warnings
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Mount
from starlette.testclient import TestClient

from kodi_mcp_mcp.server_core import SERVER_VERSION
from kodi_mcp_mcp.tool_contract import EXPECTED_TOOL_NAMES
from kodi_mcp_server import __version__
from kodi_mcp_server.http_app import create_base_app
from kodi_mcp_server.remote_mcp_app import create_remote_mcp


_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}


class GateError(RuntimeError):
    """A release-readiness invariant failed."""


def _project_version(project_root: Path) -> str:
    pyproject = project_root / "pyproject.toml"
    try:
        metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        value = metadata["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise GateError(f"cannot read project version from {pyproject}: {exc}") from exc
    if not isinstance(value, str):
        raise GateError(f"project version in {pyproject} is not a string")
    return value


def check_release_identity(
    *,
    project_root: Path,
    expected_version: str,
    expected_sha: str | None = None,
) -> dict[str, str]:
    """Validate source, wheel metadata, imports, API metadata, and MCP identity."""
    project_root = project_root.resolve()
    if not _VERSION_RE.fullmatch(expected_version):
        raise GateError("expected version must be a stable X.Y.Z semantic version")
    if expected_sha is not None and not _SHA_RE.fullmatch(expected_sha):
        raise GateError("expected SHA must be 40 lowercase hexadecimal characters")

    try:
        installed_version = distribution_version("kodi_mcp_server")
    except PackageNotFoundError as exc:
        raise GateError("kodi_mcp_server distribution metadata is unavailable") from exc

    actual = {
        "pyproject": _project_version(project_root),
        "distribution": installed_version,
        "package": __version__,
        "fastapi": create_base_app().version,
        "mcp_server": SERVER_VERSION,
        "future_tag": f"v{expected_version}",
    }
    mismatches = {
        name: value
        for name, value in actual.items()
        if name != "future_tag" and value != expected_version
    }
    if mismatches:
        detail = ", ".join(f"{name}={value!r}" for name, value in sorted(mismatches.items()))
        raise GateError(f"expected version {expected_version!r}; mismatches: {detail}")

    if expected_sha is not None:
        try:
            actual_sha = subprocess.run(
                ["git", "-C", str(project_root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise GateError(f"cannot resolve Git HEAD for {project_root}: {exc}") from exc
        if actual_sha != expected_sha:
            raise GateError(f"expected Git SHA {expected_sha}, found {actual_sha}")

    return actual


def _openapi_operations(app: FastAPI) -> tuple[dict[tuple[str, str], dict[str, Any]], list[warnings.WarningMessage]]:
    app.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = app.openapi()
    operations = {
        (method.upper(), path): operation
        for path, path_item in schema["paths"].items()
        for method, operation in path_item.items()
        if method.upper() in _HTTP_METHODS
    }
    return operations, caught


def check_openapi() -> dict[str, int | bool]:
    """Prove OpenAPI generation, unique IDs, and fresh-generation determinism."""
    from kodi_mcp_server.main import app

    first, first_warnings = _openapi_operations(app)
    second, second_warnings = _openapi_operations(app)
    first_ids = {key: operation["operationId"] for key, operation in first.items()}
    second_ids = {key: operation["operationId"] for key, operation in second.items()}
    duplicate_warnings = [
        item
        for item in [*first_warnings, *second_warnings]
        if "Duplicate Operation ID" in str(item.message)
    ]
    unique_count = len(set(first_ids.values()))
    if unique_count != len(first_ids):
        raise GateError("OpenAPI operation IDs are not unique")
    if duplicate_warnings:
        raise GateError("OpenAPI emitted duplicate-operation-ID warnings")
    if first_ids != second_ids:
        raise GateError("OpenAPI operation IDs are not deterministic")
    if not any(isinstance(route, Mount) and route.path == "/mcp" for route in app.routes):
        raise GateError("StreamableHTTP /mcp mount is missing")
    if not any(isinstance(route, APIRoute) and route.path == "/health" for route in app.routes):
        raise GateError("health route is missing")
    return {
        "operation_count": len(first_ids),
        "unique_operation_ids": unique_count,
        "duplicate_warnings": 0,
        "deterministic": True,
    }


def _sse_payload(response: Any) -> dict[str, Any]:
    if response.status_code != 200:
        raise GateError(f"StreamableHTTP request failed with status {response.status_code}")
    if not response.headers.get("content-type", "").startswith("text/event-stream"):
        raise GateError("StreamableHTTP response was not SSE")
    data_lines = [
        line.removeprefix("data:").strip()
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    if not data_lines:
        raise GateError("StreamableHTTP response contained no SSE data frame")
    return json.loads(data_lines[0])


def check_streamable_http() -> dict[str, Any]:
    """Initialize isolated StreamableHTTP and list the exact public tool contract."""
    remote_app, remote_lifespan = create_remote_mcp()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with remote_lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", remote_app)
    with TestClient(app) as client:
        init = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "release-gate", "version": "1"},
                },
            },
        )
        init_body = _sse_payload(init)
        session_id = init.headers.get("mcp-session-id")
        if not session_id or init_body.get("result", {}).get("serverInfo", {}).get("name") != "kodi-mcp":
            raise GateError("StreamableHTTP initialization contract failed")
        listed = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={
                "mcp-session-id": session_id,
                "mcp-protocol-version": "2025-11-25",
            },
        )
        body = _sse_payload(listed)

    tools = body.get("result", {}).get("tools", [])
    names = [tool.get("name") for tool in tools]
    if any(not isinstance(name, str) for name in names):
        raise GateError("tools/list returned a missing or malformed tool name")
    if len(names) != len(set(names)):
        raise GateError("tools/list returned duplicate tool names")
    actual_names = set(names)
    if actual_names != EXPECTED_TOOL_NAMES:
        raise GateError(
            "tool surface differs from authoritative contract: "
            f"missing={sorted(EXPECTED_TOOL_NAMES - actual_names)}, "
            f"extra={sorted(actual_names - EXPECTED_TOOL_NAMES)}"
        )
    return {
        "tool_count": len(names),
        "unique_tool_count": len(actual_names),
        "tool_names": sorted(actual_names),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-sha")
    args = parser.parse_args(argv)
    expected_version = args.expected_version or _project_version(args.project_root)
    try:
        report = {
            "identity": check_release_identity(
                project_root=args.project_root,
                expected_version=expected_version,
                expected_sha=args.expected_sha,
            ),
            "openapi": check_openapi(),
            "streamable_http": check_streamable_http(),
        }
    except GateError as exc:
        parser.exit(1, f"release gate failed: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
