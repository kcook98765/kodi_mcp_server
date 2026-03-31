# BOOTSTRAP.md - Session Start / Resume Checklist

_When starting a new session or resuming work, run through this checklist._

## Session Start Checklist

1. **Read CURRENT_STATE.md first**
   - Know what exists and what gaps remain
   - Identify any outstanding next steps

2. **Inspect project/ structure**
   - Only after reviewing CURRENT_STATE.md
   - `find . -type f -name "*.py"` to see what exists

3. **Check git state**
   - `git status` in workspace root
   - `git status` in project/
   - Any uncommitted changes? Any untracked files?

4. **Verify task type**
   - Is this **backend-server work** (`project/`)?
   - Or **future wrapper work** (not yet implemented)?
   - CLI wrappers do NOT exist yet — do not assume they do

5. **Start work**
   - Follow the Git Workflow Enforcement rules in AGENTS.md
   - Update CURRENT_STATE.md after any meaningful change

---

**Note:** This file is a session-start checklist, not onboarding. The workspace is already configured — use this to get oriented quickly.
