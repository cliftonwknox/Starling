"""CrewTUI Model Manager — Add, remove, and list model presets."""

import json
import os
import sys

CONFIG_DIR = os.path.dirname(__file__)
PRESETS_FILE = os.path.join(CONFIG_DIR, "model_presets.json")

# Built-in presets that ship with the app
BUILTIN_PRESETS = {
    "haiku": {
        "label": "Claude Haiku 4.5",
        "model": "openrouter/anthropic/claude-haiku-4.5",
        "base_url": "https://openrouter.ai/api/v1",
        "api_format": "openai",
        "api_key_env": "OPENROUTER_API_KEY",
        "provider": "OpenRouter",
        "extra": {},
    },
    "grok": {
        "label": "Grok 4.1 Fast",
        "model": "openai/grok-4-1-fast",
        "base_url": "https://api.x.ai/v1",
        "api_format": "openai",
        "api_key_env": "XAI_API_KEY",
        "provider": "xAI",
        "extra": {"additional_drop_params": ["stop"]},
    },
    "grok-reasoning": {
        "label": "Grok 4.1 Reasoning",
        "model": "openai/grok-4-1-fast-reasoning",
        "base_url": "https://api.x.ai/v1",
        "api_format": "openai",
        "api_key_env": "XAI_API_KEY",
        "provider": "xAI",
        "extra": {"additional_drop_params": ["stop"]},
    },
    "grok-420": {
        "label": "Grok 4.20 Reasoning",
        "model": "openai/grok-4.20-0309-reasoning",
        "base_url": "https://api.x.ai/v1",
        "api_format": "openai",
        "api_key_env": "XAI_API_KEY",
        "provider": "xAI",
        "extra": {"additional_drop_params": ["stop"]},
    },
    "kimi": {
        "label": "Kimi K2.5",
        "model": "openai/moonshotai/kimi-k2.5",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_format": "openai",
        "api_key_env": "NVIDIA_API_KEY",
        "provider": "NVIDIA",
        "extra": {},
    },
    "qwen": {
        "label": "Qwen 3.5 (local)",
        "model": "openai/qwen_qwen3.5-35b-a3b",
        "base_url": "http://127.0.0.1:1234/v1",
        "api_format": "openai",
        "api_key_env": None,
        "provider": "LM Studio",
        "extra": {},
    },
    "qwen-plus": {
        "label": "Qwen 3.6 Plus",
        "model": "openai/qwen3.6-plus-2026-04-02",
        "base_url": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        "api_format": "openai",
        "api_key_env": "ALIBABA_API_KEY",
        "provider": "Alibaba",
        "extra": {},
    },
    "gemini": {
        "label": "Gemini 3 Flash",
        "model": "openai/gemini-3-flash-preview",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_format": "openai",
        "api_key_env": "GOOGLE_API_KEY",
        "provider": "Google",
        "extra": {},
    },
}


def load_presets():
    """Load custom presets and merge with builtins."""
    presets = {k: {**v} for k, v in BUILTIN_PRESETS.items()}
    if os.path.exists(PRESETS_FILE):
        with open(PRESETS_FILE) as f:
            custom = json.load(f)
        presets.update(custom)
    return presets


def save_custom_presets(presets):
    """Save only non-builtin presets."""
    custom = {k: v for k, v in presets.items() if k not in BUILTIN_PRESETS}
    with open(PRESETS_FILE, "w") as f:
        json.dump(custom, f, indent=2)


def load_env():
    """Load .env file as dict."""
    env_file = os.path.join(CONFIG_DIR, ".env")
    env = {}
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env[key.strip()] = val.strip()
    return env


def save_env(env):
    """Write env dict back to .env file."""
    env_file = os.path.join(CONFIG_DIR, ".env")
    with open(env_file, "w") as f:
        for key, val in env.items():
            f.write(f"{key}={val}\n")
    os.chmod(env_file, 0o600)


def prompt(text, default=""):
    """Prompt with optional default."""
    if default:
        result = input(f"  {text} [{default}]: ").strip()
        return result if result else default
    return input(f"  {text}: ").strip()


