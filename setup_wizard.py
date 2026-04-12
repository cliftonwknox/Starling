"""Starling Setup Wizard — Interactive first-run configuration.

Entry points:
    run_setup()   — CLI entry; pre-start menu → path picker → dispatch
    _run_full_wizard()  — Advanced path (the original full flow)
    _run_quick_start()  — Quick Start path (5 steps, template-based, 1 agent)
    _run_team_setup()   — Team Setup path (delegates to full wizard for now)

Navigation sentinels (returned by step functions):
    _BACK  — user wants to go back to the previous step
    _SKIP  — user wants to skip this step (only when field is skippable)
    _QUIT  — user wants to exit the wizard
"""

import os
import json
import sys
import readline  # enables line editing, history, and arrow keys in input()

import theme

COLORS = ["cyan", "green", "yellow", "magenta", "blue", "red", "white", "orange"]
MAX_AGENTS = 10

# Navigation sentinels — objects, not strings, so no collision with user input
_BACK = object()
_SKIP = object()
_QUIT = object()
_DONE = object()  # signals "another wizard path completed successfully — exit quietly"


def _nav_hint(skippable: bool = False) -> str:
    """Return the standard navigation hint line."""
    parts = ["Enter = next", "b = back", "q = quit"]
    if skippable:
        parts.insert(2, "s = skip")
    return theme.color("  (" + " | ".join(parts) + ")", "muted")


def _prompt_nav(label: str, default: str = "", hint: str = "", skippable: bool = False,
                required: bool = False):
    """Prompt with nav support. Returns the user's value, or a sentinel.

    Returns:
        - str: the user's answer (possibly default)
        - _BACK: user typed 'b' (or variants) — caller should pop state
        - _SKIP: user typed 's' — only if skippable=True, else treated as text
        - _QUIT: user typed 'q' and confirmed
    """
    while True:
        prompt = theme.prompt_text(label, default=default, hint=hint)
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return _QUIT

        low = raw.lower()
        if low == "b":
            return _BACK
        if low == "q":
            # Confirm quit
            try:
                confirm = input(theme.color("  Quit without saving? [y/N]: ", "warning")).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return _QUIT
            if confirm == "y":
                return _QUIT
            continue  # re-prompt
        if low == "s" and skippable:
            return _SKIP

        # Normal answer path
        if not raw and default:
            return default
        if required and not raw:
            theme.error("This field is required.")
            continue
        return raw


def _pick_option(label: str, options: list, default_index: int = 0,
                 skippable: bool = False) -> object:
    """Numbered option picker with nav support.

    Args:
        label: Prompt label (shown above the options).
        options: List of (display_name, value) tuples OR plain strings.
        default_index: 0-based index of the default option.
        skippable: Whether 's' skip is allowed.

    Returns:
        The selected value (second element of tuple, or the string if list of strings),
        or _BACK/_SKIP/_QUIT sentinel.
    """
    # Normalize to (display, value) tuples
    norm = [(o, o) if isinstance(o, str) else o for o in options]

    while True:
        print()
        for i, (disp, _) in enumerate(norm, 1):
            marker = theme.color("  *", "accent") if (i - 1) == default_index else ""
            print(f"    {theme.color(str(i), 'highlight')}) {disp}{marker}")

        default_str = str(default_index + 1) if norm else ""
        raw = _prompt_nav(label, default=default_str, skippable=skippable)
        if raw in (_BACK, _SKIP, _QUIT):
            return raw
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(norm):
                return norm[idx][1]
        except (ValueError, TypeError):
            pass
        theme.error(f"Pick a number 1-{len(norm)}.")


def _contains_manager(value: str) -> bool:
    """Check if a string contains the blocked 'manager' keyword (case-insensitive)."""
    return "manager" in (value or "").lower()


def _print_manager_block(field: str):
    """Print the standard block message for a manager keyword violation."""
    print(f"  BLOCKED: 'manager' is not allowed in {field}.")
    print(f"    CrewAI strips tool access from agents with 'manager' in the name.")
    print(f"    Use 'coordinator', 'lead', 'director', or 'supervisor' instead.")


def _preset_available(key: str, preset: dict) -> bool:
    """Check if a model preset is usable — has API key set, or local server reachable.

    Returns False for malformed/unreachable presets. Cloud presets without an
    api_key_env set are treated as unavailable (there's no way to reach them).
    """
    if not isinstance(preset, dict):
        return False
    key_env = preset.get("api_key_env")
    if key_env:
        return bool(os.environ.get(key_env))
    base_url = preset.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        # No key and no URL — can't reach this preset
        return False
    # Local models (lm-studio, ollama): ping the server with a short timeout
    if "127.0.0.1" in base_url or "localhost" in base_url:
        import urllib.request
        try:
            urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=1)
            return True
        except Exception:
            return False
    # Remote URL without an api_key_env — we have no credentials, so unavailable
    return False


def _prompt(text, default="", required=False):
    while True:
        if default:
            result = input(f"  {text} [{default}]: ").strip()
            return result if result else default
        result = input(f"  {text}: ").strip()
        if result or not required:
            return result
        print("  This field is required. Please enter a value.")


def _prompt_yn(text, default=True):
    d = "Y/n" if default else "y/N"
    result = input(f"  {text} [{d}]: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


def _prompt_int(text, default=1, min_val=1, max_val=100):
    while True:
        result = _prompt(text, str(default))
        try:
            val = int(result)
            if min_val <= val <= max_val:
                return val
            print(f"  Must be between {min_val} and {max_val}.")
        except ValueError:
            print("  Enter a number.")


def _prompt_choice(text, options, default=None):
    print(f"\n  {text}")
    for i, opt in enumerate(options, 1):
        marker = " *" if default and opt == default else ""
        print(f"    {i}) {opt}{marker}")
    while True:
        choice = input(f"  Choice [1-{len(options)}]: ").strip()
        if not choice and default:
            return default
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print("  Invalid choice.")


def _banner(title):
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}\n")


