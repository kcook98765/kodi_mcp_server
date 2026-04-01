#!/usr/bin/env python3
"""
Kodi MCP CLI Wrapper

Thin CLI layer that calls the backend server.
- Deterministic, JSON-only output
- No retry logic, no business logic duplication
- Strict input validation
- Standardized exit codes
"""

import argparse
import json
import sys
import time
from urllib.parse import urljoin
from typing import Any

import requests

SERVER_BASE_URL = "http://localhost:8000"

EXIT_SUCCESS = 0
EXIT_INVALID_ARGS = 1
EXIT_CONNECTION_ERROR = 2
EXIT_SERVER_ERROR = 3
EXIT_TIMEOUT = 4


def make_request(endpoint: str, method: str = "GET", data: dict | None = None) -> tuple[dict | None, int, str]:
    """
    Make HTTP request to backend server.
    
    Returns:
        (result_dict, exit_code, error_message)
    """
    url = urljoin(SERVER_BASE_URL + "/", endpoint)
    
    try:
        response = requests.request(
            method=method,
            url=url,
            json=data if method == "POST" else None,
            params=data if method == "GET" and data else None,
            timeout=30,
        )
        
        try:
            result = response.json()
        except json.JSONDecodeError:
            return None, EXIT_SERVER_ERROR, f"Invalid JSON response: {response.text[:200]}"
        
        if response.status_code != 200:
            error_msg = result.get("error", f"HTTP {response.status_code}")
            return None, EXIT_SERVER_ERROR, error_msg
        
        return result, EXIT_SUCCESS, ""
        
    except requests.exceptions.ConnectionError:
        return None, EXIT_CONNECTION_ERROR, "Failed to connect to server"
    except requests.exceptions.Timeout:
        return None, EXIT_TIMEOUT, "Request timeout"
    except Exception as e:
        return None, EXIT_SERVER_ERROR, str(e)


def format_output(data: Any, compact: bool = False) -> str:
    """Format output for CLI consumption."""
    if compact:
        return json.dumps(data, separators=(',', ':'))
    return json.dumps(data, indent=2, default=str)


def cmd_system_status(args: argparse.Namespace) -> int:
    """Get server/system status."""
    result, exit_code, error = make_request("/status")
    
    if exit_code != EXIT_SUCCESS:
        output = {
            "ok": False,
            "command": "system status",
            "error": error,
        }
        print(format_output(output, args.compact))
        return exit_code
    
    output = {
        "ok": True,
        "command": "system status",
        "data": result,
    }
    
    print(format_output(output, args.compact))
    return EXIT_SUCCESS


def cmd_jsonrpc(args: argparse.Namespace) -> int:
    """Execute a JSON-RPC command via execute_jsonrpc tool."""
    if not args.method:
        output = {
            "ok": False,
            "command": "jsonrpc call",
            "error": "Missing required argument: --method",
        }
        print(format_output(output, args.compact))
        return EXIT_INVALID_ARGS
    
    result, exit_code, error = make_request(
        "/tools/execute_jsonrpc",
        method="GET",
        data={"method": args.method},
    )
    
    if exit_code != EXIT_SUCCESS:
        output = {
            "ok": False,
            "command": "jsonrpc call",
            "error": error,
        }
        print(format_output(output, args.compact))
        return exit_code
    
    output = {
        "ok": True,
        "command": "jsonrpc call",
        "data": {
            "method": args.method,
            "result": result.get("result"),
            "error": result.get("error"),
            "error_type": result.get("error_type"),
            "error_code": result.get("error_code"),
            "latency_ms": result.get("latency_ms"),
        },
        "latency_ms": result.get("latency_ms"),
    }
    
    print(format_output(output, args.compact))
    return EXIT_SUCCESS if "result" in result else EXIT_SERVER_ERROR