def prompt_choice(text, options, default=None):
    """Prompt with numbered choices."""
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
        print("  Invalid choice, try again.")


def cmd_list():
    """List all model presets."""
    presets = load_presets()
    env = load_env()

    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║              CrewTUI — Model Presets                     ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    for key in sorted(presets.keys()):
        p = presets[key]
        builtin = " (built-in)" if key in BUILTIN_PRESETS else " (custom)"
        api_key_env = p.get("api_key_env")
        if api_key_env:
            has_key = "✓" if env.get(api_key_env) or os.environ.get(api_key_env) else "✗ missing"
        else:
            has_key = "✓ none needed"

        print(f"  {key:20s} {p['label']}")
        print(f"  {'':20s} Provider: {p.get('provider', '?')}  |  Format: {p.get('api_format', 'openai')}  |  Key: {has_key}{builtin}")
        print(f"  {'':20s} Model: {p['model']}")
        print(f"  {'':20s} URL: {p['base_url']}")
        print()


def cmd_add():
    """Add a new model preset via wizard."""
    presets = load_presets()
    env = load_env()

    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║              Add New Model Preset                           ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    # Preset name
    while True:
        name = prompt("Preset name (short, no spaces, e.g. 'deepseek-r1')").lower().replace(" ", "-")
        if not name:
            print("  Name is required.")
            continue
        if name in presets:
            print(f"  '{name}' already exists. Pick another name or use 'edit'.")
            continue
        break

    # Label
    label = prompt("Display label (e.g. 'DeepSeek R1')", name.title())

    # Provider name
    provider = prompt("Provider name (e.g. 'DeepSeek', 'Together AI', 'Local')", "Custom")

    # API format
    api_format = prompt_choice(
        "API compatibility format?",
        ["openai", "anthropic"],
        default="openai",
    )

    # Base URL
    print("\n  Common base URLs:")
    print("    OpenRouter:  https://openrouter.ai/api/v1")
    print("    xAI:         https://api.x.ai/v1")
    print("    NVIDIA:      https://integrate.api.nvidia.com/v1")
    print("    Together:    https://api.together.xyz/v1")
    print("    Groq:        https://api.groq.com/openai/v1")
    print("    Local:       http://127.0.0.1:1234/v1")
    base_url = prompt("Base URL")
    if not base_url:
        print("  Base URL is required. Aborting.")
        return

    # Model ID
    print(f"\n  Model ID as the provider expects it.")
    print(f"  For LiteLLM prefix with 'openai/' for OpenAI-compatible endpoints.")
    print(f"  Examples: openai/deepseek-r1, openrouter/deepseek/deepseek-r1")
    model_id = prompt("Model ID")
    if not model_id:
        print("  Model ID is required. Aborting.")
        return

    # API key
    print(f"\n  API key setup:")
    key_choice = prompt_choice(
        "How is auth handled?",
        ["Environment variable (recommended)", "No key needed (local models)", "Enter key now"],
    )

    api_key_env = None
    if key_choice == "Environment variable (recommended)":
        api_key_env = prompt("Env var name (e.g. DEEPSEEK_API_KEY)").upper()
        if api_key_env:
            existing = env.get(api_key_env) or os.environ.get(api_key_env)
            if existing:
                print(f"  ✓ {api_key_env} already set.")
            else:
                key_val = prompt(f"Enter the API key for {api_key_env} (saved to .env)")
                if key_val:
                    env[api_key_env] = key_val
                    save_env(env)
                    print(f"  ✓ Saved to .env")
    elif key_choice == "Enter key now":
        api_key_env = prompt("Env var name to store it as").upper()
        if api_key_env:
            key_val = prompt(f"API key")
            if key_val:
                env[api_key_env] = key_val
                save_env(env)
                print(f"  ✓ Saved as {api_key_env} in .env")

    # Drop params (for providers that reject certain params)
    drop_stop = prompt_choice(
        "Does this provider reject the 'stop' parameter? (xAI does)",
        ["No", "Yes"],
        default="No",
    )
    extra = {}
    if drop_stop == "Yes":
        extra["additional_drop_params"] = ["stop"]

    # Build preset
    preset = {
        "label": label,
        "model": model_id,
        "base_url": base_url,
        "api_format": api_format,
        "api_key_env": api_key_env,
        "provider": provider,
        "extra": extra,
    }

    # Confirm
    print(f"\n  ── Summary ──")
    print(f"  Name:     {name}")
    print(f"  Label:    {label}")
    print(f"  Provider: {provider}")
    print(f"  Format:   {api_format}")
    print(f"  URL:      {base_url}")
    print(f"  Model:    {model_id}")
    print(f"  Key env:  {api_key_env or 'none'}")
    if extra:
        print(f"  Extra:    {extra}")

    confirm = prompt("\n  Save this preset? (y/n)", "y")
    if confirm.lower() != "y":
        print("  Cancelled.")
        return

    presets[name] = preset
    save_custom_presets(presets)
    print(f"\n  ✓ Preset '{name}' saved! Use '/config <agent> {name}' in the TUI.")


