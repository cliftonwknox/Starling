"""CrewTUI — Config-driven CrewAI multi-agent system.

Agents, tasks, and crew composition are loaded from project_config.json.
Tools are sourced from three tiers: built-in, crewai ecosystem, and custom skills.
"""

import os
import importlib
import inspect
import litellm
from crewai import Agent, Task, Crew, LLM
from crewai.tools import BaseTool
from crewai_tools import ScrapeWebsiteTool
from ddgs import DDGS
from pydantic import Field

# xAI models don't support 'stop' parameter - drop it globally
litellm.drop_params = True


# === Built-in Tools ===

class DDGSearchTool(BaseTool):
    name: str = "DuckDuckGo Search"
    description: str = "Search the web using DuckDuckGo. Returns top results with titles, URLs, and snippets."
    max_results: int = Field(default=8)

    def _run(self, query: str) -> str:
        try:
            results = DDGS().text(query, max_results=self.max_results)
            if not results:
                return "No results found."
            output = []
            for r in results:
                output.append(f"**{r.get('title', 'No title')}**\n{r.get('href', '')}\n{r.get('body', '')}\n")
            return "\n".join(output)
        except Exception as e:
            return f"Search error: {e}"


class TavilySearchTool(BaseTool):
    name: str = "Tavily Search"
    description: str = "Search the web using Tavily for high-quality, AI-optimized results."

    def _run(self, query: str) -> str:
        try:
            from tavily import TavilyClient
            api_key = os.environ.get("TAVILY_API_KEY")
            if not api_key:
                return "Tavily API key not set."
            client = TavilyClient(api_key=api_key)
            response = client.search(query, max_results=5)
            results = response.get("results", [])
            if not results:
                return "No results found."
            output = []
            for r in results:
                output.append(f"**{r.get('title', 'No title')}**\n{r.get('url', '')}\n{r.get('content', '')}\n")
            return "\n".join(output)
        except Exception as e:
            return f"Tavily error: {e}"


# Built-in tool instances
_ddg_search = DDGSearchTool()
_tavily_search = TavilySearchTool()
_scrape_website = ScrapeWebsiteTool()

BUILTIN_TOOLS = {
    "ddg_search": {"instance": _ddg_search, "description": "DuckDuckGo web search (free, no key)"},
    "tavily_search": {"instance": _tavily_search, "description": "Tavily AI search (needs TAVILY_API_KEY)"},
    "scrape_website": {"instance": _scrape_website, "description": "Scrape any URL"},
}


# === CrewAI Ecosystem Tools ===

# Known crewai_tools that can be loaded on demand (prefixed with crewai: in config)
CREWAI_TOOLS_CATALOG = {
    "FileReadTool": "A tool for reading file contents",
    "FileWriterTool": "Write content to files",
    "DirectoryReadTool": "List directory contents",
    "DirectorySearchTool": "Search for files in directories",
    "PDFSearchTool": "Search PDF documents",
    "CSVSearchTool": "Search CSV files",
    "JSONSearchTool": "Search JSON files",
    "TXTSearchTool": "Search text files",
    "MDXSearchTool": "Search MDX/Markdown files",
    "XMLSearchTool": "Search XML files",
    "DOCXSearchTool": "Search DOCX documents",
    "CodeDocsSearchTool": "Search code documentation",
    "GithubSearchTool": "Search GitHub repositories",
    "SerperDevTool": "Google search via Serper API",
    "WebsiteSearchTool": "Search within a website",
    "YoutubeVideoSearchTool": "Search YouTube videos",
    "YoutubeChannelSearchTool": "Search YouTube channels",
    "EXASearchTool": "Exa AI search",
    "DallETool": "Generate images with DALL-E",
    "VisionTool": "Analyze images using vision models",
    "OCRTool": "Optical character recognition on images",
    "NL2SQLTool": "Natural language to SQL queries",
    "RagTool": "RAG-based document search",
    "ScrapeElementFromWebsiteTool": "Scrape specific elements from websites",
    "SeleniumScrapingTool": "Web scraping with Selenium",
    "BraveSearchTool": "Brave web search",
    "BraveNewsSearchTool": "Brave news search",
}


