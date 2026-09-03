# CI and reproducible release gates

This document is the authoritative guide to repository validation and release readiness. These gates are intentionally offline: they do not deploy, tag, publish, or contact the private Kodi compatibility lab.

## Runtime and dependency assumptions

The current production and deterministic-test runtime is Python 3.13, so CI exercises Python 3.13 only. `pyproject.toml` does not currently declare `requires-python`; this workflow does not invent a broader interpreter-support promise. Add a matrix only when project metadata and maintained support policy justify it.

CI installs the package and the bounded `ci` extra from `pyproject.toml`. The CI extra pins the accepted MCP 2.0.0/AnyIO 4.14.2 test baseline and bounds the remaining direct test tools; it also declares the `requests` dependency used by the separately maintained CLI tests. The project still has no complete transitive lockfile, so resolution is repeatable in shape but not byte-for-byte locked. Dependency locking is a separate future decision. Pip caching keys from `pyproject.toml` and is an optimization only.

## Test tiers

### Tier A — deterministic PR gate

Runs for every pull request and push to `main` on a GitHub-hosted Ubuntu runner:

- the complete pytest suite, once;
- compileall for `src/` and `scripts/`;
- package and application imports;
- source/distribution/package/FastAPI/MCP version identity;
- OpenAPI generation, operation-ID uniqueness, duplicate-warning absence, and fresh-generation determinism;
- isolated StreamableHTTP initialization and `tools/list`;
- exact, unique tool names against the single source contract in `kodi_mcp_mcp.tool_contract`.

The full pytest suite supplies the detailed OpenAPI, runtime-identity, StreamableHTTP, output/input contract, and remote-security regressions, including `tests/test_remote_security.py`. Tests use fakes, ASGI/in-memory transports, monkeypatching, and pytest temporary directories; they require no live Kodi, private network, secrets, or production configuration. Some tests create cache/bytecode files and bounded temporary files. Tests are intended to be order-independent, although the known warning below comes from legacy event-loop access whose visibility can depend on prior loop use.

### Tier B — packaging/release gate

Builds both wheel and sdist with the standard `build` frontend, creates a clean virtual environment, installs the wheel non-editably, checks distribution/import identity, installs only the HTTP smoke harness dependency, and runs the same release gate against the installed artifact. This catches stale editable metadata and mismatches among:

- `pyproject.toml`;
- installed distribution metadata;
- `kodi_mcp_server.__version__`;
- FastAPI application version;
- MCP `SERVER_VERSION`.

The installed-artifact gate also generates OpenAPI and performs the non-mutating StreamableHTTP/tool-list smoke test.

### Tier C — private compatibility acceptance

Kodi 19.5, 20.5, 21.3, and 22 Beta remain outside GitHub-hosted CI. They require private lab infrastructure and are not implied by a green Tier A/B run.

Recommended first future path: **Option A**, a manual local/Hermes matrix run that emits a bounded, structured, signed or checksummed evidence report. Release policy can require that report before tagging without exposing the lab or credentials to GitHub. A narrowly scoped self-hosted runner or external GitHub status can be reconsidered later, after its trust and permission model is designed explicitly.

### Tier D — production and publication

Always manual and outside CI: production configuration changes, stop/restart/deployment, rollback, Git tags, GitHub Releases, and any package/image publication. The release-readiness workflow validates only; it has `contents: read` and no credentials or write permissions.

## Local equivalents

From a clean checkout using Python 3.13:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e ".[ci]"
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src scripts
.venv/bin/python -c "import kodi_mcp_mcp.server_core, kodi_mcp_server.main"
.venv/bin/python -m kodi_mcp_server.release_gate --project-root .
```

For a candidate version and exact source commit:

```bash
.venv/bin/python -m kodi_mcp_server.release_gate \
  --project-root . \
  --expected-version X.Y.Z \
  --expected-sha FULL_40_CHARACTER_GIT_SHA
```

For the isolated package equivalent, build into a disposable directory, create a fresh virtual environment, install the one wheel, verify import metadata, install `httpx>=0.28,<1` as the smoke harness, and invoke the same release gate with that environment's Python. GitHub Actions performs these steps in `.github/workflows/validation.yml`.

## Warning policy

CI does not globally suppress or promote warnings. The accepted baseline contains one known warning:

- `tests/test_http_errors.py:45` — `DeprecationWarning: There is no current event loop`.

It remains visible as technical debt. This infrastructure change neither filters nor fixes it; any increase or change should be investigated.

## Workflow security and behavior

- `.github/workflows/ci.yml`: `pull_request` and pushes to `main`; superseded runs for the same PR/ref are cancelled.
- `.github/workflows/release-readiness.yml`: manual dispatch requiring an intended `X.Y.Z` version and full expected Git SHA; it does not tag or publish.
- `.github/workflows/validation.yml`: reusable implementation shared by both callers, avoiding duplicated command logic.
- All workflows declare only `contents: read`.
- No `pull_request_target`, secrets, private addresses, production credentials, or home-lab access are used.
- Manual inputs are passed as quoted environment values and then strictly validated before Git comparison; they are not evaluated as shell code.

## Release checklist

1. Implement the scoped change and establish RED/focused evidence where applicable. **Developer**
2. Run focused tests. **Developer**
3. Run the full deterministic suite. **Tier A automates on PR/push**
4. Obtain strict independent acceptance review. **Manual gate**
5. Create the reviewed local commit. **Manual**
6. Publish the feature branch and PR. **Manual; Tier A/B run automatically**
7. Merge only after review and required checks. **Manual**
8. Synchronize and verify local `main` against the production merge commit. **Manual**
9. Deploy to production without dependency drift. **Manual, outside CI**
10. Verify live runtime identity and behavior. **Manual, outside CI**
11. Tag the exact production merge commit only after Tier C acceptance. **Manual**
12. Publish the GitHub Release from that exact tag. **Manual**

The manual release-readiness dry run may be used before steps 9–12 to bind version and source SHA, but a successful result is not live-Kodi acceptance, deployment, a tag, or a Release.
