"""CrewTUI Setup Wizard — Interactive first-run configuration."""

import os
import json
import sys

COLORS = ["cyan", "green", "yellow", "magenta", "blue", "red", "white", "orange"]
MAX_AGENTS = 10


def _prompt(text, default=""):
    if default:
        result = input(f"  {text} [{default}]: ").strip()
        return result if result else default
    return input(f"  {text}: ").strip()


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
    """Main setup wizard entry point."""
    _banner("CrewTUI Setup Wizard")
    print("  This wizard will configure your CrewTUI project.\n")

    # Step 1: Project info
    project_name = _prompt("Project name", "My Crew")
    project_desc = _prompt("Project description", "AI-powered multi-agent system")

    # Step 2: Working directory
    default_dir = os.path.expanduser(f"~/crewtui-projects/{project_name.lower().replace(' ', '-')}")
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

    # Load presets for selection
    from model_wizard import load_presets
    presets = load_presets()
    preset_keys = list(presets.keys())

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
    _banner("API Key Check")
    _check_api_keys(agents, presets)

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
    print(f"\n  Run 'crewtui' to launch the TUI.\n")


def _setup_agent(index: int, preset_keys: list, presets: dict, available_tools: dict, used_ids: set) -> dict:
    """Configure a single agent."""
    # ID
    while True:
        default_id = f"agent{index + 1}" if index > 0 else "leader"
        agent_id = _prompt("Agent ID (short, no spaces)", default_id).lower().replace(" ", "_")
        if agent_id in used_ids:
            print(f"  ID '{agent_id}' already taken. Pick another.")
            continue
        if "manager" in agent_id:
            print(f"  ⚠ WARNING: CrewAI treats agents with 'manager' in the ID as hierarchical managers.")
            print(f"    These agents CANNOT have tools assigned. Consider 'coordinator' or 'lead' instead.")
            confirm = _prompt("  Keep this ID anyway? (y/n)", "n")
            if confirm.lower() != "y":
                continue
        break

    name = _prompt("Display name", agent_id.title())
    role = _prompt("Role (what CrewAI sees)", name)
    if "manager" in role.lower() and "manager" not in agent_id:
        print(f"  ⚠ WARNING: CrewAI may treat agents with 'manager' in the role as hierarchical managers.")
        print(f"    These agents cannot have tools. Consider 'coordinator' or 'lead' instead.")
    goal = _prompt("Goal (1-2 sentences)")
    backstory = _prompt("Backstory (personality/expertise)")

    # Model preset
    print(f"\n  Available model presets:")
    for i, key in enumerate(preset_keys, 1):
        p = presets[key]
        print(f"    {i}) {key:18s} {p['label']} via {p['provider']}")
    while True:
        choice = _prompt("Model preset", preset_keys[0] if preset_keys else "")
        if choice in presets or choice.isdigit():
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(preset_keys):
                    choice = preset_keys[idx]
                else:
                    print("  Invalid number.")
                    continue
            break
        print(f"  Unknown preset. Use one of: {', '.join(preset_keys)}")
    preset = choice

    # Tools
    print(f"\n  Available tools:")
    tool_list = sorted(available_tools.keys())
    for i, tid in enumerate(tool_list, 1):
        info = available_tools[tid]
        print(f"    {i}) {tid} — {info['description'][:40]}")
    tools_input = _prompt("Tools (numbers or IDs, comma-sep, blank=none)", "")
    tools = []
    if tools_input.strip():
        for part in tools_input.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(tool_list):
                    tools.append(tool_list[idx])
            elif part in available_tools:
                tools.append(part)

    # Color
    color = COLORS[index % len(COLORS)]
    custom_color = _prompt("Color", color)
    if custom_color in COLORS:
        color = custom_color

    # Delegation
    allow_delegation = _prompt_yn("Allow delegation?", index == 0)

    return {
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


def _check_api_keys(agents: list, presets: dict):
    """Check which API keys are needed and prompt for missing ones."""
    from dotenv import load_dotenv
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_file)

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

    env_updates = {}
    for env_var, info in needed.items():
        existing = os.environ.get(env_var)
        if existing:
            print(f"  {env_var:25s} -> {info['provider']:15s} [green]set[/] (used by: {', '.join(info['agents'])})")
        else:
            print(f"  {env_var:25s} -> {info['provider']:15s} [red]MISSING[/] (used by: {', '.join(info['agents'])})")
            key = _prompt(f"Enter {env_var} (blank to skip)")
            if key:
                env_updates[env_var] = key
                os.environ[env_var] = key

    if env_updates:
        # Append to .env
        existing_env = {}
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        existing_env[k.strip()] = v.strip()
        existing_env.update(env_updates)
        with open(env_file, "w") as f:
            for k, v in existing_env.items():
                f.write(f"{k}={v}\n")
        os.chmod(env_file, 0o600)
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


def _generate_desktop_shortcut(project_name: str):
    """Generate a .desktop file for the app menu."""
    desktop_dir = os.path.expanduser("~/.local/share/applications")
    icon_path = os.path.expanduser("~/.local/share/icons/crewtui.svg")
    safe_name = project_name.lower().replace(" ", "-")
    desktop_path = os.path.join(desktop_dir, f"crewtui-{safe_name}.desktop")

    project_dir = os.path.dirname(__file__)

    # Install bundled icon if not already present
    bundled_icon = os.path.join(project_dir, "crewtui.svg")
    if os.path.exists(bundled_icon) and not os.path.exists(icon_path):
        os.makedirs(os.path.dirname(icon_path), exist_ok=True)
        import shutil
        shutil.copy2(bundled_icon, icon_path)

    content = f"""[Desktop Entry]
Name={project_name} (CrewTUI)
Comment=Launch {project_name} Agent Command Center
Exec=gnome-terminal -- bash -c "cd {project_dir} && uv run python tui.py; exec bash"
Icon={icon_path if os.path.exists(icon_path) else 'utilities-terminal'}
Terminal=false
Type=Application
Categories=Development;Utility;
Keywords=crewai;crewtui;agents;
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
