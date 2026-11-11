# ClipApp

## Context
ClipApp is a desktop app for personal use (may be published later) that processes gameplay recordings:
1. Automatically removes silences by analyzing the microphone track.
2. Lets the user pick clips by hand (35 s default, 15–60 s range) in 16:9 or 9:16.
3. Generates local subtitles per clip, lets the user correct and style them, and burns them into the video.
4. Batch export.

No cloud AI, no paid APIs, no telemetry. Everything runs locally and free.

## Stack (change it when the app needs it; justify in the stage summary)
- `engine/`: Python 3.12 with uv. Typer (CLI), FastAPI + uvicorn (local API), pydantic v2, numpy,
  faster-whisper, pysubs2, SQLite (stdlib `sqlite3`). FFmpeg and ffprobe via subprocess.
- `app/`: Tauri 2 + React + TypeScript strict + Vite, Tailwind, Zustand, TanStack Query,
  React Router, wavesurfer.js v7.
- The engine runs as a local process (sidecar) on 127.0.0.1 with a per-session token. The UI never
  opens the original video: it only consumes the proxy, the waveform and data from the engine.

## Hardware and OS
- Windows 11. Main PC: Intel i5 + NVIDIA GTX 1650 Ti (4 GB VRAM): NVENC for encoding and Whisper on
  GPU with `compute_type=int8_float16`. Secondary PC: Ryzen 7 with integrated Radeon: AMF for encoding
  and Whisper on CPU. Code detects hardware at runtime; never assume a fixed setup.
- Recordings come from OBS as multitrack MKV: track 1 mix, track 2 mic only, track 3 game only.

## Repo structure
- `CLAUDE.md`, `README.md`, `.gitignore`
- `docs/` (`api.md`, `decisions/`)
- `engine/pyproject.toml`
- `engine/src/clipapp_engine/`: `cli.py`, `config.py`, `doctor.py`, `media/`, `silence/`,
  `transcribe/`, `subtitles/`, `export/`, `jobs/`, `db/`, `api/`
- `engine/assets/fonts/`, `engine/tests/`
- `app/`: `src/api`, `src/features`, `src/components`, `src/stores`, `src-tauri/`
- `scripts/`, `samples/` (git-ignored)

## Code rules
- Comments and docstrings in English. Document modules, classes and public functions (purpose,
  args, return, errors). No line-by-line comments.
- Python: type hints everywhere, `ruff` and `mypy --strict` clean, pytest for every engine function.
- TypeScript: strict, no `any`, ESLint and Prettier clean.
- Non-destructive editing: never modify the original video; edits are stored as data and rendered on export.
- FFmpeg: always argument lists (never shell strings), progress via `-progress pipe:1`, paths with
  spaces and accents supported.
- Tests use synthetic media generated with FFmpeg lavfi, never files from `samples/`.
- Add dependencies or change the architecture when needed to make the app work, without asking;
  list every such change and its reason in the stage summary.

## Stage workflow
1. Present a short plan (files, functions, tests), then implement it without waiting for approval.
2. Implement only the stage scope, nothing from future stages.
3. Run tests and linters and fix until they pass.
4. Finish with: summary, commands to try it by hand, and pending items. Then commit with message
   `etapa N: <summary>` and create tag `etapa-N`.

## Commands
Engine (run from `engine/`):
- Tests: `uv run pytest`
- Lint: `uv run ruff check .` and `uv run ruff format --check .`
- Types: `uv run mypy`
- CLI: `uv run clipapp-engine --help`, `uv run clipapp-engine doctor [--json]`
- Slow tests (real model, need `samples/voz.wav`): `uv run pytest -m slow`
- Sample media: `uv run python ../scripts/make_samples.py`
- Subtitle previews (one PNG per template/aspect in `samples/previews/`): `uv run python ../scripts/preview_styles.py [--video f.mkv]`

App (run from `app/`):
- Dev: `npm run tauri dev`
- Lint: `npm run lint`
- Typecheck: `npm run typecheck`
- Format: `npm run format`

Env: `CLIPAPP_FFMPEG_DIR` overrides where `ffmpeg.exe`/`ffprobe.exe` are found (otherwise PATH).
`CLIPAPP_WHISPER_MODEL`, `CLIPAPP_WHISPER_DEVICE` (`cuda`/`cpu`), `CLIPAPP_WHISPER_COMPUTE_TYPE`
force the transcription setup (otherwise auto-detected). GPU needs `uv sync --extra cuda`.
Data dir: `%APPDATA%/clipapp` with `cache/`, `models/`, `logs/`.
