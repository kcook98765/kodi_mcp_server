# USER.md - About Your Human

_Learn about the person you're helping. Update this as you go._

- **Name:**
- **What to call them:**
- **Pronouns:** _(optional)_
- **Timezone:**
- **Notes:**

## Context

This user wants a **local-first, controlled, deterministic interface for remote Kodi operations**.

### What They're Building

They're not looking for a generic Kodi controller. They're building:

1. A **custom middle-layer server** that sits between local CLI tools and remote Kodi
2. **Structured, machine-friendly responses** for automation
3. **Safe, incremental changes** with clear rollback paths
4. **Future CLI wrappers** that the agent can invoke without touching internals

### What They Care About

- **Reliability** — the server shouldn't break
- **Predictability** — same inputs = same outputs
- **Clear architecture** — so future work has context
- **Separation** — backend server vs. CLI interface vs. agent interface

### What They Don't Want

- Ad-hoc scripting
- Direct raw API calls from agent messages
- Generic MCP language that doesn't fit this custom setup
- Magic — if something needs config, document it

### Working Style

- They prefer **code-first** communication
- They want you to **read the codebase** before suggesting changes
- They value **structured output** and **deterministic behavior**
- They appreciate **clear summaries** of what exists before you modify it

---

The more you know, the better you can help. But remember — you're learning about a person, not building a dossier. Respect the difference.
