"""
Tests for kodi_cli.py CLI wrapper.

Tests input validation, output formatting, and error handling.
Does NOT test actual server connectivity - that's covered by server tests.
"""

import argparse
import json
import sys
from unittest.mock import patch, MagicMock

import pytest

# Add scripts directory to path
sys.path.insert(0, "/home/node/.openclaw/workspace/project/scripts")

import kodi_cli


class TestInputValidation:
    """Test CLI argument validation."""

    def test_system_status_requires_no_args(self, capsys):
        """System status command works without additional args."""
        with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="system",
                subcommand="status",
                compact=False,
            )
            
            with patch("kodi_cli.make_request", return_value=({"status": "ok"}, kodi_cli.EXIT_SUCCESS, "")):
                result = kodi_cli.cmd_system_status(mock_parse.return_value)
                assert result == kodi_cli.EXIT_SUCCESS
                
                captured = capsys.readouterr()
                output = json.loads(captured.out)
                assert output["ok"] is True
                assert output["command"] == "system status"
                assert "data" in output

    def test_jsonrpc_requires_method(self, capsys):
        """JSON-RPC command requires --method argument."""
        with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="jsonrpc",
                subcommand="call",
                method=None,
                params=None,
                compact=False,
            )
            
            result = kodi_cli.cmd_jsonrpc(mock_parse.return_value)
            assert result == kodi_cli.EXIT_INVALID_ARGS
            
            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert output["ok"] is False
            assert output["command"] == "jsonrpc call"
            assert "error" in output

    def test_addon_info_requires_addonid(self, capsys):
        """Addon info requires --addonid argument."""
        with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="addon",
                subcommand="info",
                addonid=None,
                compact=False,
            )
            
            result = kodi_cli.cmd_addon_info(mock_parse.return_value)
            assert result == kodi_cli.EXIT_INVALID_ARGS
            
            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert output["ok"] is False
            assert output["command"] == "addon info"
            assert "error" in output

    def test_addon_execute_requires_addonid(self, capsys):
        """Addon execute requires --addonid argument."""
        with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="addon",
                subcommand="execute",
                addonid=None,
                compact=False,
            )
            
            result = kodi_cli.cmd_addon_execute(mock_parse.return_value)
            assert result == kodi_cli.EXIT_INVALID_ARGS
            
            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert output["ok"] is False
            assert output["command"] == "addon execute"
            assert "error" in output

    def test_builtin_requires_command(self, capsys):
        """Builtin execute requires --command argument."""
        with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="builtin",
                subcommand="exec",
                kodi_cmd=None,
                addonid=None,
                compact=False,
            )
            
            result = kodi_cli.cmd_builtin_exec(mock_parse.return_value)
            assert result == kodi_cli.EXIT_INVALID_ARGS
            
            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert output["ok"] is False
            assert output["command"] == "builtin exec"
            assert "error" in output


class TestOutputFormat:
    """Test output formatting and structure."""

    def test_format_output_compact(self):
        """Compact JSON output has no indentation."""
        data = {"status": "ok", "data": {"key": "value"}}
        output = kodi_cli.format_output(data, compact=True)
        assert "\n" not in output
        assert "  " not in output

    def test_format_output_pretty(self):
        """Pretty JSON output has indentation."""
        data = {"status": "ok", "data": {"key": "value"}}
        output = kodi_cli.format_output(data, compact=False)
        assert "\n" in output
        assert "  " in output

    def test_jsonrpc_output_structure_success(self, capsys):
        """JSON-RPC success output has unified envelope structure."""
        mock_result = {
            "request_id": "test-123",
            "result": {"version": 21},
            "error": None,
            "error_type": None,
            "error_code": None,
            "latency_ms": 5,
        }
        
        with patch("kodi_cli.make_request", return_value=(mock_result, kodi_cli.EXIT_SUCCESS, "")):
            with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
                mock_parse.return_value = argparse.Namespace(
                    command="jsonrpc",
                    subcommand="call",
                    method="JSONRPC.Version",
                    params=None,
                    compact=False,
                )
                
                result = kodi_cli.cmd_jsonrpc(mock_parse.return_value)
                
                # Verify unified envelope structure
                captured = capsys.readouterr()
                output = json.loads(captured.out)
                assert output["ok"] is True
                assert output["command"] == "jsonrpc call"
                assert "data" in output
                assert output["data"]["method"] == "JSONRPC.Version"
                assert output["data"]["result"] == {"version": 21}
                assert output["latency_ms"] == 5


