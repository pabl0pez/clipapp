# ClipApp

Desktop app to process gameplay recordings locally: removes silences using the mic track, lets you
pick clips (16:9 or 9:16), generates and burns local subtitles, and batch-exports. No cloud, no paid
APIs, no telemetry.

- `engine/` — Python 3.12 engine (CLI + local API). `cd engine && uv run clipapp-engine doctor`
- `app/` — Tauri 2 + React UI. `cd app && npm install && npm run tauri dev`

See `CLAUDE.md` for architecture, rules and commands.
