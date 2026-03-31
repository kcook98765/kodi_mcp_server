# SKILLS.md - AgentSkills for kodi_mcp_server Development

These skills provide specialized instructions for backend server development tasks.

## Available Skills

### inspect-architecture

**Description:** Analyze the current architecture, trace request flows, and identify entry points, transports, and gaps.

**Use when:**
- You need to understand how requests flow through the system
- Before making changes to understand dependencies
- When tracing a bug or feature implementation path

**Steps:**
1. Read `main.py`, `mcp_app.py`, `repo_app.py` to identify entry points
2. Examine `transport/*.py` to map transport layers
3. Review `models/*.py` for request/response structures
4. Check `tools/*.py` for tool implementations
5. Summarize the architecture and identify gaps

---

### stabilize-config

**Description:** Ensure configuration loading is robust, documented, and follows best practices.

**Use when:**
- Config values are not loading correctly
- New configuration options are needed
- Environment variables need to be documented

**Steps:**
1. Review `config.py` and identify current loading logic
2. Check `.env.example` for documented variables
3. Ensure all config has sensible defaults
4. Add validation for required fields
5. Document config loading behavior in README

---

### standardize-responses

**Description:** Ensure all endpoints return structured, predictable JSON responses.

**Use when:**
- Adding new endpoints
- Refactoring existing tools
- Preparing for CLI wrapper implementation

**Steps:**
1. Define standard response structure (success/error fields)
2. Ensure all tools follow the same pattern
3. Add consistent error codes/messages
4. Document response formats in README

---

### define-cli-contract

**Description:** Define the contract for future CLI wrapper commands.

**Use when:**
- Preparing for CLI wrapper implementation
- Need to document expected inputs/outputs
- Designing new commands

**Steps:**
1. List all tool endpoints that need CLI wrappers
2. Define command syntax for each
3. Specify expected JSON inputs/outputs
4. Document error handling behavior
5. Create example CLI usage in README

---

### improve-error-handling

**Description:** Add consistent error handling across transports and tools.

**Use when:**
- Errors are inconsistent or unclear
- Debugging is difficult due to poor error messages
- Preparing for production use

**Steps:**
1. Review error handling in each transport
2. Define standard error response structure
3. Add descriptive error messages
4. Ensure network errors are caught and handled
5. Document error codes and meanings

---

### test-transports

**Description:** Create or run integration tests for transport layers.

**Use when:**
- Adding new transports
- Modifying existing transports
- Validating connectivity to remote Kodi

**Steps:**
1. Create mock Kodi endpoint for testing
2. Test each transport with mock data
3. Test error cases (timeout, connection failed, invalid response)
4. Document test coverage
5. Add CI integration if needed

---

### update-readme

**Description:** Keep project/README.md up to date with current architecture and usage.

**Use when:**
- After major changes
- Adding new endpoints
- Documenting config options

**Steps:**
1. Review current README
2. Update architecture diagram if needed
3. Document new endpoints/tools
4. Update config section with all environment variables
5. Add usage examples

---

### validate-repo-server

**Description:** Test and validate the repo server functionality.

**Use when:**
- After modifying repo serving code
- Adding new addon packages
- Validating repo serving for Kodi

**Steps:**
1. Start repo server on configured port
2. Verify endpoints are accessible
3. Test addon package serving
4. Validate package indexing
5. Document repo server usage

---

### git-review

**Description:** Review git status in both workspace root and project/ before or after work.

**Use when:**
- Before starting any meaningful task
- After completing a task
- When asked about current state

**Steps:**
1. Run `git status` in workspace root
2. Run `git status` in project/
3. Report: branch, tracking, uncommitted files, untracked files
4. Warn if uncommitted changes exist before unrelated work
5. Suggest next action (commit, push, or continue)

---

### update-current-state

**Description:** Update CURRENT_STATE.md after meaningful changes.

**Use when:**
- After any code change
- After any architecture documentation update
- When known gaps change
- After fixing a gap identified in this file

**Steps:**
1. Review what changed (code, config, endpoints)
2. Update "What Exists" section if relevant
3. Update "Known Gaps" section if gaps changed
4. Update "Next Steps" section if priorities shifted
5. Add date stamp: **Last updated:** [today's date]
6. Confirm: "Definition of Done" items are still met

---

## General Guidelines

- **Read before you write** — Always examine existing code before making changes
- **Check CURRENT_STATE.md first** — Know what exists and what gaps remain
- **Small commits** — Keep changes focused and testable
- **Document as you go** — Update README and CURRENT_STATE.md after changes
- **Test incrementally** — Don't refactor everything at once
- **Think CLI-first** — Design endpoints with future CLI wrappers in mind
- **CLI wrappers do NOT exist yet** — they are future work, not current

---

These skills are for backend server development. They are NOT for direct Kodi addon development or deployment.
