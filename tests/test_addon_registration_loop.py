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
                        "schema_version": 1,
                        "state_rev": 1,
                        "registration": {"applied_ttl_seconds": 60},
                        "repo_zip": {
                            "saved_path": "/profile/repo_stage/dev-repo.zip",
                            "size_bytes": 2048,
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


async def _run_registration_exception_recovery(monkeypatch):
    """Drive ``_addon_registration_loop`` through an iteration whose
    ``register_with_addon`` call raises an arbitrary exception.

    The loop's outer ``try/except Exception`` must absorb the raised error:
    the background task must not die, the failure must be contained to that
    iteration (no state read, no staging), and the loop must terminate
    cleanly and promptly when stop_event is set.

    Verified:
      1. a raised RuntimeError does not propagate out of the loop
      2. register_with_addon is called exactly once (the failed iteration);
         the loop did not crash into a retry storm or a silent death
      3. read_addon_state is never called (tripwire)
      4. stage_dev_repo_zip is never called (tripwire)
      5. the loop terminates deterministically after one iteration
      6. no real repository filesystem is modified (before/after snapshots)
    """
    import kodi_mcp_server.milestone_a_bridge as milestone
    from kodi_mcp_server.http_app import _addon_registration_loop
    from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT

    stop_event = asyncio.Event()
    register_calls = [0]

    async def _fake_register_with_addon(payload):
        register_calls[0] += 1
        # Deterministically stop the loop after this failed iteration so the
        # loop's post-iteration sleep returns immediately (no production 5s
        # unhealthy-retry sleep is awaited).
        stop_event.set()
        # Simulate the bridge dying with an arbitrary, unanticipated error.
        raise RuntimeError("bridge connection refused")

    async def _fake_read_addon_state():
        # Tripwire: a registration exception must never reach the state read.
        raise AssertionError("read_addon_state must not be called after a registration exception")

    async def _fake_stage_dev_repo_zip(*, zip_path, repo_version=None, verify=True):
        # Tripwire: the failure path must never trigger staging.
        raise RuntimeError("staging must not be attempted")

    monkeypatch.setattr(milestone, "register_with_addon", _fake_register_with_addon)
    monkeypatch.setattr(milestone, "read_addon_state", _fake_read_addon_state)
    monkeypatch.setattr(milestone, "stage_dev_repo_zip", _fake_stage_dev_repo_zip)

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
    # Reaching the next line at all proves the raised exception was absorbed
    # by the loop's outer handler instead of killing the task.
    await _addon_registration_loop(stop_event=stop_event)
    elapsed = time.monotonic() - start

    assert register_calls[0] == 1, f"register_with_addon called {register_calls[0]} times, expected 1"
    assert stop_event.is_set(), "stop_event should be set by the (failing) register call"
    assert elapsed < 5.0, f"loop took {elapsed:.2f}s, expected < 5s (no production retry sleep)"

    snapshot_after = snapshot_repo_state()
    assert snapshot_before == snapshot_after, (
        f"repo filesystem modified: before={snapshot_before}, after={snapshot_after}"
    )


def test_addon_registration_loop_survives_registration_exception(monkeypatch):
    # Explicit-loop mechanics per the module docstring: never set as the
    # thread's current loop.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_registration_exception_recovery(monkeypatch))
    finally:
        loop.close()


async def _run_unhealthy_stale_no_stage_iteration(monkeypatch):
    """Drive ``_addon_registration_loop`` through ONE iteration whose state read
    reports a *graceful* (business-level) unhealthy registration: present but
    stale (TTL expired). The loop must classify it UNHEALTHY and NOT stage the
    repo zip — staging is gated on ``healthy`` — and terminate cleanly when
    stop_event is set.

    The repo flags are set so they WOULD require staging if ``healthy`` were
    true (dev_setup_available=False, repo_zip_file_exists=False); the ONLY
    reason staging does not fire is the unhealthy registration, isolating the
    ``healthy`` gate from the repo-need flags.

    Verified:
      1. register_with_addon succeeds (transport + business ok), called exactly
         once; read_addon_state called exactly once, after registration
      2. stage_dev_repo_zip is never called even though the repo flags would
         otherwise require staging (tripwire)
      3. no real bridge/network is touched; no repo filesystem modified
      4. the loop terminates deterministically after one iteration
    """
    import kodi_mcp_server.milestone_a_bridge as milestone
    from kodi_mcp_server.http_app import _addon_registration_loop
    from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT

    stop_event = asyncio.Event()
    call_order = []

    async def _fake_register_with_addon(payload):
        call_order.append("register_with_addon")
        # Stop the loop after this (unhealthy) iteration so the loop's
        # post-iteration sleep returns immediately (no production 5s sleep).
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
        call_order.append("read_addon_state")
        # Must run after registration.
        assert call_order == ["register_with_addon", "read_addon_state"], (
            f"unexpected call order: {call_order}"
        )
        # UNHEALTHY state: registration present but STALE -> healthy is False.
        # Repo flags are set so they would require staging if healthy were True.
        return (
            milestone.EnvelopeResult(
                transport_ok=True,
                business_ok=True,
                envelope={
                    "transport": {"ok": True},
                    "result": {
                        "ok": True,
                        "schema_version": 1,
                        "state_rev": 1,
                        "registration": {"applied_ttl_seconds": 60},
                        "repo_zip": {
                            "saved_path": "/profile/repo_stage/dev-repo.zip",
                            "size_bytes": 0,
                        },
                        "derived": {
                            "registration_present": True,
                            "registration_stale": True,
                            "dev_setup_available": False,
                            "repo_zip_file_exists": False,
                        },
                    },
                },
            ),
            {"ok": True},
        )

    async def _fake_stage_dev_repo_zip(*, zip_path, repo_version=None, verify=True):
        # Tripwire: an unhealthy (stale) registration must never stage.
        raise AssertionError(
            "staging must not be attempted for an unhealthy/stale registration"
        )

    monkeypatch.setattr(milestone, "register_with_addon", _fake_register_with_addon)
    monkeypatch.setattr(milestone, "read_addon_state", _fake_read_addon_state)
    monkeypatch.setattr(milestone, "stage_dev_repo_zip", _fake_stage_dev_repo_zip)

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

    # The loop must finish promptly (no production unhealthy retry sleep).
    assert elapsed < 5.0, f"loop took {elapsed:.2f}s, expected < 5s"

    # Exactly one iteration, in the correct order (implies each entry point
    # called exactly once); the loop survived the unhealthy state.
    assert call_order == ["register_with_addon", "read_addon_state"], f"call order: {call_order}"
    assert stop_event.is_set(), "stop_event should be set by the (only) register call"

    # No repository filesystem modification (staging was never entered).
    snapshot_after = snapshot_repo_state()
    assert snapshot_before == snapshot_after, (
        f"repo filesystem modified: before={snapshot_before}, after={snapshot_after}"
    )


def test_addon_registration_loop_unhealthy_stale_does_not_stage(monkeypatch):
    # Explicit-loop mechanics per the module docstring: never set as the
    # thread's current loop.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_unhealthy_stale_no_stage_iteration(monkeypatch))
    finally:
        loop.close()


async def _run_rejected_registration_iteration(monkeypatch):
    """Drive ``_addon_registration_loop`` through ONE iteration whose
    ``register_with_addon`` returns the live addon's **401/UNAUTHORIZED
    envelope** — transport OK, business OK=False.

    This is the real-world "server KODI_BRIDGE_TOKEN does not equal the addon
    mcp_token" failure. The loop's real code (``http_app._addon_registration_loop``)
    must take the ``transport_ok=True, business_ok=False`` branch: classify the
    iteration as REJECTED (a business-level failure, not a transport failure
    and not healthy), NOT read addon state, NOT stage the repo zip, and keep
    the background task alive so it converges once the token is corrected.

    The envelope mirrors the live addon wire shape exactly
    (``{"transport": {"ok": true}, "result": {"ok": false, "error_code":
    "UNAUTHORIZED", ...}}``), so the test pins the branch against the real
    protocol contract rather than a synthetic variant.

    Verified:
      1. register_with_addon is called exactly once, returning the 401
         envelope (transport_ok=True, business_ok=False)
      2. read_addon_state is NEVER called (tripwire) — only the healthy branch
         reads state; a rejected registration must not
      3. stage_dev_repo_zip is NEVER called (tripwire) — staging is gated on
         a healthy registration
      4. no real bridge/network is touched; no repo filesystem modified
      5. the loop terminates deterministically after one iteration (the fake
         sets stop_event, so the post-iteration wait returns immediately)
    """
    import kodi_mcp_server.milestone_a_bridge as milestone
    from kodi_mcp_server.http_app import _addon_registration_loop
    from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT

    stop_event = asyncio.Event()
    register_calls = [0]
    read_calls = [0]

    async def _fake_register_with_addon(payload):
        register_calls[0] += 1
        # Deterministically stop the loop after this (rejected) iteration so
        # the post-iteration wait returns immediately (no production sleep).
        stop_event.set()
        # The live addon's 401 wire shape: transport succeeded (the HTTP
        # request was made), but the business result is a rejection.
        return (
            milestone.EnvelopeResult(
                transport_ok=True,
                business_ok=False,
                envelope={
                    "transport": {"ok": True},
                    "result": {
                        "ok": False,
                        "error_code": "UNAUTHORIZED",
                        "message": "Missing or invalid X-Kodi-MCP-Token",
                    },
                },
            ),
            {
                "transport": {"ok": True},
                "result": {
                    "ok": False,
                    "error_code": "UNAUTHORIZED",
                    "message": "Missing or invalid X-Kodi-MCP-Token",
                },
            },
        )

    async def _fake_read_addon_state():
        read_calls[0] += 1
        # Tripwire: a rejected (401) registration must never reach the state
        # read — the healthy branch is the only path that calls this.
        raise AssertionError(
            "read_addon_state must not be called for a rejected (401) registration"
        )

    async def _fake_stage_dev_repo_zip(*, zip_path, repo_version=None, verify=True):
        # Tripwire: a rejected registration must never trigger staging.
        raise AssertionError("staging must not be attempted for a rejected registration")

    monkeypatch.setattr(milestone, "register_with_addon", _fake_register_with_addon)
    monkeypatch.setattr(milestone, "read_addon_state", _fake_read_addon_state)
    monkeypatch.setattr(milestone, "stage_dev_repo_zip", _fake_stage_dev_repo_zip)

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

    # Exactly one iteration: the rejected registration was processed once.
    assert register_calls[0] == 1, f"register_with_addon called {register_calls[0]} times, expected 1"
    # The rejection branch must NOT read addon state.
    assert read_calls[0] == 0, f"read_addon_state called {read_calls[0]} times, expected 0"
    assert stop_event.is_set(), "stop_event should be set by the (rejected) register call"
    assert elapsed < 5.0, f"loop took {elapsed:.2f}s, expected < 5s (no production retry sleep)"

    # No repository filesystem modification (staging was never entered).
    snapshot_after = snapshot_repo_state()
    assert snapshot_before == snapshot_after, (
        f"repo filesystem modified: before={snapshot_before}, after={snapshot_after}"
    )


def test_addon_registration_loop_rejected_registration_does_not_stage(monkeypatch):
    # Explicit-loop mechanics per the module docstring: never set as the
    # thread's current loop.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_rejected_registration_iteration(monkeypatch))
    finally:
        loop.close()


async def _run_flat_state_healthy_no_stage_iteration(monkeypatch):
    """Drive ``_addon_registration_loop`` through ONE healthy iteration whose
    ``/mcp/state`` response is in the REAL flat envelope shape the addon
    actually returns (``result.registration`` / ``result.repo_zip`` /
    ``result.derived`` as siblings of ``result.ok`` — NO ``result.state``
    wrapper).

    Regression for a key-level drift: the loop historically looked under
    ``result.state`` for ``registration`` and ``repo_zip``, so with the real
    flat shape it read ``applied_ttl_seconds`` as absent (fell back to the 60s
    default), read ``repo_zip.size_bytes`` as 0, and concluded a healthy,
    validly-staged repo "looked obviously wrong (too small)" and re-staged
    the repo zip every reconciliation interval.

    Verified:
      1. register_with_addon + read_addon_state each called exactly once, in
         order; all bridge entry points monkeypatched
      2. stage_dev_repo_zip is NEVER called for a healthy state with a valid
         staged zip well above the 1024-byte threshold (tripwire)
      3. the loop's reconciliation wait interval is derived from the
         non-default ``registration.applied_ttl_seconds`` (40 -> interval 20s),
         NOT the 60s default (which would give 30s)
      4. no real bridge/network is touched; no repo filesystem modified
      5. the loop terminates deterministically after one useful iteration
    """
    import kodi_mcp_server.milestone_a_bridge as milestone
    from kodi_mcp_server.http_app import _addon_registration_loop
    from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT

    stop_event = asyncio.Event()
    observed_intervals = []
    register_calls = [0]
    read_calls = [0]

    async def _fake_register_with_addon(payload):
        register_calls[0] += 1
        # Deterministically stop the loop after this successful iteration so
        # the post-iteration wait returns immediately (no production sleep).
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
        # The REAL flat /mcp/state result shape (addon-side, verified against
        # http_bridge.py's /mcp/state handler): registration and repo_zip are
        # siblings of ok/schema_version/state_rev/derived. No "state" key.
        # applied_ttl_seconds=40 is deliberately non-default so the interval
        # assertion proves it was consumed (40 -> max(10, min(30, 20)) = 20,
        # whereas the 60s fallback would give 30).
        return (
            milestone.EnvelopeResult(
                transport_ok=True,
                business_ok=True,
                envelope={
                    "transport": {"ok": True},
                    "result": {
                        "ok": True,
                        "schema_version": 1,
                        "state_rev": 42,
                        "registration": {
                            "applied_ttl_seconds": 40,
                            "last_seen_at": 1756000000,
                        },
                        "repo_zip": {
                            "saved_path": "/profile/repo_stage/dev-repo.zip",
                            "size_bytes": 4194304,
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
        # Tripwire: a healthy state with a validly staged, above-threshold zip
        # must never re-stage — the defect caused a staging call on every
        # reconciliation interval.
        raise RuntimeError(
            "staging must not be attempted for a healthy state with a valid staged zip"
        )

    async def _fake_wait_for(*args, **kwargs):
        # Record the loop's chosen sleep interval and return immediately:
        # stop_event is set by the register fake, so the real wait would
        # return just as fast — recording the timeout first proves the
        # consumed TTL. The loop's call is wait_for(stop_event.wait(),
        # timeout=interval); args[0] is that coroutine, so record by the
        # timeout kwarg and close the coroutine to avoid "never awaited".
        import inspect

        if args and inspect.iscoroutine(args[0]):
            observed_intervals.append(kwargs.get("timeout"))
            args[0].close()
        return None

    monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for, raising=False)
    monkeypatch.setattr(milestone, "register_with_addon", _fake_register_with_addon)
    monkeypatch.setattr(milestone, "read_addon_state", _fake_read_addon_state)
    monkeypatch.setattr(milestone, "stage_dev_repo_zip", _fake_stage_dev_repo_zip)

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

    # The loop must finish promptly (no production sleep).
    assert elapsed < 5.0, f"loop took {elapsed:.2f}s, expected < 5s"
    assert stop_event.is_set(), "stop_event should be set by the register call"
    assert register_calls[0] == 1, f"register_with_addon called {register_calls[0]} times"
    assert read_calls[0] == 1, f"read_addon_state called {read_calls[0]} times"

    # The reconciliation wait interval must be derived from the addon's
    # applied_ttl_seconds=40 (-> 20s), not the 60s default (-> 30s).
    assert observed_intervals, "healthy-iteration wait interval not observed"
    assert observed_intervals[0] == 20, (
        f"expected 20s interval from applied_ttl_seconds=40, "
        f"got {observed_intervals[0]!r} (60s default fallback would give 30)"
    )

    # No repository filesystem modification (staging was never entered).
    snapshot_after = snapshot_repo_state()
    assert snapshot_before == snapshot_after, (
        f"repo filesystem modified: before={snapshot_before}, after={snapshot_after}"
    )


async def _run_flat_state_small_valid_zip_no_restage_iteration(monkeypatch):
    """Drive ``_addon_registration_loop`` through ONE healthy iteration whose
    ``/mcp/state`` reports a validly staged repo zip BELOW the historical
    1024-byte heuristic — the real observed live artifact size (1000 bytes).

    Regression for the arbitrary-size re-staging loop: a structurally valid,
    installable Kodi repository addon zip (addon.xml with the
    ``xbmc.addon.repository`` extension + service.py stub + root addons.xml)
    compresses to ~1000 bytes with ZIP_DEFLATED. The loop's historical
    ``repo_size < 1024`` "looks obviously wrong" check therefore fired on the
    legitimate artifact, so the loop rebuilt and re-staged the repo addon zip
    on every reconciliation cycle (live ``repo_zip.staged_at`` observed
    advancing ~60s apart indefinitely).

    The addon-side /mcp/state contract already provides authoritative signals
    that the artifact is present and usable: ``derived.repo_zip_file_exists``
    (actual filesystem check) and ``derived.dev_setup_available`` (full
    readiness). Byte size alone, below a small threshold, is NOT a valid
    signal of corruption for this artifact class, so the loop must not
    re-stage merely because size_bytes < 1024.

    Verified:
      1. register_with_addon + read_addon_state each called exactly once, in
         order; all bridge entry points monkeypatched
      2. stage_dev_repo_zip is NEVER called for a healthy state with a valid
         staged zip of 1000 bytes (tripwire, patched at the source module so
         the loop's lazy import resolves to the tripwire)
      3. no repository filesystem modification (before/after snapshots)
      4. the loop terminates deterministically after one useful iteration
    """
    import kodi_mcp_server.milestone_a_bridge as milestone
    import kodi_mcp_server.repo_generator as repo_generator
    from kodi_mcp_server.http_app import _addon_registration_loop
    from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT

    stop_event = asyncio.Event()
    call_order = []
    stage_calls = [0]
    build_calls = [0]

    async def _fake_register_with_addon(payload):
        call_order.append("register_with_addon")
        # Deterministically stop the loop after this successful iteration so
        # the post-iteration wait returns immediately (no production sleep).
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
        call_order.append("read_addon_state")
        # Must run after registration.
        assert call_order == ["register_with_addon", "read_addon_state"], (
            f"unexpected call order: {call_order}"
        )
        # The REAL flat /mcp/state result shape, with the live-observed
        # artifact size: a valid repository addon zip of 1000 bytes, present
        # on disk and fully ready.
        return (
            milestone.EnvelopeResult(
                transport_ok=True,
                business_ok=True,
                envelope={
                    "transport": {"ok": True},
                    "result": {
                        "ok": True,
                        "schema_version": 1,
                        "state_rev": 43,
                        "registration": {"applied_ttl_seconds": 60},
                        "repo_zip": {
                            "repo_id": "dev-repo",
                            "repo_version": "1.0.0",
                            "size_bytes": 1000,
                            "special_path": (
                                "special://profile/addon_data/"
                                "service.kodi_mcp/dev_repo/dev-repo.zip"
                            ),
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
        # Tripwire: a healthy state with a valid, present 1000-byte zip must
        # never re-stage merely because of its byte size.
        stage_calls[0] += 1
        raise AssertionError(
            "staging must not be attempted for a healthy state with a valid "
            "staged zip below the historical 1024-byte heuristic"
        )

    # The loop lazy-imports stage_dev_repo_zip and build_repo_addon INSIDE the
    # staging branch, so patch the source modules (not just milestone) to
    # guarantee the real staging path — including any bridge upload — is
    # unreachable. build_repo_addon would also write into the repo-addon/
    # filesystem; the fake records the call (and must not write) so the
    # decisive assertion is that the staging branch was never entered at all.
    def _fake_build_repo_addon(*args, **kwargs):
        build_calls[0] += 1
        raise AssertionError(
            "build_repo_addon must not be called in the no-restage path"
        )

    monkeypatch.setattr(milestone, "register_with_addon", _fake_register_with_addon)
    monkeypatch.setattr(milestone, "read_addon_state", _fake_read_addon_state)
    monkeypatch.setattr(milestone, "stage_dev_repo_zip", _fake_stage_dev_repo_zip)
    monkeypatch.setattr(repo_generator, "build_repo_addon", _fake_build_repo_addon)

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

    # The loop must finish promptly (no production sleep).
    assert elapsed < 5.0, f"loop took {elapsed:.2f}s, expected < 5s"
    assert stop_event.is_set(), "stop_event should be set by the register call"
    assert call_order == ["register_with_addon", "read_addon_state"], f"call order: {call_order}"

    # The decisive assertion: the staging branch must not be entered at all
    # for a small-but-valid zip. build_repo_addon is the branch's first
    # action, so it is the earliest observable signal of a re-stage attempt.
    assert build_calls[0] == 0, (
        f"build_repo_addon called {build_calls[0]} time(s); a healthy state "
        f"with a valid 1000-byte staged zip must not be re-staged by size alone"
    )
    assert stage_calls[0] == 0, (
        f"stage_dev_repo_zip called {stage_calls[0]} time(s); a healthy state "
        f"with a valid 1000-byte staged zip must not be re-staged by size alone"
    )

    # No repository filesystem modification (staging was never entered).
    snapshot_after = snapshot_repo_state()
    assert snapshot_before == snapshot_after, (
        f"repo filesystem modified: before={snapshot_before}, after={snapshot_after}"
    )


def test_addon_registration_loop_small_valid_zip_not_re_staged_by_size(monkeypatch):
    # Explicit-loop mechanics per the module docstring: never set as the
    # thread's current loop.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_flat_state_small_valid_zip_no_restage_iteration(monkeypatch))
    finally:
        loop.close()


def test_addon_registration_loop_flat_state_healthy_does_not_re_stage(monkeypatch):
    # Explicit-loop mechanics per the module docstring: never set as the
    # thread's current loop.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_flat_state_healthy_no_stage_iteration(monkeypatch))
    finally:
        loop.close()
