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
from typing import Optional

import theme

COLORS = ["cyan", "green", "yellow", "magenta", "blue", "red", "white", "orange"]
MAX_AGENTS = 10

# Navigation sentinels — objects, not strings, so no collision with user input
_BACK = object()
_SKIP = object()
_QUIT = object()
_DONE = object()  # signals "another wizard path completed successfully — exit quietly"


_ENV_KEY_RE = __import__("re").compile(r"^[A-Z_][A-Z0-9_]*$", __import__("re").IGNORECASE)


def _sanitize_env_pair(key: str, value: str) -> tuple:
    """Validate an env var name + value pair before writing to a `.env` file.

    Returns (clean_key, clean_value) or (None, None) if the pair must be rejected.
    Rejects:
      - keys not matching POSIX env var name syntax (prevents shell-injection
        via crafted .starling backups that supply malicious key names)
      - values containing newline / carriage return / null bytes (prevents
        injecting a second VAR=... line and overwriting unrelated env vars
        like LD_PRELOAD or PATH)
    """
    if not isinstance(key, str) or not isinstance(value, str):
        return None, None
    key = key.strip()
    if not _ENV_KEY_RE.match(key):
        return None, None
    if "\n" in value or "\r" in value or "\x00" in value:
        return None, None
    return key, value


def _starling_version() -> str:
    """Read the canonical version string. Tolerates a missing __version__
    module so development checkouts without the file still work."""
    try:
        from __version__ import __version__
        return __version__
    except ImportError:
        return "0.0.0-dev"

# When True, 'q' is suppressed in nav prompts — caller uses 'b' to exit sub-flow.
# Set inside model picker so users can't quit the whole wizard from a sub-step.
_QUIT_SUPPRESSED = False


def _nav_hint(skippable: bool = False) -> str:
    """Return the standard navigation hint line."""
    parts = ["Enter = next", "b = back"]
    if not _QUIT_SUPPRESSED:
        parts.append("q = quit")
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
            if _QUIT_SUPPRESSED:
                theme.warn("  Quit is disabled here — use 'b' to go back.")
                continue
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
    """Numbered option picker with nav support and pagination.

    Args:
        label: Prompt label (shown above the options).
        options: List of (display_name, value) tuples OR plain strings.
        default_index: 0-based index of the default option.
        skippable: Whether 's' skip is allowed.

    Returns:
        The selected value, or _BACK/_SKIP/_QUIT sentinel.

    Pagination: when options exceed terminal height, paginate. Extra commands:
        n — next page
        p — previous page
        g — jump to page containing the default
    """
    # Normalize to (display, value) tuples
    norm = [(o, o) if isinstance(o, str) else o for o in options]

    # Reserve rows for: prompt header (3) + nav hint (2) + input prompt (2)
    import shutil
    _, term_rows = shutil.get_terminal_size(fallback=(80, 24))
    page_size = max(5, term_rows - 8)
    total_pages = (len(norm) + page_size - 1) // page_size
    # Start on the page that contains the default
    cur_page = default_index // page_size if total_pages > 0 else 0

    while True:
        start = cur_page * page_size
        end = min(start + page_size, len(norm))
        print()
        if total_pages > 1:
            print(theme.color(
                f"  Page {cur_page + 1}/{total_pages}  "
                f"(showing {start + 1}–{end} of {len(norm)})",
                "muted",
            ))
        for i in range(start, end):
            disp, _val = norm[i]
            num = i + 1
            marker = theme.color("  ← default", "accent") if i == default_index else ""
            print(f"    {theme.color(f'{num:>2}', 'highlight')}) {disp}{marker}")

        default_str = str(default_index + 1) if norm else ""
        nav_extras = ""
        if total_pages > 1:
            nav_extras = theme.color(
                f"  (n/p = next/prev page, or type a number 1–{len(norm)})",
                "muted",
            )
            print(nav_extras)

        raw = _prompt_nav(label, default=default_str, skippable=skippable)
        if raw in (_BACK, _SKIP, _QUIT):
            return raw
        # Pagination commands
        if isinstance(raw, str):
            lraw = raw.lower().strip()
            if lraw == "n" and total_pages > 1:
                cur_page = (cur_page + 1) % total_pages
                continue
            if lraw == "p" and total_pages > 1:
                cur_page = (cur_page - 1) % total_pages
                continue
            if lraw == "g" and total_pages > 1:
                cur_page = default_index // page_size
                continue
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
    # Enter alt screen buffer with dark background painted. Guarantees we
    # restore the terminal on any exit path (try/finally wraps the whole body).
    theme.enter_dark_screen()
    try:
        _run_setup_body()
    finally:
        theme.exit_dark_screen()


def _run_setup_body():
    theme.clear_screen()
    theme.banner("Starling Setup")
    print(f"  {theme.color('Welcome to Starling', 'primary', bold=True)} — let's get your crew configured.\n")
    # Warn if the terminal is smaller than recommended. The resize request was
    # already sent in enter_dark_screen(); this catches the case where the
    # terminal ignored it (iTerm2, Apple Terminal, Alacritty, kitty do).
    if not theme.check_terminal_size(min_cols=100, min_rows=28):
        try:
            input(theme.color("  Press Enter to continue anyway...", "muted"))
        except (EOFError, KeyboardInterrupt):
            return

    # Pre-start menu
    while True:
        print("  How would you like to start?\n")
        print(f"    {theme.color('1', 'highlight')}) New project")
        print(f"    {theme.color('2', 'highlight')}) Import existing config (.starling backup)")
        print(f"    {theme.color('3', 'highlight')}) Quit")
        try:
            choice = input(theme.color("\n  Choice [1]: ", "highlight")).strip() or "1"
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "1":
            break
        elif choice == "2":
            if _run_import_flow():
                return  # import completed
            continue  # import cancelled — re-show menu
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
    """Unified model picker.

    Two-level UX:
      1. Provider Overview — user configures any number of providers (key,
         base URL, models to enable). Auto-launches if no providers configured yet.
      2. Model Pick — filtered list of models from configured providers only.

    Quit is available on the main model-pick page, but suppressed inside
    sub-flows (provider overview, API key entry, custom model form).
    Returns the model preset key, or a nav sentinel.
    """
    # Ensure at least one provider is configured before we can pick a model.
    if not state.get("configured_providers"):
        result = _provider_overview(state)
        if result in (_BACK, _SKIP, _QUIT):
            return result

    return _pick_model_from_configured(state)


def _provider_overview(state: dict):
    """Provider overview menu. User picks providers to configure.

    Quit is suppressed in this sub-flow; use 'b' to back out to the main pick.
    Runs until user hits 'd' (done) or a nav sentinel. Returns the done
    signal (empty string) or nav sentinel.
    """
    global _QUIT_SUPPRESSED
    prev_suppressed = _QUIT_SUPPRESSED
    _QUIT_SUPPRESSED = True
    try:
        return _provider_overview_body(state)
    finally:
        _QUIT_SUPPRESSED = prev_suppressed