class TestExitCodes:
    """Test exit code behavior."""

    def test_exit_code_success(self):
        """Successful requests return EXIT_SUCCESS."""
        assert kodi_cli.EXIT_SUCCESS == 0

    def test_exit_code_invalid_args(self):
        """Invalid arguments return EXIT_INVALID_ARGS."""
        assert kodi_cli.EXIT_INVALID_ARGS == 1

    def test_exit_code_connection_error(self):
        """Connection errors return EXIT_CONNECTION_ERROR."""
        assert kodi_cli.EXIT_CONNECTION_ERROR == 2

    def test_exit_code_server_error(self):
        """Server errors return EXIT_SERVER_ERROR."""
        assert kodi_cli.EXIT_SERVER_ERROR == 3

    def test_exit_code_timeout(self):
        """Timeouts return EXIT_TIMEOUT."""
        assert kodi_cli.EXIT_TIMEOUT == 4


class TestConnectionErrorHandling:
    """Test network error handling."""

    def test_connection_error_returns_exit_code(self, monkeypatch):
        """Connection errors return proper exit code."""
        import requests
        
        def mock_request(*args, **kwargs):
            raise requests.exceptions.ConnectionError("Connection failed")
        
        monkeypatch.setattr(kodi_cli.requests, "request", mock_request)
        
        result, exit_code, error = kodi_cli.make_request("/status")
        assert exit_code == kodi_cli.EXIT_CONNECTION_ERROR
        assert "connect" in error.lower()

    def test_connection_error_output(self, monkeypatch):
        """Connection errors print unified error envelope to stdout."""
        import requests
        
        def mock_request(*args, **kwargs):
            raise requests.exceptions.ConnectionError()
        
        monkeypatch.setattr(kodi_cli.requests, "request", mock_request)
        
        with patch("kodi_cli.sys.stderr") as mock_stderr:
            result = kodi_cli.cmd_system_status(
                argparse.Namespace(
                    command="system",
                    subcommand="status",
                    compact=False,
                )
            )
            assert result == kodi_cli.EXIT_CONNECTION_ERROR


class TestRequestConstruction:
    """Test HTTP request construction."""

    def test_get_request_with_params(self, monkeypatch):
        """GET requests pass data as query params."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            kodi_cli.make_request("/status", method="GET", data={"key": "value"})
            
            mock_req.assert_called_once()
            call_kwargs = mock_req.call_args
            assert call_kwargs[1]["params"] == {"key": "value"}
            assert call_kwargs[1]["json"] is None

    def test_post_request_with_json(self, monkeypatch):
        """POST requests pass data as JSON."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            kodi_cli.make_request("/tools/status", method="POST", data={"key": "value"})
            
            mock_req.assert_called_once()
            call_kwargs = mock_req.call_args
            assert call_kwargs[1]["json"] == {"key": "value"}
            assert call_kwargs[1]["params"] is None