class LazyCrewAITool(BaseTool):
    """Proxy that defers crewai_tools instantiation until first use."""
    name: str = "Loading..."
    description: str = "Tool loading on first use"
    _tool_class_name: str = ""
    _inner: object = None

    def __init__(self, class_name: str, **kwargs):
        catalog_desc = CREWAI_TOOLS_CATALOG.get(class_name, "CrewAI tool")
        super().__init__(
            name=class_name,
            description=catalog_desc,
            _tool_class_name=class_name,
            **kwargs,
        )

    def _get_inner(self):
        if self._inner is None:
            try:
                import crewai_tools
                cls = getattr(crewai_tools, self._tool_class_name, None)
                if cls and inspect.isclass(cls):
                    # Some tools need a directory/file path argument
                    if self._tool_class_name in ("DirectoryReadTool", "DirectorySearchTool"):
                        try:
                            from config_loader import get_work_dir
                            self._inner = cls(directory=get_work_dir())
                        except Exception:
                            self._inner = cls(directory=".")
                    else:
                        self._inner = cls()
            except Exception:
                pass
        return self._inner

    def _run(self, query: str) -> str:
        inner = self._get_inner()
        if inner is None:
            return f"Tool {self._tool_class_name} failed to load."
        return inner._run(query)


# === Custom Skills (skills/ directory) ===

def load_skills_dir(skills_dir: str = None) -> dict:
    """Auto-discover BaseTool subclasses from skills/*.py files.

    Returns dict of {"skills:name": {"instance": tool, "description": ...}}
    """
    if skills_dir is None:
        try:
            from config_loader import get_skills_dir
            skills_dir = get_skills_dir()
        except Exception:
            return {}

    if not os.path.isdir(skills_dir):
        return {}

    skills = {}
    for filename in os.listdir(skills_dir):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        module_name = filename[:-3]
        filepath = os.path.join(skills_dir, filename)
        try:
            spec = importlib.util.spec_from_file_location(f"skills.{module_name}", filepath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for attr_name, obj in inspect.getmembers(mod, inspect.isclass):
                if issubclass(obj, BaseTool) and obj is not BaseTool:
                    instance = obj()
                    key = f"skills:{module_name}"
                    skills[key] = {
                        "instance": instance,
                        "description": getattr(instance, 'description', '')[:60],
                    }
        except Exception:
            continue
    return skills


# === Tool Registry (unified) ===

def build_tool_registry(skills_dir: str = None) -> dict:
    """Build the full tool registry from all three tiers.

    Returns dict of {tool_id: {"instance": BaseTool, "description": str, "tier": str}}
    """
    registry = {}

    # Tier 1: Built-in
    for tid, info in BUILTIN_TOOLS.items():
        registry[tid] = {**info, "tier": "built-in"}

    # Tier 2: CrewAI ecosystem (loaded on demand — only catalog here)
    # Actual instances are created when resolve_tools() is called

    # Tier 3: Custom skills
    custom = load_skills_dir(skills_dir)
    for tid, info in custom.items():
        registry[tid] = {**info, "tier": "custom"}

    return registry


def resolve_tools(tool_ids: list, skills_dir: str = None) -> list:
    """Given a list of tool IDs from config, return actual tool instances.
    CrewAI tools use lazy proxies — only instantiated on first use."""
    registry = build_tool_registry(skills_dir)
    tools = []
    for tid in tool_ids:
        if tid in registry:
            tools.append(registry[tid]["instance"])
        elif tid.startswith("crewai:"):
            class_name = tid.split(":", 1)[1]
            tools.append(LazyCrewAITool(class_name))
        # silently skip unknown tools
    return tools


def list_available_tools(skills_dir: str = None) -> dict:
    """List all available tools across all tiers for the Skills tab/wizard.

    Returns {tool_id: {"description": str, "tier": str, "installed": bool}}
    """
    registry = build_tool_registry(skills_dir)
    available = {}

    # Built-in + custom (already loaded)
    for tid, info in registry.items():
        available[tid] = {
            "description": info["description"],
            "tier": info["tier"],
            "installed": True,
        }

    # CrewAI catalog (not loaded until assigned)
    for class_name, desc in CREWAI_TOOLS_CATALOG.items():
        tid = f"crewai:{class_name}"
        if tid not in available:
            available[tid] = {
                "description": desc,
                "tier": "crewai",
                "installed": False,
            }

    return available


# === Config-driven agent/crew builder ===

def build_llm_from_preset(preset_name: str, presets: dict) -> LLM:
    """Build a CrewAI LLM from a preset name and presets dict."""
    preset = presets.get(preset_name)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_name}")
    api_key = os.environ.get(preset["api_key_env"]) if preset.get("api_key_env") else "lm-studio"
    return LLM(
        model=preset["model"],
        base_url=preset["base_url"],
        api_key=api_key,
        temperature=0.3,
        max_tokens=4096,
        **preset.get("extra", {}),
    )


