# Changelog

All notable changes to Starling are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [1.3.0-alpha] — 2026-04-13

This release focuses on **wizard polish, full backup/restore, and a
production-grade security + correctness pass**. The setup wizard is now the
canonical interface for everything that touches models, providers, and
agents, with the same two-level provider picker used in every flow.

### New features

#### Backup & restore (full fidelity)
- **`starling export`** CLI subcommand and TUI buttons (Config tab) to write
  a single-file `.starling` backup. Default behavior **includes secrets**
  (API keys from work-dir `.env`, Telegram bot token + chat ID, custom model
  presets + builtin overrides, cron jobs, custom skill `.py` files) so a
  restore is zero-setup.
- `--strip` flag for share-safe exports without secrets.
- **Numbered import picker** in the setup wizard: lists every backup in your
  configured directory with date/size/`[with secrets]` markers. Pick by
  number, or `d <num>` to delete with `delete` confirmation.
- Backup files use a human-readable filename:
  `Starling export <project> 2026-04-13 17-30.starling`
- Backup metadata: `backup_name`, `starling_version`, `backup_format_version`,
  `exported_at_human`, `has_secrets`.
- Files containing secrets are written `chmod 600`; failure to set perms
  warns the user instead of silently lying.

#### User preferences (`~/.config/starling/preferences.json`)
- New `preferences` module storing user-scoped settings outside the install
  directory so they survive reinstall.
- First-time backup directory prompt; blank keeps the current default
  (`~/starling-backups`), non-blank saves as the new default.
- Custom **model presets** now persist to `~/.config/starling/model_presets.json`
  (with one-time legacy fallback to the install dir). Atomic tmp+rename writes
  prevent partial files. Custom presets survive Starling reinstall.

#### Two-level model picker (canonical everywhere)
- Single `_step_model` entry point for any flow that selects a model. First
  call shows a provider overview; subsequent calls go straight to the
  filtered model list. No more flat "list every preset" picker.
- Provider config page exposes:
  - `k` — Enter / update API key (key value only — env var name is implicit)
  - `u` — Set / override base URL
  - `m` — Add another model ID under this provider
  - `e` — Edit any model preset's details (built-in or custom — overrides
    persist in `model_presets.json`)
  - `r` — Reset a built-in model to its shipped defaults
  - `x` — Delete a custom provider, or one of its custom presets
  - `t` — Test connection (local providers only)

#### Setup wizard polish
- **Three-path entry**: Quick start (1 agent, ~5 prompts), Team setup
  (multi-agent with Leader designation), or Advanced (full control).
- **"+ Build custom agent" option** in template picker — define id, name,
  role, goal, backstory from scratch instead of starting from a template.
- **Quit (`q`) suppressed** inside sub-flows (provider overview, API key
  entry, custom model form, custom agent form). Use `b` to go back. Quit
  is only available on the main pick screens.
- **Embedding model prefetch** at the end of every setup path. Downloads
  `all-MiniLM-L6-v2` (routing) and `nomic-embed-text-v1.5` (memory) up front
  to `~/.cache/starling/embeddings/` so the daemon never blocks on first use.
  Models are CPU-only and cached persistently across reboots.
- Manager name block now hard-rejects "manager" (case-insensitive) in agent
  id, role, and name fields with suggested alternatives.

#### TUI improvements
- **Config tab — Export buttons**: "Export backup (with secrets)" and
  "Export share-safe (no secrets)". Result is logged to the Config tab.