def _provider_overview_body(state: dict):
    from model_wizard import load_presets
    all_presets = load_presets()

    # Group presets by provider
    providers: dict = {}  # prov_label -> list of (key, preset_dict)
    for k, v in all_presets.items():
        providers.setdefault(v.get("provider", "Other"), []).append((k, v))

    # Alphabetical for predictability, Custom last
    prov_order = sorted(providers.keys(), key=lambda p: (p == "Custom", p.lower()))

    # If "configured_providers" doesn't exist yet, auto-detect via env/reachability
    # so xAI (if key is in env) shows as ready without the user having to open it.
    state.setdefault("configured_providers", set())
    cfg = state["configured_providers"]
    for prov_name in prov_order:
        has_ready = any(
            (v.get("api_key_env") and os.environ.get(v["api_key_env"]))
            or (not v.get("api_key_env") and _preset_available(k, v))
            for k, v in providers[prov_name]
        )
        if has_ready:
            cfg.add(prov_name)

    while True:
        theme.clear_screen()
        theme.step_header(3, 5, "Configure Providers")
        print("  Pick a provider to configure (enter its number).")
        print("  Providers with a configured API key or reachable local server are")
        print("  marked [ready] — those models are immediately usable.\n")

        options = []
        for i, prov_name in enumerate(prov_order, 1):
            presets = providers[prov_name]
            # Count ready models in this provider
            ready_count = sum(
                1 for k, v in presets
                if (v.get("api_key_env") and os.environ.get(v["api_key_env"]))
                or (not v.get("api_key_env") and _preset_available(k, v))
            )
            if prov_name in cfg and ready_count > 0:
                status = theme.color(f"[{ready_count} model(s) ready]", "success")
            elif prov_name in cfg:
                status = theme.color("[configured, no key yet]", "warning")
            else:
                status = theme.color("[not configured]", "muted")
            label = theme.color(prov_name, "accent", bold=True)
            print(f"  {theme.color(f'{i:>2}', 'highlight')}) {label:40s} {status}")

        custom_prov_num = len(prov_order) + 1
        custom_model_num = len(prov_order) + 2
        print(f"\n  {theme.color(f'{custom_prov_num:>2}', 'highlight')}) "
              + theme.color("+ Add a custom provider", "highlight", bold=True))
        print(f"  {theme.color(f'{custom_model_num:>2}', 'highlight')}) "
              + theme.color("+ Add a custom model to an existing provider", "highlight", bold=True))

        ready_total = sum(1 for p in cfg if providers.get(p))
        print()
        done_hint = (
            theme.color("  d) Done — continue", "success", bold=True)
            if cfg else
            theme.color("  d) Done — continue (no providers configured — agent will have no model!)", "warning")
        )
        print(done_hint)
        print(_nav_hint())

        try:
            raw = input(theme.color("\n  Choice: ", "highlight")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return _QUIT

        if raw == "q":
            return _QUIT
        if raw == "b":
            return _BACK
        if raw == "d":
            if not cfg:
                theme.warn("  No providers configured. Are you sure? [y/N]")
                try:
                    confirm = input("  > ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    return _QUIT
                if confirm != "y":
                    continue
            return ""  # done signal

        # Parse numeric
        try:
            n = int(raw)
        except ValueError:
            theme.error("  Enter a number, 'd' for done, 'b' back, or 'q' quit.")
            try:
                input(theme.color("  Press Enter to continue...", "muted"))
            except (EOFError, KeyboardInterrupt):
                return _QUIT
            continue

        if 1 <= n <= len(prov_order):
            prov_name = prov_order[n - 1]
            r = _configure_provider(prov_name, providers[prov_name], state)
            if r is _QUIT:
                return _QUIT
            # Refresh grouping since custom additions may have changed things
            all_presets = load_presets()
            providers = {}
            for k, v in all_presets.items():
                providers.setdefault(v.get("provider", "Other"), []).append((k, v))
            prov_order = sorted(providers.keys(), key=lambda p: (p == "Custom", p.lower()))
            continue
        if n == custom_prov_num:
            r = _add_custom_provider_flow(state)
            if r is _QUIT:
                return _QUIT
            # Refresh
            all_presets = load_presets()
            providers = {}
            for k, v in all_presets.items():
                providers.setdefault(v.get("provider", "Other"), []).append((k, v))
            prov_order = sorted(providers.keys(), key=lambda p: (p == "Custom", p.lower()))
            continue
        if n == custom_model_num:
            r = _add_custom_model_flow()
            if r is not None:
                # User completed — mark provider (from the added preset) as configured
                all_presets = load_presets()
                preset = all_presets.get(r)
                if preset:
                    cfg.add(preset.get("provider", "Custom"))
                providers = {}
                for k, v in all_presets.items():
                    providers.setdefault(v.get("provider", "Other"), []).append((k, v))
                prov_order = sorted(providers.keys(), key=lambda p: (p == "Custom", p.lower()))
            continue

        theme.error(f"  Number out of range.")
        try:
            input(theme.color("  Press Enter to continue...", "muted"))
        except (EOFError, KeyboardInterrupt):
            return _QUIT


def _configure_provider(prov_name: str, presets: list, state: dict):
    """Configure one provider: show its models, prompt for key or base URL.

    Built-in preset fields are shown as reference ("Typical:") but the user
    types the actual value — nothing is auto-filled silently. Leaving a field
    blank preserves whatever's already set (env var or existing preset).

    Returns _QUIT on explicit quit, empty string on completion/back.
    """
    from model_wizard import load_presets, save_custom_presets
    from typing import Optional as _Opt

    while True:
        theme.clear_screen()
        theme.step_header(3, 5, f"Configure {prov_name}")

        # Show existing models under this provider
        print(f"  {theme.color('Models registered for this provider:', 'primary', bold=True)}\n")
        for k, v in presets:
            model_id = v.get("model", "")
            key_env = v.get("api_key_env")
            if key_env:
                badge = (theme.color("[key set]", "success")
                         if os.environ.get(key_env)
                         else theme.color("[needs key]", "warning"))
            else:
                badge = (theme.color("[reachable]", "success")
                         if _preset_available(k, v)
                         else theme.color("[not reachable]", "error"))
            print(f"    • {theme.color(v.get('label', k), 'accent'):30s} "
                  f"{theme.color(model_id, 'muted'):42s} {badge}")

        # Show what the provider needs
        sample = presets[0][1] if presets else {}
        typical_url = sample.get("base_url", "")
        typical_env = sample.get("api_key_env", "")
        is_local = not typical_env and typical_url and (
            "127.0.0.1" in typical_url or "localhost" in typical_url
        )

        print()
        print(f"  {theme.color('Configuration:', 'primary', bold=True)}\n")

        if typical_env:
            cur_env_val = os.environ.get(typical_env)
            if cur_env_val:
                print(f"    Env var {theme.color(typical_env, 'highlight')}: "
                      + theme.color("set in environment ✓", "success"))
            else:
                print(f"    Env var {theme.color(typical_env, 'highlight')}: "
                      + theme.color("not set", "warning"))
        else:
            print(f"    No API key required (local provider).")

        print(f"    Base URL reference: {theme.color(typical_url or '(not set)', 'muted')}")
        print()

        # Show edit/delete options when this provider has any user-added (custom)
        # presets. Built-in presets are never editable/deletable from here.
        from model_wizard import BUILTIN_PRESETS
        custom_presets = [(k, v) for k, v in presets if k not in BUILTIN_PRESETS]
        has_custom = len(custom_presets) > 0
        # Whole-provider delete is only offered if ALL presets under this provider
        # are custom (no built-ins to preserve).
        is_custom_provider = has_custom and all(k not in BUILTIN_PRESETS for k, _ in presets)

        # Commands
        print(f"  {theme.color('k', 'highlight')}) Enter / update API key")
        print(f"  {theme.color('u', 'highlight')}) Set / override base URL")
        print(f"  {theme.color('m', 'highlight')}) Add another model ID to this provider")
        if is_local:
            print(f"  {theme.color('t', 'highlight')}) Test connection")
        # Edit any preset (built-in or custom). Overrides are kept on save.
        if presets:
            print(f"  {theme.color('e', 'highlight')}) Edit a model's details (including built-ins)")
        if has_custom:
            if is_custom_provider:
                print(f"  {theme.color('x', 'error')}) Delete this custom provider (removes all its models)")
            else:
                print(f"  {theme.color('x', 'error')}) Delete a custom model from this provider")
        # Reset a built-in override back to defaults
        has_overridden_builtin = any(
            k in BUILTIN_PRESETS and v != BUILTIN_PRESETS[k]
            for k, v in presets
        )
        if has_overridden_builtin:
            print(f"  {theme.color('r', 'highlight')}) Reset a built-in model to its default")
        print(f"  {theme.color('b', 'highlight')}) Back to providers")
        if not _QUIT_SUPPRESSED:
            print(f"  {theme.color('q', 'highlight')}) Quit")

        try:
            choice = input(theme.color("\n  Choice: ", "highlight")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return _QUIT

        if choice == "b" or choice == "":
            state.setdefault("configured_providers", set()).add(prov_name)
            return ""
        if choice == "q":
            if _QUIT_SUPPRESSED:
                theme.warn("  Quit is disabled here — use 'b' to go back.")
                try:
                    input(theme.color("  Press Enter to continue...", "muted"))
                except (EOFError, KeyboardInterrupt):
                    pass
                continue
            return _QUIT

        if choice == "x" and has_custom:
            if is_custom_provider:
                theme.warn(f"\n  Delete custom provider '{prov_name}' and all its models?")
                for k, _v in custom_presets:
                    print(f"    • {k}")
                try:
                    confirm = input(theme.color("  Type 'delete' to confirm: ", "error")).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    continue
                if confirm != "delete":
                    theme.muted("  Cancelled.")
                    continue
                all_presets = load_presets()
                for k, _v in custom_presets:
                    all_presets.pop(k, None)
                custom_only = {k: v for k, v in all_presets.items() if k not in BUILTIN_PRESETS}
                save_custom_presets(custom_only)
                state.setdefault("configured_providers", set()).discard(prov_name)
                theme.success(f"  Deleted provider '{prov_name}'.")
                try:
                    input(theme.color("  Press Enter to continue...", "muted"))
                except (EOFError, KeyboardInterrupt):
                    pass
                return ""
            else:
                # Per-preset delete
                print("\n  Pick a custom model to delete:")
                for i, (k, v) in enumerate(custom_presets, 1):
                    print(f"    {theme.color(f'{i:>2}', 'highlight')}) {v.get('label', k)} ({v.get('model','')})")
                try:
                    pick = input(theme.color("  Number (or blank to cancel): ", "highlight")).strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if not pick:
                    continue
                try:
                    idx = int(pick) - 1
                except ValueError:
                    theme.error("  Not a number.")
                    continue
                if not (0 <= idx < len(custom_presets)):
                    theme.error("  Out of range.")
                    continue
                del_key, _del_preset = custom_presets[idx]
                try:
                    confirm = input(theme.color(f"  Type 'delete' to delete '{del_key}': ", "error")).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    continue
                if confirm != "delete":
                    theme.muted("  Cancelled.")
                    continue
                all_presets = load_presets()
                all_presets.pop(del_key, None)
                custom_only = {k: v for k, v in all_presets.items() if k not in BUILTIN_PRESETS}
                save_custom_presets(custom_only)
                theme.success(f"  Deleted '{del_key}'.")
                presets = [(k, v) for k, v in load_presets().items()
                           if v.get("provider", "Other") == prov_name]
                try:
                    input(theme.color("  Press Enter to continue...", "muted"))
                except (EOFError, KeyboardInterrupt):
                    pass
                continue

        if choice == "r" and has_overridden_builtin:
            overridden = [(k, v) for k, v in presets
                          if k in BUILTIN_PRESETS and v != BUILTIN_PRESETS[k]]
            print("\n  Pick a built-in model to reset to default:")
            for i, (k, v) in enumerate(overridden, 1):
                print(f"    {theme.color(f'{i:>2}', 'highlight')}) {v.get('label', k)} ({v.get('model','')})")
            try:
                pick = input(theme.color("  Number (or blank to cancel): ", "highlight")).strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if not pick:
                continue
            try:
                idx = int(pick) - 1
            except ValueError:
                theme.error("  Not a number.")
                continue
            if not (0 <= idx < len(overridden)):
                theme.error("  Out of range.")
                continue
            reset_key, _ = overridden[idx]
            all_presets = load_presets()
            all_presets[reset_key] = dict(BUILTIN_PRESETS[reset_key])
            save_custom_presets(all_presets)
            theme.success(f"  Reset '{reset_key}' to built-in defaults.")
            presets = [(k, v) for k, v in load_presets().items()
                       if v.get("provider", "Other") == prov_name]
            try:
                input(theme.color("  Press Enter to continue...", "muted"))
            except (EOFError, KeyboardInterrupt):
                pass
            continue

        if choice == "e" and presets:
            edit_pool = presets  # built-in or custom; both allowed
            print("\n  Pick a model to edit:")
            for i, (k, v) in enumerate(edit_pool, 1):
                print(f"    {theme.color(f'{i:>2}', 'highlight')}) {v.get('label', k)} ({v.get('model','')})")
            try:
                pick = input(theme.color("  Number (or blank to cancel): ", "highlight")).strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if not pick:
                continue
            try:
                idx = int(pick) - 1
            except ValueError:
                theme.error("  Not a number.")
                continue
            if not (0 <= idx < len(edit_pool)):
                theme.error("  Out of range.")
                continue
            edit_key, edit_preset = edit_pool[idx]
            all_presets = load_presets()
            p = dict(all_presets.get(edit_key, edit_preset))

            def _edit(label, current, required=True):
                try:
                    v = input(theme.prompt_text(label, default=str(current or ""))).strip()
                except (EOFError, KeyboardInterrupt):
                    return current
                if not v:
                    return current
                return v

            p["label"] = _edit("Display label", p.get("label", edit_key))
            p["model"] = _edit("Model ID", p.get("model", ""))
            p["base_url"] = _edit("Base URL", p.get("base_url", ""))
            p["api_key_env"] = _edit("API key env var", p.get("api_key_env", "") or "")
            fmt = _edit("API format (openai / anthropic)", p.get("api_format", "openai"))
            p["api_format"] = "anthropic" if fmt.strip().lower() == "anthropic" else "openai"
            all_presets[edit_key] = p
            custom_only = {k: v for k, v in all_presets.items() if k not in BUILTIN_PRESETS}
            save_custom_presets(custom_only)
            theme.success(f"  Updated '{edit_key}'.")
            # Refresh the presets list for redraw
            presets = [(k, v) for k, v in load_presets().items()
                       if v.get("provider", "Other") == prov_name]
            try:
                input(theme.color("  Press Enter to continue...", "muted"))
            except (EOFError, KeyboardInterrupt):
                pass
            continue

        if choice == "k":
            if not typical_env:
                theme.warn("  This provider doesn't use an API key.")
                try:
                    input(theme.color("  Press Enter to continue...", "muted"))
                except (EOFError, KeyboardInterrupt):
                    return _QUIT
                continue
            # Use the provider's standard env var name directly. Users don't
            # need to think about this — it's just where the key gets saved.
            env_var = typical_env
            print(f"\n  {theme.color(prov_name, 'accent', bold=True)} API key")
            theme.muted(f"  (Will be saved as {env_var} in your work dir .env)")
            try:
                key_val = input(theme.color(
                    f"\n  Paste your API key: ", "highlight")).strip()
            except (EOFError, KeyboardInterrupt):
                return _QUIT

            if not key_val:
                # Empty input — do NOT mark provider configured, warn user clearly.
                theme.error(f"  No value entered for {env_var}. Nothing saved.")
                theme.muted("  Try 'k' again. If paste failed, try typing the key directly.")
                try:
                    input(theme.color("  Press Enter to continue...", "muted"))
                except (EOFError, KeyboardInterrupt):
                    return _QUIT
                continue

            # Defensive: warn if pasted value looks wrong (contains = or whitespace)
            if "=" in key_val and key_val.startswith(env_var):
                # User pasted "NVIDIA_API_KEY=..." — strip the prefix
                suffix = key_val.split("=", 1)[1].strip()
                if suffix:
                    key_val = suffix
                    theme.muted(f"  (Stripped '{env_var}=' prefix from your paste.)")
            if " " in key_val or "\t" in key_val:
                theme.warn(f"  Value contains whitespace — keys usually don't. Saved anyway.")

            # Persist: set in current process env + stage for .env write on finalize
            os.environ[env_var] = key_val
            state.setdefault("api_keys_pending", {})[env_var] = key_val
            state.setdefault("configured_providers", set()).add(prov_name)
            # Visible confirmation with character count so user can verify
            masked = key_val[:6] + "..." + key_val[-4:] if len(key_val) > 12 else "*" * len(key_val)
            theme.success(
                f"  {env_var} saved ({len(key_val)} chars, {masked}). "
                f"Written to .env on finalize."
            )
            try:
                input(theme.color("  Press Enter to continue...", "muted"))
            except (EOFError, KeyboardInterrupt):
                return _QUIT
            continue

        if choice == "u":
            print(f"\n  Typical base URL: {theme.color(typical_url or '(none)', 'muted')}")
            new_url = input(theme.color(
                f"  Base URL (empty to keep current): ", "highlight")).strip()
            if new_url:
                state.setdefault("provider_url_overrides", {})[prov_name] = new_url
                theme.success(f"  Base URL override set to {new_url}")
                theme.muted("  (Applies at agent runtime; existing presets keep their URL in the catalog.)")
            try:
                input(theme.color("  Press Enter to continue...", "muted"))
            except (EOFError, KeyboardInterrupt):
                return _QUIT
            continue

        if choice == "m":
            new_preset_key = _add_custom_model_flow(default_provider=prov_name)
            if new_preset_key:
                # Refresh presets list for this provider
                all_presets = load_presets()
                presets = [(k, v) for k, v in all_presets.items()
                           if v.get("provider") == prov_name]
                state.setdefault("configured_providers", set()).add(prov_name)
            continue

        if choice == "t" and is_local:
            test_url = state.get("provider_url_overrides", {}).get(prov_name, typical_url)
            try:
                import urllib.request
                urllib.request.urlopen(test_url.rstrip("/") + "/models", timeout=3)
                theme.success(f"  Connection to {test_url} OK")
                state.setdefault("configured_providers", set()).add(prov_name)
            except Exception as e:
                theme.error(f"  Connection failed: {str(e)[:80]}")
                theme.muted("  Is LM Studio / Ollama running? Check the base URL.")
            try:
                input(theme.color("  Press Enter to continue...", "muted"))
            except (EOFError, KeyboardInterrupt):
                return _QUIT
            continue

        theme.error(f"  Unknown choice: {choice}")
        try:
            input(theme.color("  Press Enter to continue...", "muted"))
        except (EOFError, KeyboardInterrupt):
            return _QUIT


def _add_custom_provider_flow(state: dict):
    """Register a brand-new provider with its own base URL and env var.

    After saving, the user can then add models to it via option 15.
    """
    theme.clear_screen()
    theme.banner("Add a custom provider")
    print("  A provider is just a name + base URL + optional API key env var.")
    print("  You'll add the provider first, then its models.\n")

    name = input(theme.color(
        "  Provider display name (e.g. 'MyProvider'): ", "highlight")).strip()
    if not name:
        theme.muted("  Cancelled.")
        try:
            input(theme.color("  Press Enter to continue...", "muted"))
        except (EOFError, KeyboardInterrupt):
            return _QUIT
        return ""

    print(f"\n  Typical base URL formats:")
    theme.muted("    https://api.example.com/v1        (cloud)")
    theme.muted("    http://127.0.0.1:port/v1          (local)")
    base_url = input(theme.color("\n  Base URL: ", "highlight")).strip()

    print(f"\n  Typical env var naming: PROVIDERNAME_API_KEY (all caps)")
    env_var = input(theme.color(
        "  API key env var (blank if none needed): ", "highlight")).strip()

    key_val = ""
    if env_var:
        key_val = input(theme.color(
            f"  Paste {env_var} value (empty to skip): ", "highlight")).strip()

    # Save as a placeholder preset so the provider appears in the overview.
    # Actual models for it get added via "Add custom model" with this provider name.
    # The preset is a shell with a placeholder model entry.
    from model_wizard import load_presets, save_custom_presets
    all_presets = load_presets()
    placeholder_key = f"{name.lower().replace(' ', '-')}-placeholder"
    if placeholder_key not in all_presets:
        all_presets[placeholder_key] = {
            "label": f"{name} (placeholder — add a real model)",
            "model": "replace-with-real-model-id",
            "base_url": base_url,
            "api_format": "openai",
            "api_key_env": env_var or None,
            "provider": name,
            "extra": {},
        }
        save_custom_presets(all_presets)

    if key_val and env_var:
        os.environ[env_var] = key_val
        state.setdefault("api_keys_pending", {})[env_var] = key_val

    state.setdefault("configured_providers", set()).add(name)
    theme.success(f"  Provider '{name}' added. Next, add models to it via option 15.")
    try:
        input(theme.color("  Press Enter to continue...", "muted"))
    except (EOFError, KeyboardInterrupt):
        return _QUIT
    return ""


def _pick_model_from_configured(state: dict):
    """Pick a specific model from the configured providers' catalogs."""
    from model_wizard import load_presets
    all_presets = load_presets()
    cfg = state.get("configured_providers") or set()

    # Filter to only configured providers (if any)
    if cfg:
        available = [(k, v) for k, v in all_presets.items()
                     if v.get("provider") in cfg]
    else:
        available = list(all_presets.items())

    # Drop placeholder entries (custom providers without real models yet)
    available = [(k, v) for k, v in available
                 if not k.endswith("-placeholder")]

    if not available:
        theme.warn("  No models available. Let's configure a provider first.")
        try:
            input(theme.color("  Press Enter to continue...", "muted"))
        except (EOFError, KeyboardInterrupt):
            return _QUIT
        return _provider_overview(state) or _pick_model_from_configured(state)

    theme.clear_screen()
    theme.step_header(4, 5, "Pick your agent's model")
    print("  Choose which model your agent will use for chat completions.\n")

    options = []
    for k, v in sorted(available, key=lambda kv: (kv[1].get("provider", ""), kv[0])):
        prov = v.get("provider", "?")
        label = v.get("label", k)
        model_id = v.get("model", "")
        display = (
            f"{theme.color(label, 'accent', bold=True):30s} "
            f"{theme.color(f'({prov})', 'muted'):20s} "
            f"{theme.color(model_id, 'muted')}"
        )
        options.append((display, k))

    options.append((
        theme.color("r) Re-configure providers", "highlight"),
        "_reconfig",
    ))

    print(_nav_hint())
    default_idx = 0
    if state.get("model_preset"):
        for i, (_, v) in enumerate(options):
            if v == state["model_preset"]:
                default_idx = i
                break

    result = _pick_option("Model", options, default_index=default_idx)
    if result in (_BACK, _SKIP, _QUIT):
        return result
    if result == "_reconfig":
        r = _provider_overview(state)
        if r in (_BACK, _QUIT):
            return r
        return _pick_model_from_configured(state)

    state["model_preset"] = result
    state["_available_presets"] = dict(all_presets)
    return result


def _add_custom_model_flow(default_provider: Optional[str] = None) -> Optional[str]:
    """Mini-flow to register a new custom model preset mid-wizard.

    Quit is suppressed — user can only save or back out.
    Prompts for name, label, model ID, base URL, API format, env var name,
    then saves to model_presets.json via model_wizard.save_custom_presets().
    If default_provider is set, uses it as the provider name (no prompt).
    Returns the new preset key on success, or None if the user aborted.
    """
    global _QUIT_SUPPRESSED
    prev_suppressed = _QUIT_SUPPRESSED
    _QUIT_SUPPRESSED = True
    try:
        return _add_custom_model_flow_body(default_provider)
    finally:
        _QUIT_SUPPRESSED = prev_suppressed


def _add_custom_model_flow_body(default_provider: Optional[str] = None) -> Optional[str]:
    theme.clear_screen()
    theme.banner("Add a custom model")
    print("  Define a new OpenAI-compatible or Anthropic-compatible model.")
    print("  You can reference any provider with a standard API endpoint.\n")
    print(_nav_hint())

    name = _prompt_nav("Preset name (short, lowercase, no spaces)",
                       hint="e.g. my-grok, local-llama", required=True)
    if name in (_BACK, _QUIT, _SKIP):
        return None
    name = name.lower().replace(" ", "-")

    # Check collision with builtins
    from model_wizard import load_presets, save_custom_presets, BUILTIN_PRESETS
    existing = load_presets()
    if name in existing:
        theme.error(f"Preset '{name}' already exists. Pick a different name.")
        try:
            input("  Press Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            return None
        return _add_custom_model_flow()

    label = _prompt_nav("Display label",
                        default=name.replace("-", " ").title(), required=True)
    if label in (_BACK, _QUIT, _SKIP):
        return None

    model_id = _prompt_nav("Full model ID",
                           hint="e.g. openai/gpt-4o, anthropic/claude-sonnet-4-20250514",
                           required=True)
    if model_id in (_BACK, _QUIT, _SKIP):
        return None

    base_url = _prompt_nav("API base URL",
                           default="https://api.openai.com/v1",
                           required=True)
    if base_url in (_BACK, _QUIT, _SKIP):
        return None

    # API format: openai-compatible or anthropic
    theme.clear_screen()
    theme.banner("Add a custom model")
    print(f"  Preset: {theme.color(name, 'accent')}  ({model_id})")
    print(f"  URL:    {base_url}\n")
    print("  API format:\n")
    print(f"    {theme.color('1', 'highlight')}) openai   — OpenAI-compatible (most providers)")
    print(f"    {theme.color('2', 'highlight')}) anthropic — Anthropic's native API")
    try:
        fmt_choice = input(theme.color("\n  Choice [1]: ", "highlight")).strip() or "1"
    except (EOFError, KeyboardInterrupt):
        return None
    api_format = "anthropic" if fmt_choice == "2" else "openai"

    env_var = _prompt_nav("Environment variable for API key",
                          default=f"{name.upper().replace('-', '_')}_API_KEY",
                          required=True)
    if env_var in (_BACK, _QUIT, _SKIP):
        return None

    provider = _prompt_nav("Provider label (for display)",
                           default=label, required=True)
    if provider in (_BACK, _QUIT, _SKIP):
        return None

    # Save
    existing[name] = {
        "label": label,
        "model": model_id,
        "base_url": base_url,
        "api_format": api_format,
        "api_key_env": env_var,
        "provider": provider,
        "extra": {},
    }
    try:
        save_custom_presets(existing)
        theme.success(f"Custom model '{name}' saved.")
        if not os.environ.get(env_var):
            theme.warn(f"Remember: {env_var} is not set in your environment.")
            theme.muted("  The next step will offer to save it.")
        try:
            input(theme.color("  Press Enter to continue...", "muted"))
        except (EOFError, KeyboardInterrupt):
            pass
        return name
    except Exception as e:
        theme.error(f"Failed to save custom preset: {e}")
        try:
            input("  Press Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            pass
        return None


def _step_api_key(state: dict):
    """Step 4: verify the chosen model's API key is available.

    Providers collect keys in the overview step, but a user may pick a model
    whose provider wasn't configured. This step is interactive: if the key is
    missing, the user can enter it here, go back to re-configure the provider,
    or skip and add later.
    """
    from model_wizard import load_presets
    presets = state.get("_available_presets") or load_presets()
    preset_key = state.get("model_preset")
    preset = presets.get(preset_key, {})
    key_env = preset.get("api_key_env")
    label = preset.get("label", preset_key)

    theme.clear_screen()
    theme.step_header(4, 5, "Verify model access")

    # Local model — no key needed
    if not key_env:
        theme.success(f"  {label} is a local model — no API key needed.")
        state["api_key_status"] = "no_key_needed"
        state["api_key_pending"] = None
        try:
            input(theme.color("\n  Press Enter to continue...", "muted"))
        except (EOFError, KeyboardInterrupt):
            return _QUIT
        return state["api_key_status"]

    # Key already in env (set during provider step OR pre-existing)
    if os.environ.get(key_env):
        if state.get("api_keys_pending", {}).get(key_env):
            theme.success(f"  {key_env} staged — will be saved to your work dir on finalize.")
            state["api_key_status"] = "pending_save"
            state["api_key_pending"] = {
                "env_var": key_env,
                "value": state["api_keys_pending"][key_env],
            }
        else:
            theme.success(f"  {key_env} is already set in your environment.")
            state["api_key_status"] = "already_set"
            state["api_key_pending"] = None
        try:
            input(theme.color("\n  Press Enter to continue...", "muted"))
        except (EOFError, KeyboardInterrupt):
            return _QUIT
        return state["api_key_status"]

    # Key is missing — interactive recovery. Let the user enter it here,
    # go back to provider config, or skip with explicit warning.
    theme.warn(f"  {label} needs {theme.color(key_env, 'highlight')} — but it's not set.")
    print()
    print(f"  {theme.color('e', 'highlight')}) Enter the API key now")
    print(f"  {theme.color('b', 'highlight')}) Back to provider setup")
    print(f"  {theme.color('s', 'highlight')}) Skip (add later via Models tab)")
    print(f"  {theme.color('q', 'highlight')}) Quit")

    while True:
        try:
            choice = input(theme.color("\n  Choice [e]: ", "highlight")).strip().lower() or "e"
        except (EOFError, KeyboardInterrupt):
            return _QUIT
        if choice == "q":
            return _QUIT
        if choice == "b":
            return _BACK
        if choice == "s":
            theme.muted(f"  Skipped — remember to set {key_env} before running tasks.")
            state["api_key_status"] = "skipped"
            state["api_key_pending"] = None
            try:
                input(theme.color("  Press Enter to continue...", "muted"))
            except (EOFError, KeyboardInterrupt):
                return _QUIT
            return state["api_key_status"]
        if choice == "e":
            try:
                key_val = input(theme.color(
                    f"  Paste {key_env} value: ", "highlight")).strip()
            except (EOFError, KeyboardInterrupt):
                return _QUIT
            if not key_val:
                theme.warn("  Empty input — no key saved. Try again or choose another option.")
                continue
            # Stage for finalize + apply to current process
            os.environ[key_env] = key_val
            state.setdefault("api_keys_pending", {})[key_env] = key_val
            state["api_key_status"] = "pending_save"
            state["api_key_pending"] = {"env_var": key_env, "value": key_val}
            theme.success(f"  {key_env} staged — will be saved on finalize.")
            try:
                input(theme.color("  Press Enter to continue...", "muted"))
            except (EOFError, KeyboardInterrupt):
                return _QUIT
            return state["api_key_status"]
        theme.error(f"  Unknown choice: {choice}")


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
    # Write all API keys staged during the provider overview step.
    # New: state["api_keys_pending"] is a dict of env_var -> value (multi-provider).
    # Backwards compat: state["api_key_pending"] was a single {env_var, value} dict.
    all_pending = dict(state.get("api_keys_pending") or {})
    single = state.get("api_key_pending")
    if single:
        all_pending[single["env_var"]] = single["value"]
    for env_var, key_val in all_pending.items():
        _save_env_key(work_dir, env_var, key_val)
        os.environ[env_var] = key_val
        theme.success(f"{env_var} saved to {os.path.join(work_dir, '.env')}")

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

    prefetch_embedding_models()
    _launch_starling_or_exit(os.path.dirname(os.path.abspath(__file__)))


def _run_team_setup():
    """Team Setup path — streamlined multi-agent flow with Leader designation.

    Steps:
        1. Project name
        2. How many agents? (2-10)
        3. For each agent: template → model (abbreviated, no per-agent review)
        4. Leader designation: pick which agent is the Leader/CEO
        5. Confirm + launch
    """
    state = {"agents": []}  # accumulates agent dicts
    steps = ["project_name", "agent_count", "agents", "leader", "confirm"]
    step_idx = 0

    def total_steps() -> int:
        return 5

    while 0 <= step_idx < len(steps):
        current = steps[step_idx]
        theme.clear_screen()
        theme.step_header(step_idx + 1, total_steps(), _team_step_title(current))

        result = _dispatch_team_step(current, state)

        if result is _DONE:
            return
        if result is _QUIT:
            theme.muted("Exiting setup.")
            return
        if result is _BACK:
            if step_idx == 0:
                theme.muted("You're at the first step. Press 'q' to quit or enter a name to continue.")
                continue
            _pop_team_step_state(steps[step_idx], state)
            step_idx -= 1
            continue
        step_idx += 1

    _finalize_team_setup(state)


def _team_step_title(step_name: str) -> str:
    titles = {
        "project_name": "Name your project",
        "agent_count":  "How many agents?",
        "agents":       "Build your team",
        "leader":       "Pick your Leader",
        "confirm":      "Review and launch",
    }
    return titles.get(step_name, step_name)


def _pop_team_step_state(step_name: str, state: dict):
    """Remove team-setup state associated with a given step."""
    keys_per_step = {
        "project_name": ["project_name", "project_desc", "work_dir"],
        "agent_count":  ["agent_count"],
        "agents":       ["agents", "_pending_keys", "_available_presets"],
        "leader":       ["leader_agent_id", "leader_auto_picked"],
        "confirm":      [],
    }
    for k in keys_per_step.get(step_name, [step_name]):
        if k == "agents":
            state["agents"] = []  # reset, don't delete
        else:
            state.pop(k, None)


def _dispatch_team_step(step_name: str, state: dict):
    if step_name == "project_name":
        return _step_project_name(state)          # reuse Quick Start step
    if step_name == "agent_count":
        return _step_agent_count(state)
    if step_name == "agents":
        return _step_agents_loop(state)
    if step_name == "leader":
        return _step_pick_leader(state)
    if step_name == "confirm":
        return _step_team_confirm(state)
    raise ValueError(f"Unknown team step: {step_name}")


def _step_agent_count(state: dict):
    """How many agents? 2-10."""
    print("  A team needs at least 2 agents. You can pick up to 10.\n")
    print(_nav_hint())
    default = str(state.get("agent_count") or 3)
    while True:
        raw = _prompt_nav("Number of agents", default=default)
        if raw in (_BACK, _SKIP, _QUIT):
            return raw
        try:
            n = int(raw)
        except (TypeError, ValueError):
            theme.error("Enter a number between 2 and 10.")
            continue
        if 2 <= n <= MAX_AGENTS:
            state["agent_count"] = n
            return n
        theme.error(f"Pick between 2 and {MAX_AGENTS}.")


def _custom_agent_flow(used_ids: set, agent_index: int):
    """Prompt user to build a custom agent from scratch.

    Quit is suppressed — user can only complete or back out to template pick.
    Returns a partial agent dict (id, name, role, goal, backstory, tools) or
    _BACK / None (None = validation loop gave up, restart template pick).
    Model/preset is picked separately by the caller.
    """
    global _QUIT_SUPPRESSED
    prev_suppressed = _QUIT_SUPPRESSED
    _QUIT_SUPPRESSED = True
    try:
        return _custom_agent_flow_body(used_ids, agent_index)
    finally:
        _QUIT_SUPPRESSED = prev_suppressed


def _custom_agent_flow_body(used_ids: set, agent_index: int):
    theme.clear_screen()
    theme.step_header(3, 5, f"Custom agent {agent_index + 1}")
    print("  Build a custom agent. Provide a short id, name, role, and goal.")
    print("  Backstory is optional. Tools can be added later in the TUI.\n")
    print(_nav_hint())

    def _ask(label: str, default: str = "", required: bool = True, block_manager: bool = True):
        while True:
            try:
                raw = input(theme.prompt_text(label, default=default)).strip()
            except (EOFError, KeyboardInterrupt):
                return _QUIT
            low = raw.lower()
            if low == "b":
                return _BACK
            if low == "q":
                if _QUIT_SUPPRESSED:
                    theme.warn("  Quit is disabled here — use 'b' to go back.")
                    continue
                return _QUIT
            val = raw or default
            if required and not val:
                theme.error(f"{label} is required.")
                continue
            if block_manager and _contains_manager(val):
                _print_manager_block(label)
                continue
            return val

    # id
    while True:
        aid = _ask("Agent id (snake_case, e.g. 'researcher')", required=True)
        if aid in (_BACK, _QUIT):
            return aid
        if aid in used_ids:
            theme.error(f"'{aid}' is already taken by another agent — pick a different id.")
            continue
        if not all(c.isalnum() or c == "_" for c in aid):
            theme.error("Id must contain only letters, digits, and underscores.")
            continue
        break

    name = _ask("Display name", default=aid.replace("_", " ").title())
    if name in (_BACK, _QUIT):
        return name
    role = _ask("Role (short title, e.g. 'Senior Researcher')")
    if role in (_BACK, _QUIT):
        return role
    goal = _ask("Goal (one sentence — what this agent delivers)")
    if goal in (_BACK, _QUIT):
        return goal
    backstory = _ask("Backstory (optional — 1-2 sentences)", required=False)
    if backstory in (_BACK, _QUIT):
        return backstory
    if not backstory:
        backstory = f"{name}, a custom agent on the team."

    return {
        "id": aid,
        "name": name,
        "role": role,
        "goal": goal,
        "backstory": backstory,
        "tools": [],
        "tier": "specialist",
    }


def _step_agents_loop(state: dict):
    """Build each agent with abbreviated prompts. Supports back within the loop."""
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
        theme.info("Switching to Advanced wizard.")
        _run_full_wizard()
        return _DONE

    from model_wizard import load_presets
    all_presets = load_presets()
    if not all_presets:
        theme.error("No model presets registered. Please report this as a bug.")
        return _QUIT
    # Show all presets — API key prompts handled separately during finalize.
    # Sort so available/ready ones appear first.
    def _sort_key(item):
        k, v = item
        key_env = v.get("api_key_env")
        if key_env:
            return (0 if os.environ.get(key_env) else 2, k)
        return (0 if _preset_available(k, v) else 3, k)
    available = sorted(all_presets.items(), key=_sort_key)
    state["_available_presets"] = dict(available)

    count = state["agent_count"]
    agents = state.get("agents") or []
    state["agents"] = agents

    # Loop with mini-state-machine supporting back inside the agent loop
    i = len(agents)  # resume from where we left off if state was preserved
    used_ids = {a["id"] for a in agents}

    while i < count:
        theme.clear_screen()
        theme.step_header(3, 5, f"Agent {i + 1} of {count}")

        # Pick template — also offer "+ Build custom agent" escape hatch.
        # Templates are shown with tier badge so user knows what they're picking.
        tmpl_options = []
        for tid, tname in templates:
            tmpl = AGENT_TEMPLATES[tid]
            purpose = tmpl.get("primary_purpose", "")[:50]
            tier = tmpl.get("tier", "specialist")
            tier_color = ("highlight" if tier == "leader"
                          else "accent" if tier == "coordinator"
                          else "muted")
            tier_badge = f"[{theme.color(tier, tier_color)}]"
            display = (
                f"{theme.color(tname, 'accent', bold=True):30s} "
                f"{tier_badge:30s} "
                f"{theme.color(purpose, 'muted')}"
            )
            tmpl_options.append((display, tid))
        # Custom agent option at end
        tmpl_options.append((
            theme.color("+ Build custom agent (name, role, goal from scratch)",
                        "highlight", bold=True),
            "_custom_agent",
        ))

        print(f"  Pick a template for agent {i + 1}, or build a custom one.\n")
        print(_nav_hint())
        tmpl_result = _pick_option("Template", tmpl_options, default_index=0)
        if tmpl_result is _BACK:
            if i == 0:
                # Back out of the whole agents step
                return _BACK
            # Remove last agent and loop back one
            agents.pop()
            used_ids = {a["id"] for a in agents}
            i -= 1
            continue
        if tmpl_result in (_SKIP, _QUIT):
            return tmpl_result

        # Custom agent path — prompt for name/role/goal, no template
        if tmpl_result == "_custom_agent":
            custom = _custom_agent_flow(used_ids, i)
            if custom in (_BACK, _QUIT):
                if custom is _QUIT:
                    return _QUIT
                continue  # back to template pick
            if custom is None:
                continue
            # custom is a partial agent dict (no preset yet). We'll add model
            # below via the normal picker flow. Skip template fetch.
            template_id = None
            tmpl = None
            pending_custom_agent = custom
        else:
            template_id = tmpl_result
            tmpl = AGENT_TEMPLATES[template_id]
            pending_custom_agent = None

        # Pick model — use the unified provider-based picker.
        # Providers were configured once at the top of team setup (auto-detected
        # from env vars). Subsequent agent picks only show the model list.
        theme.clear_screen()
        theme.step_header(3, 5, f"Agent {i + 1} of {count} — model")
        # Pass a temporary state so we don't collide with the main wizard state.
        # Inherit configured_providers so we don't re-prompt provider config.
        picker_state = {
            "configured_providers": state.get("configured_providers", set()),
            "api_keys_pending": state.get("api_keys_pending", {}),
        }
        model_result = _step_model(picker_state)
        # Propagate any API keys the user added during this pick
        for k, v in picker_state.get("api_keys_pending", {}).items():
            state.setdefault("api_keys_pending", {})[k] = v
        state["configured_providers"] = picker_state.get("configured_providers", set())
        if model_result is _BACK:
            # Re-pick template for this agent
            continue
        if model_result in (_SKIP, _QUIT):
            return model_result

        # Build the agent. For custom agents, user-picked id is authoritative.
        # For template agents, ensure unique ID via numeric suffix.
        if pending_custom_agent is not None:
            agent = {
                "id": pending_custom_agent["id"],
                "name": pending_custom_agent["name"],
                "role": pending_custom_agent["role"],
                "goal": pending_custom_agent["goal"],
                "backstory": pending_custom_agent["backstory"],
                "tools": list(pending_custom_agent.get("tools", [])),
                "preset": model_result,
                "color": COLORS[i % len(COLORS)],
                "allow_delegation": False,
                "template": "custom",
                "tier": pending_custom_agent.get("tier", "specialist"),
            }
        else:
            base_id = template_id
            if base_id not in used_ids:
                agent_id = base_id
                suffix_n = 0
            else:
                suffix_n = 2
                while f"{base_id}_{suffix_n}" in used_ids:
                    suffix_n += 1
                agent_id = f"{base_id}_{suffix_n}"
            agent = {
                "id": agent_id,
                "name": tmpl["name"] if suffix_n == 0 else f"{tmpl['name']} {suffix_n}",
                "role": tmpl["role"],
                "goal": tmpl["goal"],
                "backstory": tmpl["backstory"],
                "tools": list(tmpl["tools"]),
                "preset": model_result,
                "color": COLORS[i % len(COLORS)],
                "allow_delegation": False,
                "template": template_id,
                "tier": tmpl.get("tier", "specialist"),
            }
        agents.append(agent)
        used_ids.add(agent["id"])
        i += 1

    return "agents_complete"


def _step_pick_leader(state: dict):
    """Step 4: designate which agent is the Leader/CEO of the team."""
    agents = state.get("agents", [])
    if not agents:
        theme.error("No agents to pick a leader from — please restart setup.")
        return _QUIT

    print("  Every team needs a Leader/CEO — the agent you talk to, who")
    print("  coordinates the others. Pick one of your agents.\n")
    print(_nav_hint(skippable=True))

    # Find current default if any
    default_idx = 0
    current_leader = state.get("leader_agent_id")
    for i, a in enumerate(agents):
        if a["id"] == current_leader:
            default_idx = i
            break

    options = []
    for a in agents:
        display = f"{theme.color(a['name'], 'accent', bold=True):30s} ({a['template']}, {a['preset']})"
        options.append((display, a["id"]))

    result = _pick_option("Leader", options, default_index=default_idx, skippable=True)
    if result is _BACK:
        return _BACK
    if result is _QUIT:
        return _QUIT
    if result is _SKIP:
        # Default to first agent — flagged in state so confirm screen can show
        # that this was auto-picked rather than explicitly chosen
        state["leader_agent_id"] = agents[0]["id"]
        state["leader_auto_picked"] = True
        return _SKIP

    state["leader_agent_id"] = result
    state["leader_auto_picked"] = False
    return result


def _step_team_confirm(state: dict):
    """Step 5: summary + launch."""
    agents = state.get("agents", [])
    leader_id = state.get("leader_agent_id")

    print(f"  {theme.color('Review your team', 'primary', bold=True)}:\n")
    print(f"    Project:    {theme.color(state['project_name'], 'accent')}")
    print(f"    Work dir:   {theme.color(state['work_dir'], 'muted')}")
    print(f"    Agents:     {len(agents)}\n")

    for a in agents:
        marker = theme.color(" [LEADER]", "highlight") if a["id"] == leader_id else ""
        print(f"      • {theme.color(a['name'], 'accent', bold=True):25s} "
              f"{theme.color(a['template'], 'muted')} on "
              f"{theme.color(a['preset'], 'accent')}{marker}")

    if state.get("leader_auto_picked"):
        print()
        theme.muted("  (Leader auto-picked — first agent in the list.)")

    # Warn about any missing API keys
    from model_wizard import load_presets
    presets = state.get("_available_presets") or load_presets()
    missing_keys = set()
    for a in agents:
        preset = presets.get(a["preset"], {})
        key_env = preset.get("api_key_env")
        if key_env and not os.environ.get(key_env):
            missing_keys.add(key_env)
    if missing_keys:
        print()
        theme.warn(f"Missing API keys: {', '.join(sorted(missing_keys))}")
        print(f"    Set them in your work dir .env file before running tasks.")

    print()
    print(_nav_hint())
    try:
        raw = input(theme.color("  Save and launch Starling? [Y/n/b/q]: ", "highlight")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return _QUIT
    if raw == "b":
        return _BACK
    if raw == "q":
        return _QUIT
    if raw and raw[0] != "y":
        theme.muted("Cancelled. Run setup again when you're ready.")
        return _QUIT
    return "confirmed"


def _finalize_team_setup(state: dict):
    """Write the config from Team Setup state and launch Starling."""
    required = ("project_name", "project_desc", "work_dir", "agents", "leader_agent_id")
    missing = [k for k in required if not state.get(k)]
    if missing:
        theme.error(f"Internal error: missing state {missing}. Please re-run setup.")
        return

    agents = state["agents"]
    leader_id = state["leader_agent_id"]
    work_dir = state["work_dir"]

    # Promote the Leader agent: tier=leader, allow_delegation=True
    for a in agents:
        if a["id"] == leader_id:
            a["tier"] = "leader"
            a["allow_delegation"] = True
            break

    # Create work dir and subdirs
    os.makedirs(work_dir, exist_ok=True)
    for sub in ("output", "memory", "skills"):
        os.makedirs(os.path.join(work_dir, sub), exist_ok=True)

    config = {
        "project": {
            "name": state["project_name"],
            "description": state["project_desc"],
            "work_dir": work_dir,
        },
        "agents": agents,
        "max_agents": MAX_AGENTS,
        "default_tasks": [],
        "routing": {
            "keywords": {},
            "default_agent": leader_id,
        },
    }

    config_path = os.path.join(os.path.dirname(__file__), "project_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    # Persist any API keys staged during provider overview to work_dir/.env
    pending = state.get("api_keys_pending") or {}
    if pending:
        env_path = os.path.join(work_dir, ".env")
        try:
            existing_env = {}
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, _, v = line.partition("=")
                        existing_env[k.strip()] = v.strip().strip('"').strip("'")
            # Sanitize before writing — staged keys originate from user input
            # in the wizard, but we belt-and-suspenders the write path so future
            # callers can't accidentally inject env vars via newlines in values.
            sanitized_pending = {}
            for k, v in pending.items():
                ck, cv = _sanitize_env_pair(k, v)
                if ck is not None:
                    sanitized_pending[ck] = cv
            existing_env.update(sanitized_pending)
            with open(env_path, "w") as f:
                for k, v in existing_env.items():
                    f.write(f"{k}={v}\n")
            try:
                os.chmod(env_path, 0o600)
            except OSError:
                theme.warn(f"  Could not restrict {env_path} to 600 — contents may be world-readable.")
        except OSError as e:
            theme.warn(f"  Could not write .env: {e}")

    theme.clear_screen()
    theme.banner("Setup Complete!")
    theme.success(f"Config saved: {config_path}")
    print(f"  Project: {theme.color(state['project_name'], 'accent')}")
    print(f"  Team:    {len(agents)} agents, Leader is "
          f"{theme.color(next(a['name'] for a in agents if a['id'] == leader_id), 'accent', bold=True)}")
    print(f"  Work dir: {work_dir}\n")

    prefetch_embedding_models()
    _launch_starling_or_exit(os.path.dirname(os.path.abspath(__file__)))


def prefetch_embedding_models():
    """Pre-download the two embedding models used by routing and memory.

    Called at the end of every setup path so fastembed doesn't block the user
    on first task. CPU-only — never GPU.
    """
    theme.info("  Pre-downloading embedding models (one-time, ~160 MB)...")
    theme.muted("  Cached in ~/.cache/starling/embeddings/ — persists across reboots.")
    theme.muted("  • Routing: all-MiniLM-L6-v2 (~23 MB)")
    theme.muted("  • Memory:  nomic-embed-text-v1.5 (~138 MB)")
    try:
        from fastembed import TextEmbedding
        from fastembed.common.types import Device
        import semantic_router as _sr
        import crew_memory as _cm
        cache_dir = os.path.expanduser("~/.cache/starling/embeddings")
        os.makedirs(cache_dir, exist_ok=True)
        print("  Downloading routing model...", end=" ", flush=True)
        TextEmbedding(_sr._ROUTING_MODEL, cuda=Device.CPU, cache_dir=cache_dir)
        theme.success("done")
        print("  Downloading memory model...", end=" ", flush=True)
        TextEmbedding(_cm._EMBED_MODEL, cuda=Device.CPU, cache_dir=cache_dir)
        theme.success("done")
        print()
    except Exception as e:
        theme.warn(f"  Could not pre-download models: {e}")
        theme.muted("  They'll be downloaded automatically on first task.")


# === Export (.starling backup files) ===

def export_backup(out_path: str = "", with_secrets: bool = True) -> Optional[str]:
    """Export current project_config.json as a .starling backup.

    By default, secrets ARE included so restore is zero-setup (private backup).
    Pass with_secrets=False to strip API keys and Telegram tokens for sharing.
    """
    import datetime as _dt
    from config_loader import load_project_config, config_exists
    from model_wizard import load_presets, BUILTIN_PRESETS

    if not config_exists():
        theme.error("No project_config.json to export. Run 'starling setup' first.")
        return None

    config = load_project_config()
    project = config.get("project", {}) or {}

    all_presets = load_presets()
    # Include ALL non-default presets: custom keys AND builtin overrides that
    # differ from the shipped defaults. This is what model_presets.json already
    # stores (see save_custom_presets), so read it directly for full fidelity.
    from model_wizard import PRESETS_FILE
    custom_presets = {}
    if os.path.exists(PRESETS_FILE):
        try:
            with open(PRESETS_FILE) as f:
                custom_presets = json.load(f)
        except (OSError, json.JSONDecodeError):
            custom_presets = {}

    _now = _dt.datetime.now()
    backup = {
        "backup_name": f"Starling export — {project.get('name') or 'project'} — "
                       f"{_now.strftime('%Y-%m-%d %H:%M')}",
        "starling_version": _starling_version(),
        "backup_format_version": 1,
        "exported_at": _now.isoformat(timespec="seconds"),
        "exported_at_human": _now.strftime("%Y-%m-%d %H:%M:%S"),
        "has_secrets": bool(with_secrets),
        "project": {
            "name": project.get("name", ""),
            "description": project.get("description", ""),
        },
        "agents": config.get("agents", []),
        "default_tasks": config.get("default_tasks", []),
        "routing": config.get("routing", {"keywords": {}, "default_agent": ""}),
        "model_presets": custom_presets,
        "skills": config.get("skills", []),
    }

    # Include cron jobs from work dir (config, not runtime data)
    work_dir_abs = os.path.expanduser(project.get("work_dir") or "")
    if work_dir_abs:
        cron_path = os.path.join(work_dir_abs, "cron_config.json")
        if os.path.exists(cron_path):
            try:
                with open(cron_path) as f:
                    backup["cron_jobs"] = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass

        # Include custom skill code files (work_dir/skills/*.py)
        skills_dir = os.path.join(work_dir_abs, "skills")
        if os.path.isdir(skills_dir):
            skills_bundle = {}
            for fname in os.listdir(skills_dir):
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(skills_dir, fname)
                try:
                    with open(fpath) as f:
                        skills_bundle[fname] = f.read()
                except OSError:
                    pass
            if skills_bundle:
                backup["skill_files"] = skills_bundle

    if with_secrets:
        # Export EVERY key=value in work_dir/.env (tool keys, not just model keys).
        # Plus any preset-referenced env vars present in os.environ as a fallback.
        api_keys = {}
        work_dir = project.get("work_dir") or ""
        env_file = os.path.join(os.path.expanduser(work_dir), ".env") if work_dir else ""
        if env_file and os.path.exists(env_file):
            try:
                with open(env_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, _, val = line.partition("=")
                        k = k.strip()
                        val = val.strip().strip('"').strip("'")
                        if k and val:
                            api_keys[k] = val
            except OSError:
                pass

        # Also pick up preset-referenced keys from the current process env if
        # they're missing from .env (useful when keys were exported in ~/.bashrc).
        referenced_env_vars = {
            v.get("api_key_env") for v in all_presets.values() if v.get("api_key_env")
        }
        for k in referenced_env_vars:
            if k and k not in api_keys and os.environ.get(k):
                api_keys[k] = os.environ[k]
        backup["api_keys"] = api_keys

        # Telegram tokens (from work dir telegram_config.json if present)
        tg_path = os.path.join(os.path.expanduser(work_dir), "telegram_config.json") if work_dir else ""
        if tg_path and os.path.exists(tg_path):
            try:
                with open(tg_path) as f:
                    tg = json.load(f)
                if tg.get("bot_token"):
                    backup["bot_token"] = tg["bot_token"]
                if tg.get("chat_id"):
                    backup["chat_id"] = tg["chat_id"]
            except (OSError, json.JSONDecodeError):
                pass

    if not out_path:
        from preferences import get_backup_dir, set_backup_dir, DEFAULT_BACKUP_DIR
        current_dir = get_backup_dir()

        # Mini menu: one question, blank = keep current default.
        try:
            print()
            print(theme.color(
                f"  Backup directory [{current_dir}]:", "highlight"
            ))
            answer = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""

        if answer:
            chosen_dir = os.path.expanduser(answer)
            if chosen_dir != current_dir:
                set_backup_dir(chosen_dir)
                theme.muted(f"  Saved as default: {chosen_dir}")
            current_dir = chosen_dir

        name = project.get("name") or "project"
        ts = _now.strftime("%Y-%m-%d %H-%M")
        suffix = "" if with_secrets else " (share-safe)"
        out_path = os.path.join(
            current_dir, f"Starling export {name} {ts}{suffix}.starling"
        )

    out_path = os.path.expanduser(out_path)
    try:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(backup, f, indent=2)
        chmod_ok = True
        if with_secrets:
            try:
                os.chmod(out_path, 0o600)
            except OSError:
                chmod_ok = False
    except OSError as e:
        theme.error(f"Cannot write backup: {e}")
        return None

    theme.success(f"Exported to {out_path}")
    if with_secrets:
        theme.warn("  ⚠ This backup contains API keys and bot tokens — do NOT share.")
        if chmod_ok:
            theme.muted("  File permissions set to 600 (owner read/write only).")
        else:
            theme.warn("  Could not set permissions to 600 (filesystem may not support it).")
            theme.warn("  Store this file somewhere secure — it may be world-readable.")
    else:
        theme.muted("  Secrets stripped. Safe to share.")
    return out_path


# === Import flow (.starling backup files) ===

def _run_import_flow() -> bool:
    """Import a .starling backup file. Returns True if completed, False if cancelled."""
    theme.clear_screen()
    theme.banner("Import existing config")
    print("  Import a .starling backup file to restore a previous setup.")
    print("  The file contains agent configs, model presets, routing, and skill references.")
    print("  (If the backup includes secrets, they'll be restored automatically.)\n")
    print(_nav_hint())

    # Show all backups in the configured directory, let user pick by number.
    from preferences import get_backup_dir
    default_dir = get_backup_dir()

    def _list_backups():
        if not os.path.isdir(default_dir):
            return []
        files = [f for f in os.listdir(default_dir) if f.endswith(".starling")]
        # Sort newest-first by mtime
        return sorted(
            files,
            key=lambda f: os.path.getmtime(os.path.join(default_dir, f)),
            reverse=True,
        )

    import datetime as _dt
    path = None
    backup = None

    while path is None:
        theme.clear_screen()
        theme.banner("Import existing config")
        print(f"  Backup directory: {theme.color(default_dir, 'muted')}\n")

        files = _list_backups()
        if not files:
            theme.warn("  No .starling backups found in that directory.")
            print(f"  {theme.color('p', 'highlight')}) Enter a custom path")
            print(f"  {theme.color('b', 'highlight')}) Back")
            print(f"  {theme.color('q', 'highlight')}) Quit\n")
            try:
                choice = input(theme.color("  Choice: ", "highlight")).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            if choice == "b":
                return False
            if choice == "q":
                return False
            if choice == "p":
                manual = _prompt_nav("Path to .starling file", required=True)
                if manual in (_BACK, _QUIT):
                    return False
                candidate = os.path.expanduser(manual)
                if not os.path.exists(candidate):
                    theme.error(f"File not found: {candidate}")
                    input(theme.color("  Press Enter to continue...", "muted"))
                    continue
                path = candidate
                break
            continue

        print("  Available backups:\n")
        for i, fname in enumerate(files, 1):
            fpath = os.path.join(default_dir, fname)
            mtime = _dt.datetime.fromtimestamp(os.path.getmtime(fpath))
            size_kb = os.path.getsize(fpath) / 1024
            # Peek at has_secrets without full validation
            try:
                with open(fpath) as f:
                    peek = json.load(f)
                has_secrets = peek.get("has_secrets", False)
            except Exception:
                has_secrets = False
            secret_tag = theme.color(" [with secrets]", "success") if has_secrets else theme.color(" [stripped]", "muted")
            print(f"    {theme.color(f'{i:>2}', 'highlight')}) "
                  f"{fname}  "
                  f"{theme.color(mtime.strftime('%Y-%m-%d %H:%M'), 'muted')}  "
                  f"{theme.color(f'{size_kb:.0f} KB', 'muted')}{secret_tag}")

        print()
        print(f"  {theme.color('<num>', 'highlight')}       Import that backup")
        print(f"  {theme.color('d <num>', 'error')}     Delete that backup")
        print(f"  {theme.color('p', 'highlight')}           Enter a custom path")
        print(f"  {theme.color('b', 'highlight')}           Back")
        print(f"  {theme.color('q', 'highlight')}           Quit\n")

        try:
            choice = input(theme.color("  Choice: ", "highlight")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if choice == "b":
            return False
        if choice == "q":
            return False
        if choice == "p":
            manual = _prompt_nav("Path to .starling file", required=True)
            if manual in (_BACK, _QUIT):
                continue
            candidate = os.path.expanduser(manual)
            if not os.path.exists(candidate):
                theme.error(f"File not found: {candidate}")
                input(theme.color("  Press Enter to continue...", "muted"))
                continue
            path = candidate
            break

        # Delete action: "d 3"
        if choice.startswith("d "):
            try:
                del_idx = int(choice[2:].strip()) - 1
            except ValueError:
                theme.error("  Usage: d <number>")
                input(theme.color("  Press Enter to continue...", "muted"))
                continue
            if not (0 <= del_idx < len(files)):
                theme.error("  Number out of range.")
                input(theme.color("  Press Enter to continue...", "muted"))
                continue
            target = files[del_idx]
            theme.warn(f"\n  Delete '{target}' permanently?")
            try:
                confirm = input(theme.color("  Type 'delete' to confirm: ", "error")).strip().lower()
            except (EOFError, KeyboardInterrupt):
                continue
            if confirm != "delete":
                theme.muted("  Cancelled.")
                input(theme.color("  Press Enter to continue...", "muted"))
                continue
            try:
                os.remove(os.path.join(default_dir, target))
                theme.success(f"  Deleted {target}")
            except OSError as e:
                theme.error(f"  Could not delete: {e}")
            input(theme.color("  Press Enter to continue...", "muted"))
            continue

        # Numeric selection → import
        try:
            idx = int(choice) - 1
        except ValueError:
            theme.error("  Enter a number, 'd <num>' to delete, 'p' for custom path, 'b' back, or 'q' quit.")
            input(theme.color("  Press Enter to continue...", "muted"))
            continue
        if not (0 <= idx < len(files)):
            theme.error("  Number out of range.")
            input(theme.color("  Press Enter to continue...", "muted"))
            continue
        candidate = os.path.join(default_dir, files[idx])
        # Validate before committing — invalid picks loop back to the list
        # without recursing (keeps the call stack flat).
        backup, errors = _load_and_validate_backup(candidate)
        if errors:
            theme.error("This backup has problems:")
            for err in errors:
                print(f"    • {err}")
            input(theme.color("  Press Enter to return to the list...", "muted"))
            continue
        path = candidate
        break

    # `path` holds the validated backup's location; `backup` is already loaded.
    # (For the custom-path branch above, we also validate before breaking.)
    if path and backup is None:
        backup, errors = _load_and_validate_backup(path)
        if errors:
            theme.error("This backup has problems:")
            for err in errors:
                print(f"    • {err}")
            return False

    # Preview
    theme.clear_screen()
    theme.banner("Backup preview")
    project = backup.get("project", {})
    agents = backup.get("agents", [])
    # Support current export schema and the legacy keys in case an older
    # backup is imported.
    presets = backup.get("model_presets") or backup.get("custom_presets") or {}
    skill_files = backup.get("skill_files") or {}
    skill_refs = backup.get("skills") or backup.get("skill_names") or []
    created = backup.get("exported_at_human") or backup.get("exported_at") \
        or backup.get("meta", {}).get("created_at") or "unknown"
    version = backup.get("starling_version") \
        or backup.get("meta", {}).get("starling_version") or "unknown"
    has_secrets_flag = backup.get("has_secrets", False)

    print(f"  Created:  {theme.color(created, 'muted')}")
    print(f"  From:     {theme.color(version, 'muted')}")
    if has_secrets_flag:
        print(f"  Secrets:  {theme.color('included', 'success')}\n")
    else:
        print(f"  Secrets:  {theme.color('stripped', 'muted')}\n")
    print(f"  Project:  {theme.color(project.get('name', '(unnamed)'), 'accent', bold=True)}")
    print(f"  Agents:   {len(agents)}")
    for a in agents:
        tier = a.get("tier", "specialist")
        tier_color = "highlight" if tier == "leader" else "accent" if tier == "coordinator" else "muted"
        print(f"    • {theme.color(a.get('name', a.get('id', '?')), 'accent', bold=True):25s} "
              f"({theme.color(tier, tier_color)}, {a.get('preset', '?')})")
    if presets:
        print(f"  Model presets:       {len(presets)} (custom + builtin overrides)")
    if skill_files:
        print(f"  Skill files bundled: {len(skill_files)} (restored to work_dir/skills)")
    elif skill_refs:
        print(f"  Skills referenced:   {len(skill_refs)} (reinstall skill files separately)")
    if backup.get("cron_jobs"):
        print(f"  Cron jobs:           restored")
    if backup.get("api_keys"):
        print(f"  API keys:            {len(backup['api_keys'])} restored to work_dir/.env")
    if backup.get("bot_token"):
        print(f"  Telegram:            bot_token + chat_id restored")

    # Choose to use as-is or open in wizard
    print()
    print("  What would you like to do?\n")
    print(f"    {theme.color('1', 'highlight')}) Use as-is and launch {theme.color('(recommended)', 'muted')}")
    print(f"    {theme.color('2', 'highlight')}) Edit in wizard first")
    print(f"    {theme.color('3', 'highlight')}) Cancel")

    try:
        choice = input(theme.color("\n  Choice [1]: ", "highlight")).strip() or "1"
    except (EOFError, KeyboardInterrupt):
        return False

    if choice == "3":
        return False
    if choice == "2":
        # Apply then drop into advanced wizard for edits
        work_dir = _apply_backup(backup, use_default_work_dir=True)
        if not work_dir:
            return False
        theme.info("Backup loaded. You can now edit it with the Advanced wizard.")
        try:
            input(theme.color("  Press Enter to continue...", "muted"))
        except (EOFError, KeyboardInterrupt):
            return True
        _run_full_wizard()
        return True

    # Default: use as-is and launch
    work_dir = _apply_backup(backup, use_default_work_dir=True)
    if not work_dir:
        return False

    theme.clear_screen()
    theme.banner("Import Complete!")
    theme.success(f"Config restored from {path}")
    print(f"  Project: {theme.color(project.get('name', '(unnamed)'), 'accent')}")
    print(f"  Agents:  {len(agents)}")
    print(f"  Work dir: {work_dir}\n")

    # Flag missing keys
    if presets or agents:
        from model_wizard import load_presets
        all_presets = load_presets()
        missing = set()
        for a in agents:
            p = all_presets.get(a.get("preset", ""), {})
            ke = p.get("api_key_env")
            if ke and not os.environ.get(ke):
                missing.add(ke)
        if missing:
            theme.warn(f"Missing API keys (add to {work_dir}/.env before running):")
            for k in sorted(missing):
                print(f"    • {k}")

    prefetch_embedding_models()
    _launch_starling_or_exit(os.path.dirname(os.path.abspath(__file__)))
    return True


def _load_and_validate_backup(path: str):
    """Load a .starling file and validate its structure.

    Returns:
        (backup_dict, errors_list). If errors_list is empty, the backup is safe to apply.
    """
    errors = []
    # Reject absurdly large files before parsing — a 1 GB JSON of repeated
    # characters is technically valid and would hang `json.load` for minutes
    # while spiking memory. Real backups are well under 1 MB.
    _MAX_BACKUP_BYTES = 10 * 1024 * 1024  # 10 MB
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, [f"Cannot stat file: {e}"]
    if size > _MAX_BACKUP_BYTES:
        return None, [f"Backup file too large ({size:,} bytes; max {_MAX_BACKUP_BYTES:,})"]

    try:
        with open(path) as f:
            backup = json.load(f)
    except json.JSONDecodeError as e:
        return None, [f"Not valid JSON: {e}"]
    except (OSError, UnicodeDecodeError) as e:
        return None, [f"Cannot read file: {e}"]

    if not isinstance(backup, dict):
        return None, ["Backup root is not a JSON object"]

    # Required top-level keys
    if "project" not in backup:
        errors.append("Missing 'project' section")
    if "agents" not in backup:
        errors.append("Missing 'agents' section")

    # Validate project section (must be a dict, not null or primitive)
    project = backup.get("project")
    if "project" in backup and not isinstance(project, dict):
        errors.append("'project' is not a valid object (must be a JSON object, not null/primitive)")

    # Validate agents
    agents_raw = backup.get("agents")
    if not isinstance(agents_raw, list):
        errors.append("'agents' is not a list")
        agents = []
    elif len(agents_raw) == 0:
        errors.append("'agents' is empty — backup must contain at least one agent")
        agents = []
    else:
        agents = agents_raw
        seen_ids = set()
        leader_count = 0
        for i, a in enumerate(agents):
            if not isinstance(a, dict):
                errors.append(f"Agent #{i + 1}: not a dict")
                continue
            aid = a.get("id", "")
            if not aid:
                errors.append(f"Agent #{i + 1}: missing id")
            elif aid in seen_ids:
                errors.append(f"Agent #{i + 1}: duplicate id '{aid}'")
            seen_ids.add(aid)

            # Manager keyword check (security)
            for field in ("id", "name", "role"):
                if _contains_manager(a.get(field, "")):
                    errors.append(
                        f"Agent '{aid}': '{field}' contains blocked word 'manager'"
                    )

            # Tier validation
            tier = a.get("tier", "specialist")
            if tier not in ("specialist", "coordinator", "leader"):
                errors.append(f"Agent '{aid}': invalid tier '{tier}'")
            if tier == "leader":
                leader_count += 1

        if leader_count > 1:
            errors.append(f"Multiple Leaders ({leader_count}) — only one allowed per project")

    # Max agents check
    if len(agents) > MAX_AGENTS:
        errors.append(f"Too many agents: {len(agents)} (max {MAX_AGENTS})")

    # Secrets (api_keys, bot_token, chat_id) are allowed when has_secrets=True.
    # The importer will restore them into the work dir .env and telegram_config.json.

    return backup, errors


def _apply_backup(backup: dict, use_default_work_dir: bool = True) -> Optional[str]:
    """Apply a validated backup — write project_config.json and create work dir.

    Returns the resolved work_dir path, or None on failure.

    Security: when use_default_work_dir=True (the only mode currently used by
    the import wizard), we IGNORE any work_dir embedded in the backup and
    always derive a fresh path from the project name. This prevents a malicious
    backup from writing JSON/.env content to arbitrary filesystem locations
    (e.g. ~/.ssh, /etc, ~/.bashrc) under the user's privileges.
    """
    project = backup.get("project", {})

    if use_default_work_dir:
        # Always derive — never trust backup-supplied work_dir.
        name_slug = (project.get("name") or "imported").lower().replace(" ", "-")
        # Strip filesystem-hostile chars from the slug as a second line of defense.
        name_slug = "".join(c for c in name_slug if c.isalnum() or c in "-_") or "imported"
        work_dir = os.path.expanduser(f"~/starling-projects/{name_slug}")
    else:
        # Caller explicitly requested honoring the embedded path. Only safe when
        # the caller has already validated it (no current code path uses this).
        work_dir = project.get("work_dir", "")
        if work_dir:
            work_dir = os.path.expanduser(work_dir)

    if not work_dir:
        theme.error("Could not determine work directory from backup.")
        return None

    try:
        os.makedirs(work_dir, exist_ok=True)
        for sub in ("output", "memory", "skills"):
            os.makedirs(os.path.join(work_dir, sub), exist_ok=True)
    except OSError as e:
        theme.error(f"Cannot create work dir {work_dir}: {e}")
        return None

    # Build config from backup
    config = {
        "project": {
            "name": project.get("name", ""),
            "description": project.get("description", ""),
            "work_dir": work_dir,
        },
        "agents": backup.get("agents", []),
        "max_agents": MAX_AGENTS,
        "default_tasks": backup.get("default_tasks", []),
        "routing": backup.get("routing", {"keywords": {}, "default_agent": ""}),
    }

    # Restore model presets (custom keys + any builtin overrides the backup carried).
    # Merge with whatever is already on disk so we don't drop local edits that
    # aren't represented in the backup. save_custom_presets re-diffs against
    # BUILTIN_PRESETS, so only actual custom/override entries are persisted.
    backup_presets = backup.get("model_presets") or backup.get("custom_presets") or {}
    if backup_presets:
        try:
            from model_wizard import load_presets, save_custom_presets, PRESETS_FILE
            # Read the raw overrides file (not merged with builtins) so we only
            # write custom keys + existing overrides back — never every builtin.
            on_disk = {}
            if os.path.exists(PRESETS_FILE):
                try:
                    with open(PRESETS_FILE) as f:
                        on_disk = json.load(f) or {}
                except (OSError, json.JSONDecodeError):
                    on_disk = {}
            on_disk.update(backup_presets)
            save_custom_presets(on_disk)
        except Exception as e:
            theme.warn(f"Could not import custom model presets: {e}")

    # Restore API keys into work dir .env (from with-secrets backups)
    api_keys = backup.get("api_keys") or {}
    if api_keys:
        env_path = os.path.join(work_dir, ".env")
        try:
            # Merge with existing .env, overwriting keys present in backup
            existing_env = {}
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, _, v = line.partition("=")
                        existing_env[k.strip()] = v.strip().strip('"').strip("'")
            # Sanitize incoming api_keys before merging.
            # Rejects malformed names + values containing CR/LF/NUL that would
            # let a malicious backup inject extra env vars (e.g. LD_PRELOAD).
            sanitized_keys = {}
            rejected = []
            for k, v in api_keys.items():
                ck, cv = _sanitize_env_pair(k, v)
                if ck is None:
                    rejected.append(k)
                    continue
                sanitized_keys[ck] = cv
            if rejected:
                theme.warn(f"  Rejected {len(rejected)} unsafe env entries from backup: {', '.join(rejected[:5])}")
            existing_env.update(sanitized_keys)
            with open(env_path, "w") as f:
                for k, v in existing_env.items():
                    f.write(f"{k}={v}\n")
            try:
                os.chmod(env_path, 0o600)
            except OSError:
                theme.warn(f"  Could not restrict {env_path} to 600 — contents may be world-readable.")
            # Also set in current process env so the wizard's launch sees them
            for k, v in sanitized_keys.items():
                os.environ[k] = v
        except OSError as e:
            theme.warn(f"Could not write .env: {e}")

    # Restore cron jobs. Force every imported job to "pending_approval" so a
    # malicious backup can't ship pre-activated cron entries that auto-fire
    # against the user's LLM-connected crew on the next heartbeat tick.
    cron_jobs = backup.get("cron_jobs")
    if cron_jobs:
        try:
            quarantined = 0
            normalized = []
            # cron_jobs schema can be either a list of job dicts or a wrapper
            # object containing a "jobs" list — handle both.
            if isinstance(cron_jobs, dict):
                jobs_list = cron_jobs.get("jobs", []) if isinstance(cron_jobs.get("jobs"), list) else []
                wrapper = {k: v for k, v in cron_jobs.items() if k != "jobs"}
            elif isinstance(cron_jobs, list):
                jobs_list = cron_jobs
                wrapper = None
            else:
                jobs_list = []
                wrapper = None
            for job in jobs_list:
                if not isinstance(job, dict):
                    continue
                if job.get("status") == "active":
                    job["status"] = "pending_approval"
                    quarantined += 1
                normalized.append(job)
            payload = {**wrapper, "jobs": normalized} if wrapper is not None else normalized
            with open(os.path.join(work_dir, "cron_config.json"), "w") as f:
                json.dump(payload, f, indent=2)
            if quarantined:
                theme.warn(f"  {quarantined} restored cron job(s) set to 'pending_approval'. "
                           f"Review and approve via TUI or Telegram before they run.")
        except OSError as e:
            theme.warn(f"Could not write cron_config.json: {e}")

    # Restore custom skill files. Defense-in-depth against malicious backups:
    # - basename equality blocks any path component (../ , subdirs)
    # - explicit null-byte rejection (Python strings allow \x00; OS layer truncates)
    # - leading dot rejected (no .bashrc-style hidden files)
    # - extension lock to .py
    # - realpath check confirms the resolved destination is inside skills_dir
    skill_files = backup.get("skill_files") or {}
    if skill_files:
        skills_dir = os.path.join(work_dir, "skills")
        try:
            os.makedirs(skills_dir, exist_ok=True)
            skills_dir_real = os.path.realpath(skills_dir)
            rejected_files = []
            for fname, content in skill_files.items():
                if not isinstance(fname, str) or not isinstance(content, str):
                    rejected_files.append(repr(fname))
                    continue
                if (
                    "\x00" in fname
                    or os.path.basename(fname) != fname
                    or fname.startswith(".")
                    or not fname.endswith(".py")
                    or fname in ("", ".py")
                ):
                    rejected_files.append(fname)
                    continue
                dest = os.path.join(skills_dir, fname)
                # Resolve and verify the final path stays under skills_dir.
                # Catches symlink-based escapes if skills_dir itself contains a symlink.
                if os.path.realpath(os.path.dirname(dest)) != skills_dir_real:
                    rejected_files.append(fname)
                    continue
                with open(dest, "w") as f:
                    f.write(content)
            if rejected_files:
                theme.warn(f"  Rejected {len(rejected_files)} unsafe skill filenames: "
                           f"{', '.join(rejected_files[:5])}")
        except OSError as e:
            theme.warn(f"Could not restore skill files: {e}")

    # Restore Telegram config
    bot_token = backup.get("bot_token")
    chat_id = backup.get("chat_id")
    if bot_token or chat_id:
        tg_path = os.path.join(work_dir, "telegram_config.json")
        tg = {}
        if os.path.exists(tg_path):
            try:
                with open(tg_path) as f:
                    tg = json.load(f)
            except (OSError, json.JSONDecodeError):
                tg = {}
        if bot_token:
            tg["bot_token"] = bot_token
        if chat_id:
            tg["chat_id"] = chat_id
        try:
            with open(tg_path, "w") as f:
                json.dump(tg, f, indent=2)
            try:
                os.chmod(tg_path, 0o600)
            except OSError:
                theme.warn(f"  Could not restrict {tg_path} to 600 — bot token may be world-readable.")
        except OSError as e:
            theme.warn(f"Could not write telegram_config.json: {e}")

    config_path = os.path.join(os.path.dirname(__file__), "project_config.json")
    try:
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
    except OSError as e:
        theme.error(f"Cannot write config: {e}")
        return None

    return work_dir


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
    # IMPORTANT: exit dark screen BEFORE execv — the replacement process will
    # inherit the current terminal state. Without this, the TUI starts inside
    # our dark-painted alt buffer which causes visual glitches.
    theme.exit_dark_screen()
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

    # Use subprocess (no shell) so a path containing spaces or shell
    # metacharacters can't be interpreted. update-desktop-database is
    # optional — quietly ignore if missing or if it fails.
    try:
        import subprocess
        subprocess.run(
            ["update-desktop-database", desktop_dir],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        pass


if __name__ == "__main__":
    run_setup()