def run_setup():
    """Main setup wizard entry point — pre-start menu → path picker → dispatch."""
    theme.clear_screen()
    theme.banner("Starling Setup")
    print(f"  {theme.color('Welcome to Starling', 'primary', bold=True)} — let's get your crew configured.\n")

    # Pre-start menu
    while True:
        print("  How would you like to start?\n")
        print(f"    {theme.color('1', 'highlight')}) New project")
        print(f"    {theme.color('2', 'highlight')}) Import existing config {theme.color('(not yet implemented)', 'muted')}")
        print(f"    {theme.color('3', 'highlight')}) Quit")
        try:
            choice = input(theme.color("\n  Choice [1]: ", "highlight")).strip() or "1"
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "1":
            break
        elif choice == "2":
            theme.warn("Import is not yet implemented. Coming in a future release.")
            continue
        elif choice == "3":
            print()
            return
        else:
            theme.error("Invalid choice. Pick 1, 2, or 3.")

    # Path picker
    theme.clear_screen()
    theme.banner("Pick your setup path")
    print(f"    {theme.color('1', 'highlight')}) {theme.color('Quick start', 'accent', bold=True)}  — 1 agent, template-based (~5 prompts, ~1 min)")
    print(f"    {theme.color('2', 'highlight')}) {theme.color('Team setup', 'accent', bold=True)}   — multiple agents with templates")
    print(f"    {theme.color('3', 'highlight')}) {theme.color('Advanced', 'accent', bold=True)}     — full control over every field")

    while True:
        try:
            choice = input(theme.color("\n  Choice [1]: ", "highlight")).strip() or "1"
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "1":
            _run_quick_start()
            return
        elif choice == "2":
            _run_team_setup()
            return
        elif choice == "3":
            _run_full_wizard()
            return
        else:
            theme.error("Invalid choice. Pick 1, 2, or 3.")


def _run_quick_start():
    """Quick Start flow — 5 steps, template-based, single agent.

    Flow:
        1. Project name
        2. Pick template
        3. Pick model
        4. API key (if needed and not set)
        5. Confirm + launch
    """
    # State — dict that accumulates answers. Back navigation pops keys.
    state = {}
    steps = ["project_name", "template", "model", "api_key", "confirm"]
    step_idx = 0

    def total_steps() -> int:
        # API key step is only shown if the chosen model requires one the user
        # hasn't set. We still render it as "step 4 of 5" for consistency — if
        # skipped, the user sees the confirm step labeled 5 of 5.
        return 5

    while 0 <= step_idx < len(steps):
        current = steps[step_idx]
        theme.clear_screen()
        theme.step_header(step_idx + 1, total_steps(), _step_title(current))

        result = _dispatch_quick_step(current, state)

        if result is _DONE:
            # Another wizard path (e.g. Advanced) completed successfully
            return
        if result is _QUIT:
            theme.muted("Exiting setup.")
            return
        if result is _BACK:
            if step_idx == 0:
                theme.muted("You're at the first step. Press 'q' to quit or enter a name to continue.")
                continue
            # Pop state for the CURRENT step (the one we just backed out of)
            # so the previous step's re-render uses its own cached answer as
            # default rather than our freshly-entered value
            _pop_step_state(steps[step_idx], state)
            step_idx -= 1
            continue
        if result is _SKIP:
            state[current] = None  # explicit skip marker
            step_idx += 1
            continue

        # Non-sentinel: result is already stored in state by the dispatch
        step_idx += 1

    # Finalize — state is complete
    _finalize_quick_start(state)


def _step_title(step_name: str) -> str:
    titles = {
        "project_name": "Name your project",
        "template": "Pick an agent template",
        "model": "Pick a model",
        "api_key": "API key",
        "confirm": "Review and launch",
    }
    return titles.get(step_name, step_name)


def _pop_step_state(step_name: str, state: dict):
    """Remove answers associated with a given step from state."""
    keys_per_step = {
        "project_name": ["project_name", "project_desc", "work_dir"],
        "template": ["template"],
        "model": ["model_preset", "_available_presets"],
        "api_key": ["api_key_status", "api_key_pending"],
        "confirm": [],
    }
    for k in keys_per_step.get(step_name, [step_name]):
        state.pop(k, None)


def _dispatch_quick_step(step_name: str, state: dict):
    """Run a single Quick Start step. Mutates state. Returns value or sentinel."""
    if step_name == "project_name":
        return _step_project_name(state)
    if step_name == "template":
        return _step_template(state)
    if step_name == "model":
        return _step_model(state)
    if step_name == "api_key":
        return _step_api_key(state)
    if step_name == "confirm":
        return _step_confirm(state)
    raise ValueError(f"Unknown step: {step_name}")


def _step_project_name(state: dict):
    """Step 1: project name → derive project_desc + work_dir."""
    default = state.get("project_name") or "My Crew"
    print("  Give your project a short name. This is how you'll refer to it.\n")
    print(_nav_hint())
    result = _prompt_nav("Project name", default=default, required=True)
    if result in (_BACK, _SKIP, _QUIT):
        return result
    state["project_name"] = result
    state["project_desc"] = f"Starling crew: {result}"
    state["work_dir"] = os.path.expanduser(
        f"~/starling-projects/{result.lower().replace(' ', '-')}"
    )
    return result


def _step_template(state: dict):
    """Step 2: pick an agent template."""
    try:
        from semantic_router import AGENT_TEMPLATES, list_templates
    except ImportError:
        theme.error("Templates unavailable (semantic_router import failed).")
        theme.info("Switching to Advanced wizard for manual agent creation.")
        _run_full_wizard()
        return _DONE

    templates = list_templates()
    if not templates:
        theme.warn("No agent templates are registered.")
        theme.info("Switching to Advanced wizard for manual agent creation.")
        _run_full_wizard()
        return _DONE

    options = []
    for tid, tname in templates:
        tmpl = AGENT_TEMPLATES[tid]
        purpose = tmpl.get("primary_purpose", "")[:55]
        display = f"{theme.color(tname, 'accent', bold=True):30s} — {theme.color(purpose, 'muted')}"
        options.append((display, tid))
    options.append((theme.color("Custom (build from scratch — advanced wizard)", "warning"), "_custom"))

    print("  Pick the agent type that best matches what you want done.\n")
    print(_nav_hint())
    default_idx = 0
    if state.get("template"):
        for i, (_, tid) in enumerate(options):
            if tid == state["template"]:
                default_idx = i
                break

    result = _pick_option("Template", options, default_index=default_idx)
    if result in (_BACK, _SKIP, _QUIT):
        return result
    if result == "_custom":
        theme.info("Switching to Advanced wizard for custom agent creation.")
        _run_full_wizard()
        return _DONE  # Advanced wizard took over and ran to completion
    state["template"] = result
    return result


