"""Direct tests for ``kodi_mcp_server.http_app._addon_registration_loop``.

NOTE: this test is deliberately a *synchronous* pytest test that drives the
coroutine with an explicit ``asyncio.new_event_loop()`` + ``run_until_complete``
instead of ``@pytest.mark.asyncio`` or ``asyncio.run``. Both of those set (and
then clear) the main thread's "current event loop" slot; on Python 3.13 that
permanently breaks the legacy ``asyncio.get_event_loop()`` auto-create pattern
still used by tests/test_http_errors.py. Creating an explicit loop that is
never set as the thread's current loop leaves that slot untouched, so the full
suite stays green regardless of test-file ordering.
"""
import asyncio
import time


async def _run_single_healthy_iteration(monkeypatch):
    """Drive ``_addon_registration_loop`` through exactly ONE healthy iteration.

    The fake ``register_with_addon`` sets the loop's ``stop_event`` during that
    first successful call, so the loop's sleep (``await wait_for(stop_event.wait(),
    timeout=interval)``) returns immediately and the ``while not stop_event.is_set()``
    gate exits — one full iteration of the real loop body, no production sleep.

    Verified:
      1. register_with_addon is called exactly once with a valid payload
      2. read_addon_state is called exactly once, after registration
      3. the returned state is interpreted as HEALTHY and NOT-needing-staging
      4. stage_dev_repo_zip is never called
      5. no real bridge/network is touched (all entry points monkeypatched)
      6. no real repository filesystem is modified (before/after snapshots)
      7. the loop terminates deterministically after one useful iteration
    """
    import kodi_mcp_server.milestone_a_bridge as milestone
    from kodi_mcp_server.http_app import _addon_registration_loop
    from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT

    # Control the loop's termination: defined BEFORE the fakes so they close over
    # the real event the loop will await on.
    stop_event = asyncio.Event()

    call_order = []
    register_calls = [0]
    read_calls = [0]

    async def _fake_register_with_addon(payload):
        register_calls[0] += 1
        call_order.append("register_with_addon")
        # The real payload builder adds extra legitimate fields (control_api_version,
        # started_at, ...), so assert the required keys are a SUBSET, not equality.
        required_keys = {
            "server_id",
            "server_instance_id",
            "server_base_url",
            "mcp_endpoint_url",
            "server_version",
            "ttl_seconds",
            "features",
        }
        missing = required_keys - set(payload.keys())
        assert not missing, f"registration payload missing keys: {missing}"
        # Deterministically stop the loop after this successful iteration.
        stop_event.set()
        return (
            milestone.EnvelopeResult(
                transport_ok=True,
                business_ok=True,
                envelope={"transport": {"ok": True}, "result": {"ok": True}},
            ),
            {"ok": True},
        )

    async def _fake_read_addon_state():
        read_calls[0] += 1
        call_order.append("read_addon_state")
        # Must run after registration.
        assert call_order == ["register_with_addon", "read_addon_state"], (
            f"unexpected call order: {call_order}"
        )
        # A state the loop's real code interprets as HEALTHY and NOT-needing-staging:
        # registration_present=True, registration_stale=False, dev_setup_available=True,
        # repo_zip_file_exists=True, repo_zip.size_bytes >= 1024.
        return (
            milestone.EnvelopeResult(
                transport_ok=True,
                business_ok=True,
                envelope={
                    "transport": {"ok": True},
                    "result": {
                        "ok": True,
                        "state": {
                            "registration": {"applied_ttl_seconds": 60},
                            "repo_zip": {
                                "saved_path": "/profile/repo_stage/dev-repo.zip",
                                "size_bytes": 2048,
                            },
                        },
                        "derived": {
                            "registration_present": True,
                            "registration_stale": False,
                            "dev_setup_available": True,
                            "repo_zip_file_exists": True,
                        },
                    },
                },
            ),
            {"ok": True},
        )

    async def _fake_stage_dev_repo_zip(*, zip_path, repo_version=None, verify=True):
        # Tripwire: a healthy state must never trigger staging.
        raise RuntimeError("staging must not be attempted")

    # The loop lazily imports these from the module at call time, so patching the
    # module attributes is effective. All three bridge/network entry points are
    # covered, so no real connection can occur.
    monkeypatch.setattr(milestone, "register_with_addon", _fake_register_with_addon)
    monkeypatch.setattr(milestone, "read_addon_state", _fake_read_addon_state)
    monkeypatch.setattr(milestone, "stage_dev_repo_zip", _fake_stage_dev_repo_zip)

    # Belt-and-braces: snapshot the repo filesystem before/after. The loop only
    # writes under AUTHORITATIVE_REPO_ROOT inside the staging branch, which must
    # not be entered for a healthy state.
    def snapshot_repo_state():
        snapshots = []
        for p in (AUTHORITATIVE_REPO_ROOT, AUTHORITATIVE_REPO_ROOT.parent / "repo-addon"):
            if p.exists():
                for entry in sorted(p.rglob("*")):
                    if entry.is_file():
                        st = entry.stat()
                        snapshots.append((str(entry.relative_to(p)), st.st_size, st.st_mtime))
        return tuple(snapshots)

    snapshot_before = snapshot_repo_state()

    start = time.monotonic()
    await _addon_registration_loop(stop_event=stop_event)
    elapsed = time.monotonic() - start

    # The loop must finish promptly (no production 10/30 s sleep).
    assert elapsed < 5.0, f"loop took {elapsed:.2f}s, expected < 5s"

    # Exactly one useful iteration, in the correct order.
    assert register_calls[0] == 1, f"register_with_addon called {register_calls[0]} times"
    assert read_calls[0] == 1, f"read_addon_state called {read_calls[0]} times"
    assert call_order == ["register_with_addon", "read_addon_state"], f"call order: {call_order}"
    assert stop_event.is_set(), "stop_event should be set by the first (only) register call"

    # No repository filesystem modification.
    snapshot_after = snapshot_repo_state()
    assert snapshot_before == snapshot_after, (
        f"repo filesystem modified: before={snapshot_before}, after={snapshot_after}"
    )


def test_addon_registration_loop_single_healthy_iteration(monkeypatch):
    # See module docstring: drive the coroutine on an explicit loop that is
    # never set as the thread's current loop, keeping the legacy
    # get_event_loop() auto-create behavior used by other test files intact.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_single_healthy_iteration(monkeypatch))
    finally:
        loop.close()
