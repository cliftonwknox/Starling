"""CrewTUI Setup Wizard — Interactive first-run configuration."""

import os
import json
import sys
import readline  # enables line editing, history, and arrow keys in input()

COLORS = ["cyan", "green", "yellow", "magenta", "blue", "red", "white", "orange"]
MAX_AGENTS = 10


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
    print(f"\n  To launch:  cd {project_dir} && uv run crewtui\n")


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
    """Check if the selected preset needs an API key and prompt if missing."""
    from dotenv import load_dotenv
    source_env = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(source_env, override=False)

    preset = presets.get(preset_name, {})
    env_var = preset.get("api_key_env")
    if not env_var:
        return  # local model, no key needed

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
    # ID
    while True:
        default_id = f"agent{index + 1}" if index > 0 else "leader"
        agent_id = _prompt("Agent ID (short, no spaces)", default_id).lower().replace(" ", "_")
        if agent_id in used_ids:
            print(f"  ID '{agent_id}' already taken. Pick another.")
            continue
        if "manager" in agent_id:
            print(f"  WARNING: CrewAI treats agents with 'manager' in the ID as hierarchical managers.")
            print(f"    These agents CANNOT have tools assigned. Consider 'coordinator' or 'lead' instead.")
            confirm = _prompt("  Keep this ID anyway? (y/n)", "n")
            if confirm.lower() != "y":
                continue
        break

    name = _prompt("Display name", agent_id.replace("_", " ").title())
    role = _prompt("Role (what CrewAI sees)", name)
    if "manager" in role.lower() and "manager" not in agent_id:
        print(f"  WARNING: CrewAI may treat agents with 'manager' in the role as hierarchical managers.")
        print(f"    These agents cannot have tools. Consider 'coordinator' or 'lead' instead.")
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
            if new_id in used_ids and new_id != agent["id"]:
                print(f"  ID '{new_id}' already taken.")
            else:
                agent["id"] = new_id
        elif edit == "2":
            agent["name"] = _prompt("Display name", agent["name"])
        elif edit == "3":
            agent["role"] = _prompt("Role", agent["role"])
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

    # Detect terminal emulator
    terminal = _detect_terminal()
    if terminal:
        exec_line = f'{terminal} -e "cd {project_dir} && uv run crewtui"'
    else:
        exec_line = f'cd {project_dir} && uv run crewtui'

    content = f"""[Desktop Entry]
Name={project_name} (CrewTUI)
Comment=Launch {project_name} Agent Command Center
Exec={exec_line}
Icon={icon_path if os.path.exists(icon_path) else 'utilities-terminal'}
Terminal={'true' if not terminal else 'false'}
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