def _step_model(state: dict):
    """Step 3: pick a model — filtered to available ones only."""
    from model_wizard import load_presets
    all_presets = load_presets()
    available = [(k, v) for k, v in all_presets.items() if _preset_available(k, v)]

    if not available:
        theme.warn("No model presets have valid API keys or are reachable locally.")
        print("    Set API keys in your environment, or start LM Studio/Ollama, then re-run setup.")
        print("    Continuing with all presets — you'll need to add a key afterwards.")
        available = list(all_presets.items())

    options = []
    for k, v in available:
        key_env = v.get("api_key_env")
        key_status = ""
        if key_env and os.environ.get(key_env):
            key_status = theme.color(" [key set]", "success")
        elif not key_env:
            key_status = theme.color(" [local]", "accent")
        display = f"{k:18s} {v.get('label', ''):30s}{key_status}"
        options.append((display, k))

    print("  Pick the LLM that will power your agent.\n")
    print(_nav_hint())
    default_idx = 0
    if state.get("model_preset"):
        for i, (_, k) in enumerate(options):
            if k == state["model_preset"]:
                default_idx = i
                break

    result = _pick_option("Model", options, default_index=default_idx)
    if result in (_BACK, _SKIP, _QUIT):
        return result
    state["model_preset"] = result
    state["_available_presets"] = dict(available)  # cached for api_key step
    return result


def _step_api_key(state: dict):
    """Step 4: prompt for API key if needed. Skippable.

    Does NOT write to disk — stores the pending key in state. Actual write
    happens in _finalize_quick_start once the user confirms. This way backing
    out and changing work_dir doesn't leave orphan .env files behind.

    Records one of: "no_key_needed", "already_set", "saved", "skipped".
    """
    from model_wizard import load_presets
    presets = state.get("_available_presets") or load_presets()
    preset_key = state.get("model_preset")
    preset = presets.get(preset_key, {})
    key_env = preset.get("api_key_env")

    # Normalize: treat empty string env var name same as None
    if not key_env:
        theme.success(f"No API key needed for {preset.get('label', preset_key)} (local model).")
        state["api_key_status"] = "no_key_needed"
        state["api_key_pending"] = None
        try:
            input(theme.color("  Press Enter to continue...", "muted"))
        except (EOFError, KeyboardInterrupt):
            return _QUIT
        return "no_key_needed"

    if os.environ.get(key_env):
        theme.success(f"{key_env} is already set in your environment.")
        state["api_key_status"] = "already_set"
        state["api_key_pending"] = None
        try:
            input(theme.color("  Press Enter to continue...", "muted"))
        except (EOFError, KeyboardInterrupt):
            return _QUIT
        return "already_set"

    print(f"  {theme.color(preset.get('label', preset_key), 'accent', bold=True)} needs an API key.")
    print(f"  Environment variable: {theme.color(key_env, 'highlight')}")
    print(f"  You can skip and add it later via the Models tab or .env file.\n")
    print(_nav_hint(skippable=True))

    result = _prompt_nav("API key", hint=f"paste value for {key_env}", skippable=True)
    if result in (_BACK, _QUIT):
        return result
    if result is _SKIP:
        theme.muted(f"Skipped — remember to set {key_env} before launching.")
        state["api_key_status"] = "skipped"
        state["api_key_pending"] = None
        return _SKIP
    # Stage the key for writing during finalize (do NOT touch work_dir yet —
    # user may go back and change it)
    state["api_key_status"] = "pending_save"
    state["api_key_pending"] = {"env_var": key_env, "value": result}
    theme.success(f"{key_env} will be saved to your work dir on confirm.")
    return result


def _save_env_key(work_dir: str, env_var: str, key: str):
    """Write a key=value line to {work_dir}/.env, preserving existing keys."""
    os.makedirs(work_dir, exist_ok=True)
    env_path = os.path.join(work_dir, ".env")
    existing = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()
    existing[env_var] = key
    with open(env_path, "w") as f:
        for k, v in existing.items():
            f.write(f"{k}={v}\n")
    os.chmod(env_path, 0o600)


