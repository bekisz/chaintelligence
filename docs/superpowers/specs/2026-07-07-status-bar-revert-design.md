---
name: status-bar-revert-design
description: Revert Claude Code status bar to the default layout (pwd, branch, model, context usage) and delete the custom status‑line script.
metadata:
  type: reference
---

**Goal**

Return the status line to the built‑in default, which already displays:

- Current working directory (basename)
- Current Git branch
- Active model name
- Context‑window usage (when available)

**Changes required**

1. **Edit `.claude/settings.json`**
   - Remove the `"statusLine"` entry that points to `~/.claude/statusline_custom.sh`.
   - Preserve any other settings.
2. **Delete the custom script**
   - Remove `~/.claude/statusline_custom.sh`.

**Rationale**

The default status line already provides the information you want, and removing the custom command avoids the extra dot rendering issue while keeping the UI familiar.

**Verification steps**

- Restart Claude Code (or reload the session) and confirm the status bar shows the original components without the custom git flag or API dot.
- Verify no errors appear in the CLI output related to missing status‑line configuration.