def cmd_addon_info(args: argparse.Namespace) -> int:
    """Get addon info from bridge."""
    if not args.addonid:
        output = {
            "ok": False,
            "command": "addon info",
            "error": "Missing required argument: --addonid",
        }
        print(format_output(output, args.compact))
        return EXIT_INVALID_ARGS
    
    result, exit_code, error = make_request(
        "/tools/get_bridge_addon_info",
        method="GET",
        data={"addonid": args.addonid},
    )
    
    if exit_code != EXIT_SUCCESS:
        output = {
            "ok": False,
            "command": "addon info",
            "error": error,
        }
        print(format_output(output, args.compact))
        return exit_code
    
    output = {
        "ok": True,
        "command": "addon info",
        "data": {
            "addonid": args.addonid,
            **result,
        },
    }
    
    print(format_output(output, args.compact))
    return EXIT_SUCCESS


def cmd_addon_execute(args: argparse.Namespace) -> int:
    """Execute an addon via bridge."""
    if not args.addonid:
        output = {
            "ok": False,
            "command": "addon execute",
            "error": "Missing required argument: --addonid",
        }
        print(format_output(output, args.compact))
        return EXIT_INVALID_ARGS
    
    result, exit_code, error = make_request(
        "/tools/execute_bridge_addon",
        method="POST",
        data={"addonid": args.addonid},
    )
    
    if exit_code != EXIT_SUCCESS:
        output = {
            "ok": False,
            "command": "addon execute",
            "error": error,
        }
        print(format_output(output, args.compact))
        return exit_code
    
    output = {
        "ok": True if result.get("executed") else False,
        "command": "addon execute",
        "data": {
            "addonid": args.addonid,
            **result,
        },
    }
    
    print(format_output(output, args.compact))
    return EXIT_SUCCESS if result.get("executed") else EXIT_SERVER_ERROR


def cmd_builtin_exec(args: argparse.Namespace) -> int:
    """Execute a Kodi builtin command."""
    if not args.kodi_cmd:
        output = {
            "ok": False,
            "command": "builtin exec",
            "error": "Missing required argument: --command",
        }
        print(format_output(output, args.compact))
        return EXIT_INVALID_ARGS
    
    data = {"command": args.kodi_cmd}
    if args.addonid:
        data["addonid"] = args.addonid
    
    result, exit_code, error = make_request(
        "/tools/execute_bridge_builtin",
        method="POST",
        data=data,
    )
    
    if exit_code != EXIT_SUCCESS:
        output = {
            "ok": False,
            "command": "builtin exec",
            "error": error,
        }
        print(format_output(output, args.compact))
        return exit_code
    
    output = {
        "ok": True,
        "command": "builtin exec",
        "data": {
            "command": args.kodi_cmd,
            **result,
        },
    }
    
    print(format_output(output, args.compact))
    return EXIT_SUCCESS


def cmd_log_tail(args: argparse.Namespace) -> int:
    """Get log tail from bridge."""
    result, exit_code, error = make_request(
        "/tools/get_bridge_log_tail",
        method="GET",
        data={"lines": args.lines},
    )
    
    if exit_code != EXIT_SUCCESS:
        output = {
            "ok": False,
            "command": "log tail",
            "error": error,
        }
        print(format_output(output, args.compact))
        return exit_code
    
    output = {
        "ok": True,
        "command": "log tail",
        "data": {
            "lines": args.lines,
            **result,
        },
    }
    
    print(format_output(output, args.compact))
    return EXIT_SUCCESS