def cmd_remove():
    """Remove a custom preset."""
    presets = load_presets()
    custom_keys = [k for k in presets if k not in BUILTIN_PRESETS]

    if not custom_keys:
        print("\n  No custom presets to remove. Built-in presets cannot be removed.")
        return

    print("\n  Custom presets:")
    for i, key in enumerate(custom_keys, 1):
        p = presets[key]
        print(f"    {i}) {key} — {p['label']} ({p.get('provider', '?')})")

    choice = input(f"\n  Remove which? [1-{len(custom_keys)}] or name: ").strip()

    target = None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(custom_keys):
            target = custom_keys[idx]
    except ValueError:
        if choice in custom_keys:
            target = choice

    if not target:
        print("  Invalid choice.")
        return

    confirm = prompt(f"Remove '{target}'? (y/n)", "n")
    if confirm.lower() != "y":
        print("  Cancelled.")
        return

    del presets[target]
    save_custom_presets(presets)
    print(f"  ✓ Removed '{target}'.")


def cmd_test():
    """Quick test a preset by sending a simple prompt."""
    presets = load_presets()
    keys = sorted(presets.keys())

    print("\n  Available presets:")
    for i, key in enumerate(keys, 1):
        p = presets[key]
        print(f"    {i}) {key} — {p['label']}")

    choice = input(f"\n  Test which? [1-{len(keys)}] or name: ").strip()
    target = None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(keys):
            target = keys[idx]
    except ValueError:
        if choice in keys:
            target = choice

    if not target:
        print("  Invalid choice.")
        return

    p = presets[target]
    print(f"\n  Testing {p['label']} at {p['base_url']}...")

    from dotenv import load_dotenv
    load_dotenv(os.path.join(CONFIG_DIR, ".env"))

    try:
        import litellm
        litellm.drop_params = True

        api_key = None
        if p.get("api_key_env"):
            api_key = os.environ.get(p["api_key_env"])
            if not api_key:
                print(f"  ✗ {p['api_key_env']} not set. Add it to .env first.")
                return
        else:
            api_key = "lm-studio"

        extra = p.get("extra", {})
        response = litellm.completion(
            model=p["model"],
            messages=[{"role": "user", "content": "Say hello in one sentence."}],
            api_base=p["base_url"],
            api_key=api_key,
            max_tokens=100,
            **extra,
        )
        text = response.choices[0].message.content
        print(f"  ✓ Response: {text[:200]}")
    except Exception as e:
        print(f"  ✗ Error: {e}")


def main():
    if len(sys.argv) < 2:
        print("\nCrewTUI Model Manager")
        print("  Usage: python model_wizard.py <command>")
        print()
        print("  Commands:")
        print("    list     — Show all model presets")
        print("    add      — Add a new model (wizard)")
        print("    remove   — Remove a custom model")
        print("    test     — Test a model with a quick prompt")
        print()
        return

    cmd = sys.argv[1].lower()
    if cmd == "list":
        cmd_list()
    elif cmd == "add":
        cmd_add()
    elif cmd == "remove":
        cmd_remove()
    elif cmd == "test":
        cmd_test()
    else:
        print(f"  Unknown command: {cmd}. Use list, add, remove, or test.")


if __name__ == "__main__":
    main()
