from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


class _WorkflowLoader(yaml.SafeLoader):
    pass


for first_char, resolvers in list(_WorkflowLoader.yaml_implicit_resolvers.items()):
    _WorkflowLoader.yaml_implicit_resolvers[first_char] = [
        resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:bool"
    ]


def _load(name: str):
    path = WORKFLOWS / name
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_WorkflowLoader)


def test_workflows_have_expected_triggers_permissions_and_no_secret_surface():
    ci = _load("ci.yml")
    reusable = _load("validation.yml")
    readiness = _load("release-readiness.yml")

    assert set(ci["on"]) == {"pull_request", "push"}
    assert ci["on"]["push"]["branches"] == ["main"]
    assert ci["jobs"]["validate"]["uses"] == "./.github/workflows/validation.yml"
    assert ci["concurrency"]["cancel-in-progress"] == "true"

    assert set(readiness["on"]) == {"workflow_dispatch"}
    inputs = readiness["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"intended_version", "expected_sha"}
    assert all(item["required"] == "true" for item in inputs.values())
    assert readiness["jobs"]["validate"]["uses"] == "./.github/workflows/validation.yml"

    assert set(reusable["on"]) == {"workflow_call"}
    for workflow in (ci, reusable, readiness):
        assert workflow["permissions"] == {"contents": "read"}

    all_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("*.yml"))
    )
    assert "pull_request_target" not in all_text
    assert "secrets." not in all_text
    assert "contents: write" not in all_text
    assert all_text.count("python -m pytest") == 1
    assert "tests/test_remote_security.py" not in all_text
    assert "python -m build" in all_text
    assert "kodi_mcp_server.release_gate" in all_text