def main():
    parser = argparse.ArgumentParser(
        prog="kodi-cli",
        description="CLI wrapper for Kodi MCP Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "--server",
        default=SERVER_BASE_URL,
        help=f"Server base URL (default: {SERVER_BASE_URL})",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Output compact JSON (no indentation)",
    )
    
    # Top-level subparsers for hierarchical commands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # system status
    system_parser = subparsers.add_parser(
        "system",
        help="System operations",
    )
    system_subparsers = system_parser.add_subparsers(dest="subcommand", help="System subcommands")
    status_parser = system_subparsers.add_parser(
        "status",
        help="Get server/system status",
    )
    status_parser.set_defaults(func=cmd_system_status)
    
    # jsonrpc call
    jsonrpc_parser = subparsers.add_parser(
        "jsonrpc",
        help="JSON-RPC operations",
    )
    jsonrpc_subparsers = jsonrpc_parser.add_subparsers(dest="subcommand", help="JSON-RPC subcommands")
    call_parser = jsonrpc_subparsers.add_parser(
        "call",
        help="Execute a JSON-RPC command",
    )
    call_parser.add_argument(
        "--method",
        required=True,
        help="JSON-RPC method name",
    )
    call_parser.add_argument(
        "--params",
        required=False,
        default=None,
        help="JSON parameters object",
    )
    call_parser.set_defaults(func=cmd_jsonrpc)
    
    # addon info
    addon_parser = subparsers.add_parser(
        "addon",
        help="Addon operations",
    )
    addon_subparsers = addon_parser.add_subparsers(dest="subcommand", help="Addon subcommands")
    addon_info_parser = addon_subparsers.add_parser(
        "info",
        help="Get addon info from bridge",
    )
    addon_info_parser.add_argument(
        "--addonid",
        required=True,
        help="Addon ID (e.g., 'plugin.video.example')",
    )
    addon_info_parser.set_defaults(func=cmd_addon_info)
    
    # addon execute
    addon_exec_parser = addon_subparsers.add_parser(
        "execute",
        help="Execute an addon via bridge",
    )
    addon_exec_parser.add_argument(
        "--addonid",
        required=True,
        help="Addon ID to execute",
    )
    addon_exec_parser.set_defaults(func=cmd_addon_execute)
    
    # builtin exec
    builtin_parser = subparsers.add_parser(
        "builtin",
        help="Builtin operations",
    )
    builtin_subparsers = builtin_parser.add_subparsers(dest="subcommand", help="Builtin subcommands")
    exec_parser = builtin_subparsers.add_parser(
        "exec",
        help="Execute a Kodi builtin command",
    )
    exec_parser.add_argument(
        "--command",
        required=True,
        dest="kodi_cmd",
        help="Kodi builtin command (e.g., 'PlayerControl(Play)')",
    )
    exec_parser.add_argument(
        "--addonid",
        required=False,
        default=None,
        help="Optional addon ID for some commands",
    )
    exec_parser.set_defaults(func=cmd_builtin_exec)
    
    # log tail
    log_parser = subparsers.add_parser(
        "log",
        help="Log operations",
    )
    log_subparsers = log_parser.add_subparsers(dest="subcommand", help="Log subcommands")
    tail_parser = log_subparsers.add_parser(
        "tail",
        help="Get log tail from bridge",
    )
    tail_parser.add_argument(
        "--lines",
        type=int,
        default=20,
        help="Number of log lines to retrieve (default: 20)",
    )
    tail_parser.set_defaults(func=cmd_log_tail)
    
    args = parser.parse_args()
    
    # Handle missing top-level command
    if not args.command:
        parser.print_help()
        return EXIT_INVALID_ARGS
    
    # Handle missing subcommand (for hierarchical commands)
    if not getattr(args, 'subcommand', True):
        # Commands like "system" without subcommand show help
        if args.command in ["system", "jsonrpc", "addon", "builtin", "log"]:
            # Print help for that subparser
            if args.command == "system":
                system_parser.print_help()
            elif args.command == "jsonrpc":
                jsonrpc_parser.print_help()
            elif args.command == "addon":
                addon_parser.print_help()
            elif args.command == "builtin":
                builtin_parser.print_help()
            elif args.command == "log":
                log_parser.print_help()
        else:
            parser.print_help()
        return EXIT_INVALID_ARGS
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