def _step_confirm(state: dict):
    """Step 5: summary + launch."""
    from semantic_router import get_template
    tmpl = get_template(state["template"])
    print(f"  {theme.color('Review your setup', 'primary', bold=True)}:\n")
    print(f"    Project:    {theme.color(state['project_name'], 'accent')}")
    print(f"    Work dir:   {theme.color(state['work_dir'], 'muted')}")
    print(f"    Template:   {theme.color(tmpl['name'], 'accent')} "
          f"({theme.color(tmpl['tier'], 'highlight')})")
    print(f"    Model:      {theme.color(state['model_preset'], 'accent')}")
    api_status_map = {
        "no_key_needed": theme.color("not needed (local model)", "accent"),
        "already_set":   theme.color("already set in environment", "success"),
        "pending_save":  theme.color("will save to .env on confirm", "accent"),
        "skipped":       theme.color("[skipped — add later]", "warning"),
    }
    api_display = api_status_map.get(state.get("api_key_status"), theme.color("?", "muted"))
    print(f"    API key:    {api_display}\n")

    print(_nav_hint())
    try:
        raw = input(theme.color("  Save and launch Starling? [Y/n/b/q]: ", "highlight")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return _QUIT
    if raw == "b":
        return _BACK
    if raw == "q":
        return _QUIT
    if raw and raw != "y" and raw[0] != "y":
        theme.muted("Cancelled. Run setup again when you're ready.")
        return _QUIT
    return "confirmed"


def _finalize_quick_start(state: dict):
    """Write the config from Quick Start state and launch Starling.

    This is the single place where filesystem side effects happen — earlier
    steps stage their data in `state` but do not create directories or files.
    That way backing out of confirm never leaves orphan files behind.
    """
    # Defensive guard — we expect the state machine to enforce these, but a
    # future refactor bug shouldn't produce a user-facing KeyError stacktrace
    required = ("project_name", "project_desc", "work_dir", "template", "model_preset")
    missing = [k for k in required if not state.get(k)]
    if missing:
        theme.error(f"Internal error: missing state {missing}. Please re-run setup.")
        return

    from semantic_router import get_template

    tmpl = get_template(state["template"])
    if tmpl is None:
        theme.error(f"Template '{state['template']}' not found. Please re-run setup.")
        return

    work_dir = state["work_dir"]
    os.makedirs(work_dir, exist_ok=True)
    for sub in ("output", "memory", "skills"):
        os.makedirs(os.path.join(work_dir, sub), exist_ok=True)

    # Write API key to work dir .env only now that user has confirmed
    pending = state.get("api_key_pending")
    if pending:
        _save_env_key(work_dir, pending["env_var"], pending["value"])
        os.environ[pending["env_var"]] = pending["value"]  # apply to current session
        theme.success(f"{pending['env_var']} saved to {os.path.join(work_dir, '.env')}")

    agent = {
        "id": state["template"],
        "name": tmpl["name"],
        "role": tmpl["role"],
        "goal": tmpl["goal"],
        "backstory": tmpl["backstory"],
        "tools": list(tmpl["tools"]),
        "preset": state["model_preset"],
        "color": tmpl.get("color", "cyan"),
        "allow_delegation": False,
        "template": state["template"],
        "tier": tmpl.get("tier", "specialist"),
    }

    config = {
        "project": {
            "name": state["project_name"],
            "description": state["project_desc"],
            "work_dir": work_dir,
        },
        "agents": [agent],
        "max_agents": MAX_AGENTS,
        "default_tasks": [],
        "routing": {
            "keywords": {},
            "default_agent": agent["id"],
        },
    }

    config_path = os.path.join(os.path.dirname(__file__), "project_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    theme.clear_screen()
    theme.banner("Setup Complete!")
    theme.success(f"Config saved: {config_path}")
    print(f"  Project: {theme.color(state['project_name'], 'accent')}")
    print(f"  Agent:   {theme.color(tmpl['name'], 'accent')} on {theme.color(state['model_preset'], 'accent')}")
    print(f"  Work dir: {work_dir}\n")

    _launch_starling_or_exit(os.path.dirname(os.path.abspath(__file__)))


def _run_team_setup():
    """Team Setup path — currently delegates to the full wizard.

    In a future release this will get its own streamlined multi-agent flow.
    For now, the existing full wizard handles multiple agents well enough.
    """
    theme.muted("Team Setup uses the Advanced wizard for now. Future releases will have a dedicated flow.\n")
    try:
        input(theme.color("  Press Enter to continue...", "muted"))
    except (EOFError, KeyboardInterrupt):
        return
    _run_full_wizard()


def _launch_starling_or_exit(project_dir: str):
    """Stop any running daemon, then offer to launch Starling."""
    # Stop any running daemon so it picks up the new config
    try:
        import daemon as _daemon
        if _daemon.is_running():
            theme.muted("\n  Stopping existing Starling daemon to apply new config...")
            _daemon.stop()
    except Exception as e:
        theme.muted(f"  (Could not check/stop daemon: {e})")

    try:
        answer = input(theme.color("\n  Launch Starling now? [Y/n]: ", "highlight")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer and answer[0] != "y":
        print(f"\n  To launch later:  cd {project_dir} && uv run starling\n")
        return

    import shutil
    starling_bin = shutil.which("starling")
    if starling_bin:
        print()
        try:
            os.execv(starling_bin, [starling_bin])
        except OSError as e:
            theme.error(f"Failed to exec {starling_bin}: {e}")
            # Fall through to uv attempt
    uv_bin = shutil.which("uv")
    if uv_bin:
        try:
            os.chdir(project_dir)
            print()
            os.execv(uv_bin, [uv_bin, "run", "starling"])
        except OSError as e:
            theme.error(f"Failed to exec {uv_bin}: {e}")
    theme.warn("Could not launch Starling automatically.")
    print(f"  Run manually:  cd {project_dir} && uv run starling\n")


def _run_full_wizard():
    """Advanced path — the original full wizard flow (preserved verbatim)."""
    _banner("Starling Setup Wizard")
    print("  This wizard will configure your Starling project.\n")

    # Step 1: Project info
    project_name = _prompt("Project name", "My Crew")
    project_desc = _prompt("Project description", "AI-powered multi-agent system")

    # Step 2: Working directory
    default_dir = os.path.expanduser(f"~/starling-projects/{project_name.lower().replace(' ', '-')}")
    print(f"\n  Working directory: where output, memory, and data files go.")
    work_dir = _prompt("Working directory", default_dir)
    work_dir = os.path.expanduser(work_dir)
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(os.path.join(work_dir, "output"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "memory"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "skills"), exist_ok=True)
    print(f"  Created: {work_dir}")

    # Step 3: Agents
    _banner("Agent Setup")
    print("  Define your agents (1-10). Each agent has a role, goal, and backstory.")
    print("  The first agent is typically the leader/reviewer.\n")
    num_agents = _prompt_int("How many agents?", default=3, min_val=1, max_val=MAX_AGENTS)

    # Load presets for selection (filter to only those with valid API keys
    # or reachable local servers — avoids offering models the user can't use)
    from model_wizard import load_presets
    presets = load_presets()
    preset_keys = [k for k, v in presets.items() if _preset_available(k, v)]
    if not preset_keys:
        print("  No model presets are configured or reachable.")
        print("  Set API keys in your environment or start LM Studio/Ollama, then re-run setup.")
        preset_keys = list(presets.keys())  # fall back to all so setup can proceed

    # Load available tools
    from crew import list_available_tools
    available_tools = list_available_tools(os.path.join(work_dir, "skills"))

    agents = []
    used_ids = set()
    for i in range(num_agents):
        print(f"\n  --- Agent {i + 1} of {num_agents} ---")
        agent = _setup_agent(i, preset_keys, presets, available_tools, used_ids)
        agents.append(agent)
        used_ids.add(agent["id"])

    # Step 4: API keys
    _banner("API Key Setup")
    _check_api_keys(agents, presets, work_dir)

    # Step 5: Default tasks (optional)
    default_tasks = []
    if _prompt_yn("\n  Define default tasks (for F8 / /crew with no args)?", False):
        default_tasks = _setup_default_tasks(agents)

    # Step 6: Routing keywords
    _banner("Heartbeat Routing")
    print("  Heartbeat auto-routes tasks to agents by keywords in the description.")
    routing = _setup_routing(agents)

    # Step 7: Telegram (optional)
    if _prompt_yn("\n  Set up Telegram notifications?", False):
        import telegram_notify
        telegram_notify.cmd_setup()

    # Step 8: Build config
    config = {
        "project": {
            "name": project_name,
            "description": project_desc,
            "work_dir": work_dir,
        },
        "agents": agents,
        "max_agents": MAX_AGENTS,
        "default_tasks": default_tasks,
        "routing": routing,
    }

    # Write config
    config_path = os.path.join(os.path.dirname(__file__), "project_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"\n  Config saved: {config_path}")

    # Step 9: Desktop shortcut (optional)
    if _prompt_yn("\n  Generate desktop shortcut?", True):
        _generate_desktop_shortcut(project_name)

    _banner("Setup Complete!")
    print(f"  Project: {project_name}")
    print(f"  Agents:  {len(agents)}")
    print(f"  Work dir: {work_dir}")
    project_dir = os.path.dirname(os.path.abspath(__file__))
    _launch_starling_or_exit(project_dir)


SKILL_PACKS = {
    "leader": {
        "label": "Leader",
        "description": "Read reports, review documents, manage files",
        "tools": [
            "crewai:FileReadTool",
            "crewai:DirectoryReadTool",
            "crewai:DirectorySearchTool",
            "crewai:PDFSearchTool",
            "crewai:CSVSearchTool",
            "crewai:JSONSearchTool",
            "cron_tool",
        ],
    },
    "researcher": {
        "label": "Researcher",
        "description": "Web search, scraping, read/write documents",
        "tools": [
            "ddg_search",
            "tavily_search",
            "scrape_website",
            "crewai:FileReadTool",
            "crewai:FileWriterTool",
            "crewai:DirectoryReadTool",
            "crewai:PDFSearchTool",
            "crewai:DOCXSearchTool",
            "crewai:TXTSearchTool",
            "crewai:WebsiteSearchTool",
        ],
    },
    "coordinator": {
        "label": "Coordinator",
        "description": "Document creation, file management, data coordination",
        "tools": [
            "crewai:FileReadTool",
            "crewai:FileWriterTool",
            "crewai:DirectoryReadTool",
            "crewai:DirectorySearchTool",
            "crewai:CSVSearchTool",
            "crewai:JSONSearchTool",
            "crewai:MDXSearchTool",
            "crewai:TXTSearchTool",
        ],
    },
    "seo_marketing": {
        "label": "SEO / Marketing",
        "description": "Web research, website analysis, content marketing",
        "tools": [
            "ddg_search",
            "tavily_search",
            "scrape_website",
            "crewai:WebsiteSearchTool",
            "crewai:ScrapeElementFromWebsiteTool",
            "crewai:FileReadTool",
            "crewai:FileWriterTool",
            "crewai:GithubSearchTool",
            "crewai:YoutubeVideoSearchTool",
        ],
    },
}


def _pick_tools(available_tools: dict) -> list:
    """Let user pick a skill pack or go custom."""
    print(f"\n  Skill Packs:")
    packs = list(SKILL_PACKS.items())
    for i, (key, pack) in enumerate(packs, 1):
        print(f"    {i}) {pack['label']:18s} -- {pack['description']}")
    print(f"    {len(packs) + 1}) {'Custom':18s} -- Pick tools one at a time")
    print(f"    {len(packs) + 2}) {'None':18s} -- No tools")

    while True:
        choice = _prompt("Skill pack", "1")
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(packs):
                pack_key, pack = packs[idx - 1]
                # Filter to tools that actually exist in the registry
                tools = [t for t in pack["tools"] if t in available_tools]
                missing = [t for t in pack["tools"] if t not in available_tools]
                print(f"\n  {pack['label']} pack loaded ({len(tools)} tools):")
                for t in tools:
                    desc = available_tools[t]["description"][:45]
                    print(f"    + {t} -- {desc}")
                if missing:
                    print(f"  Skipped (not available): {', '.join(missing)}")
                return tools
            elif idx == len(packs) + 1:
                # Custom picker
                return _pick_tools_custom(available_tools)
            elif idx == len(packs) + 2:
                return []
        # Try matching by name
        lower = choice.lower().replace(" ", "_")
        if lower in SKILL_PACKS:
            pack = SKILL_PACKS[lower]
            tools = [t for t in pack["tools"] if t in available_tools]
            print(f"\n  {pack['label']} pack loaded ({len(tools)} tools):")
            for t in tools:
                desc = available_tools[t]["description"][:45]
                print(f"    + {t} -- {desc}")
            return tools
        print(f"  Invalid choice. Enter 1-{len(packs) + 2}.")


def _pick_tools_custom(available_tools: dict) -> list:
    """Interactive one-at-a-time tool picker."""
    tool_list = sorted(available_tools.keys())
    print(f"\n  Available tools ({len(tool_list)}):")
    for i, tid in enumerate(tool_list, 1):
        info = available_tools[tid]
        print(f"    {i:2d}) {tid} -- {info['description'][:40]}")

    print(f"\n  Enter tool numbers one at a time. Blank when done.")
    selected = []
    while True:
        entry = _prompt(f"Add tool ({len(selected)} selected, blank=done)", "")
        if not entry:
            break
        if entry.isdigit():
            idx = int(entry) - 1
            if 0 <= idx < len(tool_list):
                tid = tool_list[idx]
                if tid in selected:
                    print(f"  Already selected: {tid}")
                else:
                    selected.append(tid)
                    print(f"    + {tid}")
            else:
                print(f"  Invalid number. Enter 1-{len(tool_list)}.")
        elif entry in available_tools:
            if entry in selected:
                print(f"  Already selected: {entry}")
            else:
                selected.append(entry)
                print(f"    + {entry}")
        else:
            print(f"  Unknown tool: {entry}")

    if selected:
        print(f"  Selected {len(selected)} tools: {', '.join(selected)}")
    return selected


def _check_preset_key(preset_name: str, presets: dict):
    """Check if the selected preset needs an API key or local config and prompt."""
    from dotenv import load_dotenv
    source_env = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(source_env, override=False)

    preset = presets.get(preset_name, {})
    env_var = preset.get("api_key_env")

    # Local model — ask for model name and port
    if not env_var:
        provider = preset.get("provider", "").lower()
        if provider not in ("lm studio", "ollama"):
            return

        base_url = preset.get("base_url", "")
        default_port = "1234" if "lm studio" in provider else "11434"
        label = "LM Studio" if "lm studio" in provider else "Ollama"

        print(f"\n  {label} Local Model Configuration")
        print(f"  Current base URL: {base_url}")

        # Port
        port = _prompt(f"{label} port", default_port)
        if port != default_port:
            if "lm studio" in provider:
                preset["base_url"] = f"http://127.0.0.1:{port}/v1"
            else:
                preset["base_url"] = f"http://127.0.0.1:{port}"
            print(f"  Base URL set to: {preset['base_url']}")

        # Model name
        current_model = preset.get("model", "")
        print(f"\n  Which model is loaded in {label}?")
        if "lm studio" in provider:
            print(f"  Check LM Studio > Developer tab for the model identifier.")
            print(f"  Example: bartowski/qwen3.5-35b-a3b, lmstudio-community/Meta-Llama-3.1-8B")
        else:
            print(f"  Run 'ollama list' to see available models.")
            print(f"  Example: llama3.1, mistral, codellama")
        model_name = _prompt("Model name/ID", current_model)
        if model_name and model_name != current_model:
            # Ensure openai/ prefix for LM Studio
            if "lm studio" in provider and not model_name.startswith("openai/"):
                preset["model"] = f"openai/{model_name}"
            else:
                preset["model"] = model_name
            print(f"  Model set to: {preset['model']}")

        # Test connection
        if _prompt_yn(f"\n  Test {label} connection?", True):
            try:
                import litellm
                litellm.drop_params = True
                response = litellm.completion(
                    model=preset["model"],
                    messages=[{"role": "user", "content": "Say hello in one sentence."}],
                    api_base=preset["base_url"],
                    api_key="lm-studio",
                    max_tokens=50,
                    **preset.get("extra", {}),
                )
                reply = response.choices[0].message.content.strip()[:60]
                print(f"  OK: {reply}")
            except Exception as e:
                print(f"  FAILED: {str(e)[:80]}")
                print(f"  Make sure {label} is running with a model loaded.")
        return

    existing = os.environ.get(env_var)
    if existing:
        masked = existing[:4] + "..." + existing[-4:] if len(existing) > 12 else "****"
        print(f"\n  API key {env_var}: {masked} (found)")
        return

    print(f"\n  {preset_name} requires {env_var} ({preset.get('provider', '?')})")
    key = _prompt(f"Enter {env_var} (blank to skip)")
    if key:
        os.environ[env_var] = key
        # Save to .env
        existing_env = {}
        if os.path.exists(source_env):
            with open(source_env) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        existing_env[k.strip()] = v.strip()
        existing_env[env_var] = key
        with open(source_env, "w") as f:
            for k, v in existing_env.items():
                f.write(f"{k}={v}\n")
        os.chmod(source_env, 0o600)
        print(f"  Saved {env_var} to .env")
    else:
        print(f"  Skipped. You can set {env_var} later in the Models tab or .env file.")


def _setup_agent(index: int, preset_keys: list, presets: dict, available_tools: dict, used_ids: set) -> dict:
    """Configure a single agent with review/edit step."""
    # Offer templates
    try:
        from semantic_router import AGENT_TEMPLATES, list_templates
        templates = list_templates()
        print(f"\n  Agent templates (or press Enter to build from scratch):")
        for i, (tid, tname) in enumerate(templates, 1):
            tmpl = AGENT_TEMPLATES[tid]
            print(f"    {i}) {tname:20s} — {tmpl['primary_purpose'][:60]}")
        tmpl_choice = input(f"  Template [1-{len(templates)}, or Enter to skip]: ").strip()
        if tmpl_choice.isdigit() and 1 <= int(tmpl_choice) <= len(templates):
            tid, tname = templates[int(tmpl_choice) - 1]
            tmpl = AGENT_TEMPLATES[tid]
            # Safeguard: template fields shouldn't contain "manager", but
            # templates are editable data — block if a future template violates
            for field in ("id", "name", "role"):
                val = tid if field == "id" else tmpl.get(field, "")
                if _contains_manager(val):
                    _print_manager_block(f"template {field} (template '{tid}' is invalid)")
                    raise RuntimeError(f"Template '{tid}' has 'manager' in {field}")
            print(f"  Loading template: {tname}")
            print(f"  (You can edit any field below)")
            # Pre-fill and jump to the edit flow
            agent = {
                "id": tid if tid not in used_ids else f"{tid}{index + 1}",
                "name": tmpl["name"],
                "role": tmpl["role"],
                "goal": tmpl["goal"],
                "backstory": tmpl["backstory"],
                "tools": list(tmpl["tools"]),
                "preset": preset_keys[0] if preset_keys else "",
                "color": tmpl.get("color", "white"),
                "allow_delegation": False,
                "template": tid,
                "tier": tmpl.get("tier", "specialist"),
            }
            # Let user pick a model preset
            print(f"\n  Available model presets:")
            for i, key in enumerate(preset_keys, 1):
                p = presets[key]
                print(f"    {i}) {key:18s} {p['label']} via {p['provider']}")
            while True:
                choice = _prompt("Model preset", preset_keys[0] if preset_keys else "")
                if choice in presets:
                    break
                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(preset_keys):
                        choice = preset_keys[idx]
                        break
                print(f"  Enter a number or preset name.")
            agent["preset"] = choice
            return agent
    except ImportError:
        # semantic_router not available (fastembed missing) — proceed without templates
        pass
    # NOTE: RuntimeError from a template containing 'manager' is NOT caught here —
    # it propagates to the wizard's top level so the user sees the rejection.

    # ID
    while True:
        default_id = f"agent{index + 1}" if index > 0 else "leader"
        agent_id = _prompt("Agent ID (short, no spaces)", default_id).lower().replace(" ", "_")
        if agent_id in used_ids:
            print(f"  ID '{agent_id}' already taken. Pick another.")
            continue
        if _contains_manager(agent_id):
            _print_manager_block("agent IDs")
            continue
        break

    while True:
        name = _prompt("Display name", agent_id.replace("_", " ").title())
        if _contains_manager(name):
            _print_manager_block("display names")
            continue
        break

    while True:
        role = _prompt("Role (what CrewAI sees)", name)
        if _contains_manager(role):
            _print_manager_block("roles")
            continue
        break
    goal = _prompt("Goal (1-2 sentences)", f"Accomplish tasks as {role}", required=True)
    backstory = _prompt("Backstory (personality/expertise)", f"An experienced {role}", required=True)

    # Model preset
    print(f"\n  Available model presets:")
    for i, key in enumerate(preset_keys, 1):
        p = presets[key]
        print(f"    {i}) {key:18s} {p['label']} via {p['provider']}")
    print(f"\n  Enter a number (1-{len(preset_keys)}) or preset name.")
    while True:
        choice = _prompt("Model preset", preset_keys[0] if preset_keys else "")
        if choice in presets:
            break
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(preset_keys):
                choice = preset_keys[idx]
                break
            else:
                print(f"  Invalid number. Enter 1-{len(preset_keys)}.")
                continue
        print(f"  Unknown preset. Enter a number or one of: {', '.join(preset_keys[:5])}...")
    preset = choice
    _check_preset_key(preset, presets)

    # Tools — skill packs
    tools = _pick_tools(available_tools)

    # Color
    color = COLORS[index % len(COLORS)]
    custom_color = _prompt("Color", color)
    if custom_color in COLORS:
        color = custom_color

    # Delegation
    allow_delegation = _prompt_yn("Allow delegation?", index == 0)

    # Review
    agent = {
        "id": agent_id,
        "name": name,
        "role": role,
        "goal": goal,
        "backstory": backstory,
        "tools": tools,
        "preset": preset,
        "color": color,
        "allow_delegation": allow_delegation,
    }

    while True:
        print(f"\n  --- Agent Summary ---")
        fields = [
            ("1", "ID", agent["id"]),
            ("2", "Name", agent["name"]),
            ("3", "Role", agent["role"]),
            ("4", "Goal", agent["goal"][:60] + ("..." if len(agent["goal"]) > 60 else "")),
            ("5", "Backstory", agent["backstory"][:60] + ("..." if len(agent["backstory"]) > 60 else "")),
            ("6", "Preset", agent["preset"]),
            ("7", "Tools", ", ".join(agent["tools"]) if agent["tools"] else "(none)"),
            ("8", "Color", agent["color"]),
            ("9", "Delegation", "yes" if agent["allow_delegation"] else "no"),
        ]
        for num, label, val in fields:
            print(f"    {num}) {label:12s} {val}")

        edit = _prompt("\n  Edit a field? (1-9, blank to confirm)", "")
        if not edit:
            break
        if edit == "1":
            new_id = _prompt("Agent ID", agent["id"]).lower().replace(" ", "_")
            if _contains_manager(new_id):
                _print_manager_block("agent IDs")
            elif new_id in used_ids and new_id != agent["id"]:
                print(f"  ID '{new_id}' already taken.")
            else:
                agent["id"] = new_id
        elif edit == "2":
            new_name = _prompt("Display name", agent["name"])
            if _contains_manager(new_name):
                _print_manager_block("display names")
            else:
                agent["name"] = new_name
        elif edit == "3":
            new_role = _prompt("Role", agent["role"])
            if _contains_manager(new_role):
                _print_manager_block("roles")
            else:
                agent["role"] = new_role
        elif edit == "4":
            agent["goal"] = _prompt("Goal", agent["goal"], required=True)
        elif edit == "5":
            agent["backstory"] = _prompt("Backstory", agent["backstory"], required=True)
        elif edit == "6":
            new_preset = _prompt("Model preset", agent["preset"])
            if new_preset in presets or new_preset.isdigit():
                if new_preset.isdigit():
                    idx = int(new_preset) - 1
                    if 0 <= idx < len(preset_keys):
                        new_preset = preset_keys[idx]
                    else:
                        print("  Invalid number.")
                        continue
                agent["preset"] = new_preset
                _check_preset_key(new_preset, presets)
            else:
                print("  Unknown preset.")
        elif edit == "7":
            agent["tools"] = _pick_tools(available_tools)
        elif edit == "8":
            new_color = _prompt("Color", agent["color"])
            if new_color in COLORS:
                agent["color"] = new_color
            else:
                print(f"  Available: {', '.join(COLORS)}")
        elif edit == "9":
            agent["allow_delegation"] = _prompt_yn("Allow delegation?", agent["allow_delegation"])

    return agent


def _check_api_keys(agents: list, presets: dict, work_dir: str):
    """Check which API keys are needed and prompt for missing ones."""
    from dotenv import load_dotenv

    # Check both source dir and work dir for .env
    source_env = os.path.join(os.path.dirname(__file__), ".env")
    work_env = os.path.join(work_dir, ".env")

    # Load existing .env files but don't let them override — we want to ask
    load_dotenv(work_env, override=False)
    load_dotenv(source_env, override=False)

    needed = {}
    for agent in agents:
        preset = presets.get(agent.get("preset", ""))
        if preset and preset.get("api_key_env"):
            env_var = preset["api_key_env"]
            if env_var not in needed:
                needed[env_var] = {
                    "provider": preset.get("provider", "?"),
                    "agents": [],
                }
            needed[env_var]["agents"].append(agent["name"])

    # Check tool-specific keys
    for agent in agents:
        if "tavily_search" in agent.get("tools", []):
            if "TAVILY_API_KEY" not in needed:
                needed["TAVILY_API_KEY"] = {"provider": "Tavily", "agents": []}
            needed["TAVILY_API_KEY"]["agents"].append(agent["name"])

    if not needed:
        print("  No API keys needed (all local models).")
        return

    print("  The following API keys are needed:\n")
    env_updates = {}
    for env_var, info in needed.items():
        existing = os.environ.get(env_var)
        if existing:
            masked = existing[:4] + "..." + existing[-4:] if len(existing) > 12 else "****"
            print(f"  {env_var:25s} -> {info['provider']:15s} FOUND: {masked}")
            print(f"    Used by: {', '.join(info['agents'])}")
            if _prompt_yn(f"  Keep existing {env_var}?", True):
                continue
            # User wants to replace it
            key = _prompt(f"Enter new {env_var}", required=True)
            if key:
                env_updates[env_var] = key
                os.environ[env_var] = key
        else:
            print(f"  {env_var:25s} -> {info['provider']:15s} MISSING")
            print(f"    Used by: {', '.join(info['agents'])}")
            key = _prompt(f"Enter {env_var} (blank to skip)")
            if key:
                env_updates[env_var] = key
                os.environ[env_var] = key

    if env_updates:
        # Write to .env in source dir
        existing_env = {}
        if os.path.exists(source_env):
            with open(source_env) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        existing_env[k.strip()] = v.strip()
        existing_env.update(env_updates)
        with open(source_env, "w") as f:
            for k, v in existing_env.items():
                f.write(f"{k}={v}\n")
        os.chmod(source_env, 0o600)
        print(f"\n  Saved {len(env_updates)} key(s) to .env")

    # Test connections
    if _prompt_yn("\n  Test API connections?", True):
        import litellm
        litellm.drop_params = True
        for env_var, info in needed.items():
            key = os.environ.get(env_var)
            if not key:
                continue
            # Find a preset using this key
            for pname, p in presets.items():
                if p.get("api_key_env") == env_var:
                    try:
                        response = litellm.completion(
                            model=p["model"],
                            messages=[{"role": "user", "content": "Say hello in one sentence."}],
                            api_base=p["base_url"],
                            api_key=key,
                            max_tokens=50,
                            **p.get("extra", {}),
                        )
                        print(f"  {pname:18s} OK")
                    except Exception as e:
                        print(f"  {pname:18s} FAILED: {str(e)[:60]}")
                    break


def _setup_default_tasks(agents: list) -> list:
    """Define default tasks for the crew."""
    tasks = []
    agent_ids = [a["id"] for a in agents]

    print(f"\n  Define tasks. Available agents: {', '.join(agent_ids)}")
    print("  Enter blank description to stop.\n")

    while True:
        task_num = len(tasks) + 1
        desc = _prompt(f"  Task {task_num} description (blank to stop)")
        if not desc:
            break

        task_id = f"task_{task_num}"
        expected = _prompt("  Expected output", "A detailed response in markdown.")
        agent_id = _prompt(f"  Assign to agent ({', '.join(agent_ids)})", agent_ids[-1] if agent_ids else "")
        if agent_id not in agent_ids:
            print(f"  Unknown agent. Assigning to {agent_ids[0]}.")
            agent_id = agent_ids[0]

        output_file = _prompt("  Output file (blank=none)")
        context_ids = _prompt("  Depends on task IDs (comma-sep, blank=none)")
        context_list = [c.strip() for c in context_ids.split(",") if c.strip()] if context_ids else []

        tasks.append({
            "id": task_id,
            "description": desc,
            "expected_output": expected,
            "agent_id": agent_id,
            "output_file": output_file or None,
            "context_task_ids": context_list,
        })

    return tasks


def _setup_routing(agents: list) -> dict:
    """Set up heartbeat routing keywords per agent."""
    keywords = {}
    default_agent = agents[0]["id"] if agents else ""

    print("  For each agent, enter keywords that should route tasks to them.")
    print("  (Comma-separated. Blank = no auto-routing for this agent.)\n")

    for agent in agents:
        kw = _prompt(f"  {agent['name']} keywords", "")
        if kw:
            keywords[agent["id"]] = [k.strip().lower() for k in kw.split(",") if k.strip()]

    return {
        "keywords": keywords,
        "default_agent": default_agent,
    }


def _detect_terminal() -> str:
    """Detect the user's terminal emulator."""
    import shutil
    # Check environment variable first
    for env_var in ("TERMINAL", "TERM_PROGRAM"):
        term = os.environ.get(env_var)
        if term and shutil.which(term):
            return term
    # Try common terminals in preference order
    for term in [
        "x-terminal-emulator",  # Debian/Ubuntu default
        "gnome-terminal",
        "konsole",
        "xfce4-terminal",
        "mate-terminal",
        "kitty",
        "alacritty",
        "wezterm",
        "xterm",
    ]:
        if shutil.which(term):
            return term
    return ""


def _generate_desktop_shortcut(project_name: str):
    """Generate a .desktop file for the app menu."""
    desktop_dir = os.path.expanduser("~/.local/share/applications")
    icon_path = os.path.expanduser("~/.local/share/icons/starling.svg")
    safe_name = project_name.lower().replace(" ", "-")
    desktop_path = os.path.join(desktop_dir, f"starling-{safe_name}.desktop")

    project_dir = os.path.dirname(__file__)

    # Install bundled icon if not already present
    bundled_icon = os.path.join(project_dir, "starling.svg")
    if os.path.exists(bundled_icon) and not os.path.exists(icon_path):
        os.makedirs(os.path.dirname(icon_path), exist_ok=True)
        import shutil
        shutil.copy2(bundled_icon, icon_path)

    # Detect terminal emulator
    terminal = _detect_terminal()
    if terminal:
        exec_line = f'{terminal} -e "cd {project_dir} && uv run starling"'
    else:
        exec_line = f'cd {project_dir} && uv run starling'

    content = f"""[Desktop Entry]
Name={project_name} (Starling)
Comment=Launch {project_name} Agent Command Center
Exec={exec_line}
Icon={icon_path if os.path.exists(icon_path) else 'utilities-terminal'}
Terminal={'true' if not terminal else 'false'}
Type=Application
Categories=Development;Utility;
Keywords=crewai;starling;agents;
"""
    os.makedirs(desktop_dir, exist_ok=True)
    with open(desktop_path, "w") as f:
        f.write(content)
    os.chmod(desktop_path, 0o755)
    print(f"  Desktop shortcut: {desktop_path}")

    try:
        os.system(f"update-desktop-database {desktop_dir} 2>/dev/null")
    except Exception:
        pass


if __name__ == "__main__":
    run_setup()