def build_agents_from_config(project_config: dict, presets: dict) -> dict:
    """Build CrewAI agents and LLMs from project config.

    Returns {"agents": {id: Agent}, "llms": {id: LLM}}
    """
    from dotenv import load_dotenv
    load_dotenv()

    agents_cfg = project_config.get("agents", [])
    if len(agents_cfg) > project_config.get("max_agents", 10):
        raise ValueError(f"Too many agents: {len(agents_cfg)} (max {project_config.get('max_agents', 10)})")

    skills_dir = None
    try:
        from config_loader import get_skills_dir
        skills_dir = get_skills_dir()
    except Exception:
        pass

    agents = {}
    llms = {}

    for agent_cfg in agents_cfg:
        aid = agent_cfg["id"]
        preset_name = agent_cfg.get("preset", "grok")

        try:
            llm = build_llm_from_preset(preset_name, presets)
        except Exception as e:
            # Don't crash — agent will fail on use but others work
            llm = None

        llms[aid] = llm

        tool_instances = resolve_tools(agent_cfg.get("tools", []), skills_dir)

        agent = Agent(
            role=agent_cfg["role"],
            goal=agent_cfg["goal"],
            backstory=agent_cfg["backstory"],
            llm=llm,
            tools=tool_instances if tool_instances else [],
            verbose=True,
            allow_delegation=agent_cfg.get("allow_delegation", False),
        )
        agents[aid] = agent

    return {"agents": agents, "llms": llms}


def build_crew_from_config(project_config: dict, presets: dict, mission: str = None) -> Crew:
    """Build a full Crew from project config.

    If mission is provided, generates research/compile/review tasks for it.
    If not, uses default_tasks from config.
    """
    components = build_agents_from_config(project_config, presets)
    agents = components["agents"]
    agents_cfg = project_config.get("agents", [])

    if mission:
        # Auto-generate tasks from mission
        task_list = _generate_mission_tasks(mission, agents, agents_cfg)
    else:
        # Use default_tasks from config
        task_list = _build_default_tasks(project_config, agents)

    crew = Crew(
        agents=list(agents.values()),
        tasks=task_list,
        verbose=True,
    )
    return crew, components


def _get_out_dir():
    try:
        from config_loader import get_output_dir
        return get_output_dir()
    except Exception:
        d = os.path.join(os.path.dirname(__file__), "output")
        os.makedirs(d, exist_ok=True)
        return d


def _out(filename: str) -> str:
    """Return output path. CrewAI prepends CWD to output_file, so if our
    output dir is absolute, we need to make it relative to CWD."""
    out_dir = _get_out_dir()
    full_path = os.path.join(out_dir, filename)
    # Make relative to CWD so CrewAI doesn't double the path
    try:
        return os.path.relpath(full_path)
    except ValueError:
        return full_path