class TestInvalidJSONResponse:
    """Test handling of invalid JSON responses."""

    def test_invalid_json_returns_error(self, monkeypatch):
        """Invalid JSON responses return error."""
        import requests
        
        def mock_request(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "not json at all"
            mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
            return mock_response
        
        monkeypatch.setattr(kodi_cli.requests, "request", mock_request)
        
        result, exit_code, error = kodi_cli.make_request("/status")
        assert exit_code == kodi_cli.EXIT_SERVER_ERROR
        assert "invalid json" in error.lower()


class TestPOSTRequestHandling:
    """Test that POST commands correctly send JSON body and backend handles it."""

    def test_addon_execute_sends_structured_json(self, monkeypatch):
        """addon execute sends JSON object with addonid."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"executed": True}
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            kodi_cli.cmd_addon_execute(
                argparse.Namespace(
                    command="addon",
                    subcommand="execute",
                    addonid="test.addon",
                    compact=False,
                )
            )
            
            mock_req.assert_called_once()
            call_kwargs = mock_req.call_args
            assert call_kwargs[1]["method"] == "POST"
            assert call_kwargs[1]["json"] == {"addonid": "test.addon"}
            assert call_kwargs[1]["params"] is None

    def test_builtin_exec_sends_structured_json(self, monkeypatch):
        """builtin exec sends JSON object with command."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"executed": True}
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            kodi_cli.cmd_builtin_exec(
                argparse.Namespace(
                    command="builtin",
                    subcommand="exec",
                    kodi_cmd="PlayerControl(Play)",
                    addonid=None,
                    compact=False,
                )
            )
            
            mock_req.assert_called_once()
            call_kwargs = mock_req.call_args
            assert call_kwargs[1]["method"] == "POST"
            assert call_kwargs[1]["json"] == {"command": "PlayerControl(Play)"}
            assert call_kwargs[1]["params"] is None

    def test_builtin_exec_with_addonid_sends_both(self, monkeypatch):
        """builtin exec with addonid sends both in JSON object."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"executed": True}
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            kodi_cli.cmd_builtin_exec(
                argparse.Namespace(
                    command="builtin",
                    subcommand="exec",
                    kodi_cmd="ReloadSkin",
                    addonid="plugin.video.test",
                    compact=False,
                )
            )
            
            mock_req.assert_called_once()
            call_kwargs = mock_req.call_args
            assert call_kwargs[1]["method"] == "POST"
            assert call_kwargs[1]["json"] == {"command": "ReloadSkin", "addonid": "plugin.video.test"}
            assert call_kwargs[1]["params"] is None


class TestResponseEnvelopeInterpretation:
    """Test that CLI correctly interprets backend response envelopes.
    
    ok represents transport success only: error is None and error_type is None.
    Does not depend on nested business outcome fields.
    """

    def test_addon_execute_success_with_executed_true(self, monkeypatch, capsys):
        """Success response with error=None produces ok: true."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "bridge-execute-addon",
            "result": {"executed": True, "addon_id": "test"},
            "error": None,
            "error_type": None,
        }
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            result = kodi_cli.cmd_addon_execute(
                argparse.Namespace(
                    command="addon",
                    subcommand="execute",
                    addonid="test.addon",
                    compact=True,
                )
            )
            
            output = capsys.readouterr().out
            assert '"ok": true' in output or '"ok":true' in output
            assert result == kodi_cli.EXIT_SUCCESS

    def test_addon_execute_business_failure_still_transport_success(self, monkeypatch, capsys):
        """Response with error=None and executed=false still ok: true (transport success)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "bridge-execute-addon",
            "result": {"executed": False, "addon_id": "unknown"},
            "error": None,
            "error_type": None,
        }
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            result = kodi_cli.cmd_addon_execute(
                argparse.Namespace(
                    command="addon",
                    subcommand="execute",
                    addonid="test.addon",
                    compact=True,
                )
            )
            
            output = capsys.readouterr().out
            # Business failure (executed=false) should not affect ok
            assert '"ok": true' in output or '"ok":true' in output, f"Expected ok: true for transport success, got: {output}"
            assert result == kodi_cli.EXIT_SUCCESS

    def test_addon_execute_with_error_field(self, monkeypatch, capsys):
        """Response with error field produces ok: false."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "bridge-execute-addon",
            "result": None,
            "error": "addon not found",
            "error_type": "not_found",
            "error_code": 404,
        }
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            result = kodi_cli.cmd_addon_execute(
                argparse.Namespace(
                    command="addon",
                    subcommand="execute",
                    addonid="unknown.addon",
                    compact=True,
                )
            )
            
            output = capsys.readouterr().out
            assert '"ok": false' in output or '"ok":false' in output
            assert result == kodi_cli.EXIT_SUCCESS

    def test_builtin_exec_success(self, monkeypatch, capsys):
        """builtin exec with no error produces ok: true."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "bridge-execute-builtin",
            "result": None,
            "error": None,
            "error_type": None,
        }
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            result = kodi_cli.cmd_builtin_exec(
                argparse.Namespace(
                    command="builtin",
                    subcommand="exec",
                    kodi_cmd="PlayerControl(Play)",
                    addonid=None,
                    compact=True,
                )
            )
            
            output = capsys.readouterr().out
            assert '"ok": true' in output or '"ok":true' in output
            assert result == kodi_cli.EXIT_SUCCESS

    def test_builtin_exec_with_auth_error(self, monkeypatch, capsys):
        """builtin exec with error produces ok: false."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "bridge-execute-builtin",
            "result": None,
            "error": "http error 403: Forbidden",
            "error_type": "auth_error",
            "error_code": 403,
        }
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            result = kodi_cli.cmd_builtin_exec(
                argparse.Namespace(
                    command="builtin",
                    subcommand="exec",
                    kodi_cmd="PlayerControl(Play)",
                    addonid=None,
                    compact=True,
                )
            )
            
            output = capsys.readouterr().out
            assert '"ok": false' in output or '"ok":false' in output
            assert result == kodi_cli.EXIT_SUCCESS

    def test_log_tail_success(self, monkeypatch, capsys):
        """log tail with no error produces ok: true."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "bridge-log-tail",
            "result": {"lines": ["test"]},
            "error": None,
            "error_type": None,
        }
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            result = kodi_cli.cmd_log_tail(
                argparse.Namespace(
                    command="log",
                    subcommand="tail",
                    lines=20,
                    compact=True,
                )
            )
            
            output = capsys.readouterr().out
            assert '"ok": true' in output or '"ok":true' in output
            assert result == kodi_cli.EXIT_SUCCESS

    def test_jsonrpc_with_error_field(self, monkeypatch, capsys):
        """jsonrpc call with error produces ok: false."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "jsonrpc-execute",
            "result": None,
            "error": "Method not found",
            "error_type": "method_not_found",
            "error_code": -32601,
        }
        
        with patch("kodi_cli.requests.request", return_value=mock_response) as mock_req:
            result = kodi_cli.cmd_jsonrpc(
                argparse.Namespace(
                    command="jsonrpc",
                    subcommand="call",
                    method="NonExistent.Method",
                    params=None,
                    compact=True,
                )
            )
            
            output = capsys.readouterr().out
            assert '"ok": false' in output or '"ok":false' in output
            assert result == kodi_cli.EXIT_SUCCESS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestHierarchicalCommands:
    """Test the hierarchical command structure and unified response envelope."""

    def test_system_status_command(self, capsys, monkeypatch):
        """system status command works correctly with unified envelope."""
        import requests
        
        def mock_request(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"server": {"status": "running"}}
            return mock_response
        
        monkeypatch.setattr(kodi_cli.requests, "request", mock_request)
        monkeypatch.setattr(kodi_cli.sys, "argv", ["kodi-cli", "system", "status"])
        
        result = kodi_cli.main()
        assert result == kodi_cli.EXIT_SUCCESS
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["command"] == "system status"
        assert "data" in output
        assert "running" in str(output["data"])

    def test_jsonrpc_call_command(self, capsys, monkeypatch):
        """jsonrpc call command works correctly with unified envelope."""
        import requests
        
        def mock_request(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "result": {"version": 21},
                "latency_ms": 5,
            }
            return mock_response
        
        monkeypatch.setattr(kodi_cli.requests, "request", mock_request)
        
        # Simulate: kodi-cli jsonrpc call --method JSONRPC.Version
        with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="jsonrpc",
                subcommand="call",
                method="JSONRPC.Version",
                params=None,
                compact=False,
            )
            result = kodi_cli.cmd_jsonrpc(mock_parse.return_value)
            assert result == kodi_cli.EXIT_SUCCESS
            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert output["ok"] is True
            assert output["command"] == "jsonrpc call"
            assert output["data"]["method"] == "JSONRPC.Version"
            assert output["data"]["result"] == {"version": 21}
            assert output["latency_ms"] == 5

    def test_addon_info_command(self, capsys, monkeypatch):
        """addon info command works correctly with unified envelope."""
        import requests
        
        def mock_request(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"addon": {"name": "test"}}
            return mock_response
        
        monkeypatch.setattr(kodi_cli.requests, "request", mock_request)
        
        # Simulate: kodi-cli addon info --addonid test
        with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="addon",
                subcommand="info",
                addonid="test.addon",
                compact=False,
            )
            result = kodi_cli.cmd_addon_info(mock_parse.return_value)
            assert result == kodi_cli.EXIT_SUCCESS
            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert output["ok"] is True
            assert output["command"] == "addon info"
            assert output["data"]["addonid"] == "test.addon"
            assert "test" in str(output["data"])

    def test_builtin_exec_command(self, capsys, monkeypatch):
        """builtin exec command works correctly with unified envelope."""
        import requests
        
        def mock_request(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"status": "ok"}
            return mock_response
        
        monkeypatch.setattr(kodi_cli.requests, "request", mock_request)
        
        # Simulate: kodi-cli builtin exec --command PlayerControl(Play)
        with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="builtin",
                subcommand="exec",
                kodi_cmd="PlayerControl(Play)",
                addonid=None,
                compact=False,
            )
            result = kodi_cli.cmd_builtin_exec(mock_parse.return_value)
            assert result == kodi_cli.EXIT_SUCCESS
            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert output["ok"] is True
            assert output["command"] == "builtin exec"
            assert output["data"]["command"] == "PlayerControl(Play)"
            assert "ok" in str(output["data"])

    def test_log_tail_command(self, capsys, monkeypatch):
        """log tail command works correctly with unified envelope."""
        import requests
        
        def mock_request(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"lines": ["log1", "log2"]}
            return mock_response
        
        monkeypatch.setattr(kodi_cli.requests, "request", mock_request)
        
        # Simulate: kodi-cli log tail --lines 10
        with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="log",
                subcommand="tail",
                lines=10,
                compact=False,
            )
            result = kodi_cli.cmd_log_tail(mock_parse.return_value)
            assert result == kodi_cli.EXIT_SUCCESS
            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert output["ok"] is True
            assert output["command"] == "log tail"
            assert "lines" in output["data"]
            assert "log1" in str(output["data"])

    def test_service_probe_success(self, capsys, monkeypatch):
        """service probe success path returns correct envelope."""
        with patch("kodi_cli.make_request", return_value=({
            "request_id": "bridge-control-capabilities",
            "result": {
                "addon_id": "service.kodi_mcp",
                "addon_version": "0.2.16",
                "control_api_version": 1,
                "endpoints": {
                    "health": {"method": "GET", "path": "/health"},
                    "ping": {"method": "GET", "path": "/ping"},
                    "version": {"method": "GET", "path": "/version"},
                    "debug_ping": {"method": "POST", "path": "/debug/ping"},
                },
                "features": {
                    "liveness_probe": True,
                    "version_probe": True,
                    "debug_ping": True,
                    "lifecycle_control": False,
                },
            },
            "error": None,
            "error_type": None,
        }, kodi_cli.EXIT_SUCCESS, "")):
            with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
                mock_parse.return_value = argparse.Namespace(
                    command="service",
                    subcommand="probe",
                    compact=False,
                )
                result = kodi_cli.cmd_service_probe(mock_parse.return_value)
                assert result == kodi_cli.EXIT_SUCCESS
                captured = capsys.readouterr()
                output = json.loads(captured.out)
                assert output["ok"] is True
                assert output["command"] == "service probe"
                assert "data" in output
                assert output["data"]["result"]["addon_id"] == "service.kodi_mcp"
                assert output["data"]["result"]["addon_version"] == "0.2.16"
                assert isinstance(output["data"]["result"]["supported_endpoints"], dict)
                assert "health" in output["data"]["result"]["supported_endpoints"]
                assert "liveness_probe" in output["data"]["result"]["features"]
                assert output["data"]["result"]["features"]["liveness_probe"] is True

    def test_service_probe_failure(self, capsys, monkeypatch):
        """service probe failure path returns error envelope."""
        monkeypatch.setattr(kodi_cli.requests, "request", lambda *a, **k: (None, kodi_cli.EXIT_SERVER_ERROR, "connection refused"))
        
        with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="service",
                subcommand="probe",
                compact=False,
            )
            result = kodi_cli.cmd_service_probe(mock_parse.return_value)
            assert result == kodi_cli.EXIT_SERVER_ERROR
            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert output["ok"] is False
            assert output["command"] == "service probe"
            assert "error" in output

    def test_service_ping_success(self, capsys, monkeypatch):
        """service ping success path returns correct envelope."""
        with patch("kodi_cli.make_request", return_value=({
            "request_id": "bridge-ping",
            "result": {
                "addon_id": "service.kodi_mcp",
                "addon_version": "0.2.16",
                "timestamp": 1775074549,
            },
            "error": None,
            "error_type": None,
        }, kodi_cli.EXIT_SUCCESS, "")):
            with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
                mock_parse.return_value = argparse.Namespace(
                    command="service",
                    subcommand="ping",
                    compact=False,
                )
                result = kodi_cli.cmd_service_ping(mock_parse.return_value)
                assert result == kodi_cli.EXIT_SUCCESS
                captured = capsys.readouterr()
                output = json.loads(captured.out)
                assert output["ok"] is True
                assert output["command"] == "service ping"
                assert "data" in output
                assert output["data"]["result"]["addon_id"] == "service.kodi_mcp"
                assert output["data"]["result"]["addon_version"] == "0.2.16"
                assert output["data"]["result"]["timestamp"] == 1775074549

    def test_service_ping_failure(self, capsys, monkeypatch):
        """service ping failure path returns error envelope."""
        monkeypatch.setattr(kodi_cli.requests, "request", lambda *a, **k: (None, kodi_cli.EXIT_SERVER_ERROR, "connection refused"))
        
        with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="service",
                subcommand="ping",
                compact=False,
            )
            result = kodi_cli.cmd_service_ping(mock_parse.return_value)
            assert result == kodi_cli.EXIT_SERVER_ERROR
            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert output["ok"] is False
            assert output["command"] == "service ping"
            assert "error" in output

    def test_unified_envelope_success(self, capsys, monkeypatch):
        """All successful commands return unified envelope with ok=true."""
        import requests
        
        test_cases = [
            ("system status", {"status": "ok"}, "system status"),
            ("jsonrpc call", {"result": {"v": 1}}, "jsonrpc call"),
            ("addon info", {"info": "data"}, "addon info"),
            ("addon execute", {"executed": True}, "addon execute"),
            ("builtin exec", {"executed": True}, "builtin exec"),
            ("log tail", {"lines": []}, "log tail"),
        ]
        
        monkeypatch.setattr(kodi_cli.requests, "request", lambda *a, **k: (MagicMock(status_code=200, json=lambda: {"result": "ok"}), kodi_cli.EXIT_SUCCESS, ""))
        
        for command_name, mock_result, expected_cmd in test_cases:
            # Test error case first
            with patch("kodi_cli.make_request", return_value=(mock_result, kodi_cli.EXIT_SUCCESS, "")):
                with patch("kodi_cli.argparse.ArgumentParser.parse_args") as mock_parse:
                    # Determine namespace based on command
                    if command_name == "system status":
                        args = argparse.Namespace(command="system", subcommand="status", compact=False)
                        result = kodi_cli.cmd_system_status(args)
                    elif command_name == "jsonrpc call":
                        args = argparse.Namespace(command="jsonrpc", subcommand="call", method="test", params=None, compact=False)
                        result = kodi_cli.cmd_jsonrpc(args)
                    elif command_name == "addon info":
                        args = argparse.Namespace(command="addon", subcommand="info", addonid="test", compact=False)
                        result = kodi_cli.cmd_addon_info(args)
                    elif command_name == "addon execute":
                        args = argparse.Namespace(command="addon", subcommand="execute", addonid="test", compact=False)
                        result = kodi_cli.cmd_addon_execute(args)
                    elif command_name == "builtin exec":
                        args = argparse.Namespace(command="builtin", subcommand="exec", kodi_cmd="test", addonid=None, compact=False)
                        result = kodi_cli.cmd_builtin_exec(args)
                    elif command_name == "log tail":
                        args = argparse.Namespace(command="log", subcommand="tail", lines=10, compact=False)
                        result = kodi_cli.cmd_log_tail(args)
                    
                    captured = capsys.readouterr()
                    output = json.loads(captured.out)
                    assert output["ok"] is True, f"{command_name} should return ok=true"
                    assert output["command"] == expected_cmd, f"{command_name} should have correct command name"
                    assert "data" in output, f"{command_name} should have data field"
