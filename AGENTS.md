# GlanceCam: Agent Instructions

Canonical instructions for every AI agent working in this repo (Claude Code,
Codex, and anything else). `CLAUDE.md` is just a pointer to this file; edit
here, not there.

> **ALWAYS USE BEADS, AND THE WORK IS NOT DONE UNTIL IT IS COMMITTED AND
> PUSHED.** This repo tracks ALL work in **bd (beads)**, never in markdown
> TODOs, TodoWrite, or ad-hoc lists. Start every session with `bd prime`,
> pick work with `bd ready`, claim it (`bd update <id> --claim`), and close
> it when done (`bd close <id>`). File a new bead for ANY follow-up work you
> discover. Closing a bead means the change is tested, committed on `main`,
> and pushed to GitHub; never end a beads session with unpushed work.

## What This Is

GlanceCam is a minimalist, LAN-only IP camera viewer for quick glances. No
NVR, no recording, no cloud. A FastAPI service (port 9292) owns the stream
table of a bundled **go2rtc** engine (RTSP/ONVIF/HTTP in, WebRTC/MSE/MJPEG
out) and serves one grid to any LAN browser and to an optional Raspberry Pi
kiosk. The app never transcodes; go2rtc remuxes only.

Deployment targets: Docker on a server, native on a Raspberry Pi (with an
optional attached kiosk display), and any PC as a pure browser client.

## Brand and Identifiers

- Product name: **GlanceCam** (`APP_NAME` in `service/app/config.py`).
- Internal identifier is **`glancecam`** everywhere: paths, systemd units,
  the env prefix `GLANCECAM_`, and the `/health` `app=glancecam` field. No
  legacy naming.
- Published image is the literal string
  **`ghcr.io/syracuse3dprintingorg/glancecam`**. The publish workflow PINS
  this name; never derive it from `github.repository` (a fork or a rename of
  the repo would otherwise publish under the wrong name and break the fleet's
  update path).

## Repositories

GlanceCam is a single public repo. There is no dev/public split: development
happens here, in the open, on **`main`** directly.

## Writing Style

Applies to ALL project content: code comments, docs, README, CHANGELOG,
commit messages, and UI copy.

- No em-dashes. Use commas, parentheses, colons, or rewrite the sentence.
- No ASCII line or box diagrams.
- The goal is copy that reads as human-written; avoid LLM tells generally.
- Docs and UI copy are **user-forward**: written for the app's end user, not
  as notes to the developer. No option-weighing that reads like an agent
  asking for feedback, and no copy that describes the software from the
  builder's side ("Update now pulls the new image" style). Check for this
  before shipping any doc or UI text.
- Code comments explain non-obvious constraints only, not the obvious.

## Service Architecture

- `service/app/main.py`: FastAPI app; middleware order matters. Starlette
  runs the LAST-ADDED middleware OUTERMOST, so registration order is the
  reverse of execution order. The optional settings-auth gate is added
  before (runs inside) SessionMiddleware, which must see `request.session`.
- `service/app/config.py`: pydantic-settings `Settings`; env vars (prefix
  `GLANCECAM_`) override the `data/settings.json` overlay. `_SAVEABLE` lists
  persistable keys. Holds `APP_VERSION`. The hardened `save()` preserves a
  corrupt file aside, keeps a `.bak` rollback, writes atomically at chmod
  0600, and hashes password fields at rest.
- `service/app/statefile.py`: generic atomic, mtime-gated JSON state helper
  (temp file plus `os.replace`, cached reads, silent in-memory degradation
  when the data dir is unwritable).
- `service/app/services/`: `cameras.py` (the camera store), `go2rtc.py`
  (stream table client), `netguard.py` (SSRF guard), `resources.py`
  (hardware detection and stream budget), `discovery/` (LAN/ONVIF/Reolink/HA
  probes).
- `service/app/routers/`: `ui.py`, `cameras.py`, `settings.py`, `system.py`,
  `discovery.py`.
- Storage is JSON files under `data/` (`settings.json`, `cameras.json`),
  written atomically. No database.
- Camera credentials never leave the server. API responses replace them with
  a `"__set__"` sentinel; an update carrying the sentinel keeps the stored
  value. Credentials are embedded in the RTSP URL handed to go2rtc only
  (server to server), never sent to a browser.

## Conventions and Gotchas

- The `/go2rtc/*` reverse proxy route is the only origin the LAN browser
  talks to for streams, so a single port faces the LAN and the optional
  settings password can gate it later.
- SSRF guard: an arbitrary test URL fails CLOSED (an unresolvable or internal
  host is refused); a saved camera fails OPEN (a momentary DNS hiccup does not
  turn a real camera into a blocked one). Always `verify=True` unless the user
  explicitly toggles a per-camera `allow_self_signed`.
- Kiosk behaviors (auto-fullscreen, cursor hide, no settings gear) apply only
  when the browser is on loopback AND the server reports `is_pi`.

## Build and Test

```bash
docker compose up -d --build          # dev stack (app + go2rtc)

# local smoke test deps:
pip install fastapi jinja2 itsdangerous pillow python-multipart pydantic-settings httpx uvicorn websockets

# import smoke test:
python -c "import sys; sys.path.insert(0,'service'); from app.main import app"

# tests (pure logic, no network or Docker needed):
pip install pytest && python -m pytest tests/ -q
```

**Definition of done:** before handing off a code change, run
`python -m pytest tests/ -q` (the suite is pure logic and cheap) and the
import smoke test above. A user-facing change also needs a CHANGELOG entry.

## Versioning

- `APP_VERSION` in `service/app/config.py` is the single source of truth
  (major.minor.patch). The project is pre-1.0: `1.0.0` is reserved for the
  first public release, so stay in `0.x` until then.
- **Every user-facing change gets a CHANGELOG entry** under `[Unreleased]`
  in the appropriate Added/Changed/Fixed section, written in the existing
  plain-prose style (a bold one-line summary, then what it means for the
  user). The changelog doubles as the GitHub Release description, so write
  for users, not for developers.

## Authorship and Git

- **All commits are authored by
  `BillNyeDegrasseTyson <BillNyeDegrasseTyson@users.noreply.github.com>`**.
  Never add Co-Authored-By trailers, AI attributions, or session links to
  commit messages.
- Development happens on **`main`** directly.
- **Commit and push are part of done** for beads work and any load-bearing
  change (code, docs, instructions, provisioning). The deployed fleet updates
  from GitHub, so an unpushed change has not shipped. Do not wait to be asked.

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations. `cp`, `mv`, and
`rm` may be aliased to `-i` on some systems, which hangs an agent waiting for
y/n input. Use `cp -f`, `mv -f`, `rm -f` (and `-rf` for recursive
operations). Similarly: `scp`/`ssh` with `-o BatchMode=yes`, `apt-get -y`,
and `HOMEBREW_NO_AUTO_UPDATE=1` for `brew`.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
