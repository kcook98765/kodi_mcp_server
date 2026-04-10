"""Importable CLI wrapper module.

The canonical CLI implementation lives in `scripts/kodi_cli.py`, but tests (and
some environments) import `kodi_cli` as a module.

This module provides a stable import surface **and** ensures that patching
`kodi_cli.make_request` in tests affects the underlying implementation.
"""

from __future__ import annotations

from scripts import kodi_cli as _impl

# Keep a reference to the canonical implementation functions so our shim can
# safely redirect calls without creating recursion.
_IMPL_MAKE_REQUEST = _impl.make_request

# Re-export constants so callers/tests can reference them directly.
SERVER_BASE_URL = _impl.SERVER_BASE_URL

EXIT_SUCCESS = _impl.EXIT_SUCCESS
EXIT_INVALID_ARGS = _impl.EXIT_INVALID_ARGS
EXIT_CONNECTION_ERROR = _impl.EXIT_CONNECTION_ERROR
EXIT_SERVER_ERROR = _impl.EXIT_SERVER_ERROR
EXIT_TIMEOUT = _impl.EXIT_TIMEOUT


# Expose these modules for tests that monkeypatch e.g. `kodi_cli.requests.request`.
argparse = _impl.argparse
json = _impl.json
sys = _impl.sys
time = _impl.time
requests = _impl.requests


def make_request(endpoint: str, method: str = "GET", data: dict | None = None):
    """Shim entrypoint.

    By default this calls the canonical implementation function.
    In tests, this function is commonly monkeypatched; we propagate that patch
    into the underlying implementation via _sync_patched_functions().
    """

    return _IMPL_MAKE_REQUEST(endpoint=endpoint, method=method, data=data)


def format_output(data, compact: bool = False) -> str:
    return _impl.format_output(data, compact=compact)


def _sync_patched_functions() -> None:
    """Ensure test monkeypatching of this module affects the implementation."""

    # If tests monkeypatch kodi_cli.make_request, route the implementation's
    # internal `make_request(...)` calls to the patched function.
    _impl.make_request = globals()["make_request"]


def cmd_system_status(args):
    _sync_patched_functions()
    return _impl.cmd_system_status(args)


def cmd_jsonrpc(args):
    _sync_patched_functions()
    return _impl.cmd_jsonrpc(args)


def cmd_addon_info(args):
    _sync_patched_functions()
    return _impl.cmd_addon_info(args)


def cmd_addon_execute(args):
    _sync_patched_functions()
    return _impl.cmd_addon_execute(args)


def cmd_builtin_exec(args):
    _sync_patched_functions()
    return _impl.cmd_builtin_exec(args)


def cmd_log_tail(args):
    _sync_patched_functions()
    return _impl.cmd_log_tail(args)


def cmd_service_status(args):
    _sync_patched_functions()
    return _impl.cmd_service_status(args)


def cmd_service_probe(args):
    _sync_patched_functions()
    return _impl.cmd_service_probe(args)


def cmd_service_ping(args):
    _sync_patched_functions()
    return _impl.cmd_service_ping(args)


def cmd_service_version(args):
    _sync_patched_functions()
    return _impl.cmd_service_version(args)


def cmd_service_health(args):
    _sync_patched_functions()
    return _impl.cmd_service_health(args)


def main():
    _sync_patched_functions()
    return _impl.main()