- **Models tab — coloring**: each preset in the sidebar renders fully green
  if its API key is configured (or it's a local model), fully red otherwise.
  Removed the separate `[ok]` / `[no key]` text badges.

#### CLI
- `starling export [path] [--strip]` — write a `.starling` backup.
- `starling version` now reads from a single-source `__version__.py` and
  prints the actual current version (was hard-coded to 1.0.0).

### Security hardening

- **`.env` injection prevention**: `_sanitize_env_pair` validates env var
  names against `^[A-Z_][A-Z0-9_]*$` and rejects any value containing
  newline, carriage return, or null byte before writing. Applied at every
  `.env` write site (Team Setup finalize and import restore). Without this,
  a malicious `.starling` could inject `LD_PRELOAD` or `PATH` via crafted
  values.
- **Backup `work_dir` traversal blocked**: import always derives the work
  directory from the project name, ignoring any path embedded in the
  backup. Filename slug is char-stripped to `[A-Za-z0-9_-]`. Without this,
  a malicious backup could write JSON / `.env` content to `~/.ssh/`,
  `/etc/`, `~/.bashrc`, etc.
- **Skill file restoration hardened**: `os.path.basename` equality, null-byte
  rejection, leading-dot rejection, `.py` extension lock, and
  `realpath`-anchored destination check. Catches null-byte truncation
  attacks, hidden dotfiles, path traversal, and symlink-based escapes.
- **Cron jobs from backup forced to `pending_approval`**: a malicious
  backup can no longer ship pre-activated cron jobs that auto-fire against
  the user's LLM-connected crew on the next heartbeat tick. Quarantined
  jobs are surfaced in a post-import warning.
- **Telegram free-text auto-crew gated by config flag**: free-text messages
  no longer auto-run a full crew. Now requires
  `telegram.allow_free_text_crew: true` in `project_config.json` (default
  off). Plain messages get a hint reply listing valid commands.
- **`.starling` size cap**: reject files larger than 10 MB before
  `json.load`, preventing nuisance OOM/CPU via crafted huge files.
- **Bot token masking** in `telegram show`: now displays only the bot ID
  prefix + a placeholder. Chat ID middle digits also masked.
- **Process killer hardened**: `_kill_stale_tui_processes` now requires
  the process command line to reference *this* install's directory, not
  just the substring "starling" — prevents accidental SIGKILL on unrelated
  processes that happen to contain "starling" in argv.
- **Shell injection eliminated**: replaced `os.system(f"...")` with
  `subprocess.run([...])` for `update-desktop-database`. No code paths
  remain that pass user-influenced strings through `/bin/sh -c`.

### Bug fixes

- **Team Setup never persisted API keys**: `_finalize_team_setup` collected
  keys via the provider overview but only set them in the running process
  env. They were lost on exit, and exports always showed `api_keys: []`.
  Now writes them to `work_dir/.env` (chmod 600).
- **Custom agent crash**: `_step_agents_loop` referenced an `agent_id`
  local variable that was only assigned in the template-path branch.
  Custom agents triggered `UnboundLocalError` after model selection. Fixed
  by reading `agent["id"]` after the dict is built.
- **Alibaba (Qwen) preset corrected**: `DASHSCOPE_API_KEY` (was
  `ALIBABA_API_KEY`) and `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
  (was the non-existent `dashscope-us.aliyuncs.com`).
- **`save_custom_presets` was discarding builtin overrides**: when you
  edited a builtin preset, `e` would change in-memory state but the override
  was filtered out on save. Now diffs against `BUILTIN_PRESETS` and persists
  any override. Reset (`r`) removes the override and restores defaults.
- **Heartbeat queue corruption under concurrent access**: `_load_queue` /
  `_save_queue` had no lock. The TUI thread, heartbeat thread, cron engine,
  and Telegram listener all wrote to `task_queue.json` simultaneously, with
  silent partial-write corruption. Now serialized through a module-level
  `RLock` and writes are atomic (tmp + `os.replace`). All read-modify-write
  paths (`add_task`, `update_task`, `clear_done`, `_recover_stale_tasks`)
  hold the lock for the full operation.
- **`requeue_recurring` race**: previously assumed `tasks[-1]` was the
  task we just added — wrong under concurrency. Now uses the id returned
  by `add_task` to target the recurrence stamp.
- **`next_run` lexicographic comparison**: ISO timestamps with mismatched
  microsecond precision could sort wrong, causing recurring tasks to fire
  at the wrong moment. Now parses both sides as `datetime` objects.
- **Silent `Agent(llm=None)` failures**: when a preset failed to build an
  LLM (typically a missing API key env var), agent construction continued
  with `llm=None` and the failure surfaced much later as an opaque Pydantic
  error inside `crew.kickoff()`. Now collects all LLM failures upfront and
  raises a single `ValueError` listing every broken agent + reason.
- **`remove_cron("")` was a delete-all footgun**: an empty job ID matches
  every cron job because every string ends with `""`. Now refuses with a
  warning.
- **Heartbeat loop swallowed all exceptions silently**: a systematic bug in
  `on_tick` would loop forever with no log entry. Now `logger.exception`
  surfaces the traceback to `starling_daemon.log` while still keeping the
  loop alive.
- **`daemon.start` log FD leak**: if `subprocess.Popen` raised, the opened
  log file descriptor leaked. Now in a `with` block.
- **chmod failures during export/import are no longer silent**: previously
  the success banner claimed perms were set even when chmod failed. Now
  warns explicitly that the file may be world-readable.
- **TUI froze on slow LM Studio**: `_preset_available()` ran a 1-second
  `urllib.request.urlopen` per local preset inside `compose()`, blocking
  the TUI startup for up to N seconds. Replaced with a non-blocking heuristic
  filter; reachability checks belong on a background refresh.
- **Import preview always showed "unknown" / 0**: preview screen was
  reading the legacy backup schema (`meta`, `custom_presets`, `skill_names`)
  instead of the current keys (`exported_at_human`, `model_presets`,
  `skill_files`). Now reads current keys with backwards-compat fallback.
- **Import recursion**: validation failure on a picked backup recursed
  into `_run_import_flow`, growing the call stack on each retry. Now
  validates inside the loop and `continue`s.

### Performance

- **`crew_memory.delete_by_content`** no longer fetches 10,000 rows per
  call to do a substring match. Now uses server-side filter `agent_id = ?`
  and caps the scan at 2,000 rows with a warning if hit.
- **`crew_memory._enforce_limits`** uses `count_rows` (with optional
  filter) first and only fetches per-agent rows when the agent is actually
  over the limit. Was a full-table scan + Python sort per daily compact.
- **`semantic_router._get_db`** caches the LanceDB connection at module
  scope (was opening a new one per routing query).
- **`semantic_router._trim_dedup_table`** dropped the implicit pandas
  dependency. Now uses LanceDB's native API for sort + delete.

### Architecture / hygiene

- **Single-source version**: new `__version__.py` consumed by `cli.py
  version` and `setup_wizard.export_backup`. Eliminates drift.
- **Embedding cache pinned**: both `semantic_router` and `crew_memory` pass
  an explicit `cache_dir=~/.cache/starling/embeddings` to fastembed,
  overriding fastembed's default `/tmp/fastembed_cache/` (which clears on
  reboot, forcing redownload).
- **`pyproject.toml`** gained a `[build-system]` block so `uv pip install
  -e .` correctly registers the `starling` entry point. Added
  `preferences` and `__version__` to `py-modules`.
- **`.gitignore`** additions: `*.bak`, `*.bak-*`, `*.orig`, `.env.local`,
  `*.egg-info/`, `.DS_Store`, `*.swp`.
- **`config_loader`** now documents `CREWUI_CONFIG` and
  `~/.config/crewui/` as legacy fallbacks slated for removal in v1.5.

### Removed / deprecated

- The `[ok]` / `[no key]` text badges on the TUI Models tab were replaced
  by full-text color (green/red).
- `_finalize_team_setup` no longer leaves API keys in process memory only.

### Known limitations (not blocking ship)

- TUI integration of "+ New model" / "+ New agent" buttons that launch
  the wizard sub-flow (in-TUI via `app.suspend()`) is deferred to a future
  release.
- LM Studio reachability indicator on the Models tab needs to move to a
  background refresh (currently shows configured-key state instead).

---

## [1.1.0-alpha] — 2026-04-11

Last public release. See git history for details.
