---
name: status-bar-design
description: Add a git dirty flag and FastAPI health check to Claude Code status bar
metadata:
  type: reference
---

Add a compact status‑line entry that shows:

- **Git dirty flag** – a single ✗ when `git status --porcelain` reports any changes, otherwise nothing.
- **FastAPI health** – HTTP status code from `curl -I -s http://localhost:8000/` (e.g., `200`).

Implementation will use the built‑in `statusline-setup` agent to edit `.claude/settings.json` (or create it) with two shell snippets that emit the values, formatted as `git:✗ | api:200`.