def _generate_mission_tasks(mission: str, agents: dict, agents_cfg: list) -> list:
    """Generate a standard task pipeline for an ad-hoc mission."""
    agent_ids = list(agents.keys())
    tasks = []
    ts = __import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')

    # If multiple agents, create parallel research + compile + review
    if len(agent_ids) >= 3:
        # Research agents (all except first and second)
        research_tasks = []
        for aid in agent_ids[2:]:
            cfg = next((a for a in agents_cfg if a["id"] == aid), {})
            t = Task(
                description=f"Research the following from your perspective as {cfg.get('role', aid)}:\n\n{mission}\n\nProvide detailed findings.",
                expected_output="A detailed report in markdown.",
                agent=agents[aid],
            )
            research_tasks.append(t)
            tasks.append(t)

        # Compile (second agent)
        compile_agent_id = agent_ids[1]
        compile_task = Task(
            description="Compile all research into a single executive report with summary, findings, recommendations, and next steps.",
            expected_output="A comprehensive report in markdown.",
            agent=agents[compile_agent_id],
            output_file=_out(f"report_{ts}.md"),
            context=research_tasks,
        )
        tasks.append(compile_task)

        # Review (first agent)
        review_task = Task(
            description="Review the report. Evaluate recommendations, identify gaps, and provide your final decision.",
            expected_output="Review with final decision and next steps.",
            agent=agents[agent_ids[0]],
            context=[compile_task],
            output_file=_out(f"decision_{ts}.md"),
        )
        tasks.append(review_task)
    elif len(agent_ids) == 2:
        # Two agents: research + review
        t1 = Task(
            description=f"Research thoroughly:\n\n{mission}\n\nProvide detailed findings.",
            expected_output="A detailed report in markdown.",
            agent=agents[agent_ids[1]],
            output_file=_out(f"report_{ts}.md"),
        )
        t2 = Task(
            description="Review the findings and provide your final decision with next steps.",
            expected_output="Review with decision.",
            agent=agents[agent_ids[0]],
            context=[t1],
            output_file=_out(f"decision_{ts}.md"),
        )
        tasks = [t1, t2]
    else:
        # Single agent: just do it
        t = Task(
            description=f"Complete the following task:\n\n{mission}\n\nProvide a thorough response.",
            expected_output="A detailed response in markdown.",
            agent=agents[agent_ids[0]],
            output_file=_out(f"result_{ts}.md"),
        )
        tasks = [t]

    return tasks


def _build_default_tasks(project_config: dict, agents: dict) -> list:
    """Build CrewAI Task objects from default_tasks in config."""
    task_configs = project_config.get("default_tasks", [])
    if not task_configs:
        return []

    # Build tasks in order, resolving context references
    tasks_by_id = {}
    task_list = []

    for tc in task_configs:
        agent = agents.get(tc.get("agent_id"))
        if not agent:
            continue

        context = [tasks_by_id[cid] for cid in tc.get("context_task_ids", []) if cid in tasks_by_id]

        task = Task(
            description=tc["description"],
            expected_output=tc.get("expected_output", "A detailed response."),
            agent=agent,
            output_file=tc.get("output_file"),
            context=context if context else None,
        )
        tasks_by_id[tc["id"]] = task
        task_list.append(task)

    return task_list


if __name__ == "__main__":
    from config_loader import config_exists, load_project_config, get_output_dir
    from model_wizard import load_presets

    if not config_exists():
        print("No project_config.json found. Run 'crewtui setup' first.")
        raise SystemExit(1)

    from dotenv import load_dotenv
    load_dotenv()

    config = load_project_config()
    presets = load_presets()
    os.makedirs(get_output_dir(), exist_ok=True)

    crew, components = build_crew_from_config(config, presets)
    result = crew.kickoff()
    print("\n" + "=" * 60)
    print("CREW EXECUTION COMPLETE")
    print("=" * 60)
    print(result)
