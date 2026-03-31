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

## General Guidelines

- **Read before you write** — Always examine existing code before making changes
- **Small commits** — Keep changes focused and testable
- **Document as you go** — Update README and CURRENT_STATE.md regularly
- **Test incrementally** — Don't refactor everything at once
- **Think CLI-first** — Design endpoints with future CLI wrappers in mind

---

These skills are for backend server development. They are NOT for direct Kodi addon development or deployment.
