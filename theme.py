"""Starling Theme — Centralized color palette and terminal UI helpers.

Inspired by the European starling: glossy iridescent black with purple/green
sheen, yellow speckles and beak. The palette aims for high contrast against
both dark and light terminal backgrounds.

All modules (wizard, TUI, Telegram output, future web UI) import from here.
Change once, update everywhere.
"""

import os
import sys

# === Semantic color palette ===
#
# Each token has both a Rich name (for Textual widgets) and an ANSI name (for
# raw escape codes in the CLI wizard). Co-located so they can't drift.
#
# IMPORTANT: CLI code MUST go through color() / _SEMANTIC_ANSI — do not use
# the Rich names below as keys into _ANSI directly (e.g. "grey62" has no ANSI
# mapping and would render as plain text).
#
# Tokens named by purpose, not hue, so future themes can be swapped in.

_PALETTE = {
    # token:      (rich_name,     ansi_name)        # purpose
    "primary":    ("magenta",     "bright_magenta"),   # starling purple — headings
    "accent":     ("green",       "bright_green"),     # wing sheen — secondary highlights
    "highlight":  ("yellow",      "bright_yellow"),    # beak/speckles — prompts, callouts
    "muted":      ("grey62",      "grey"),             # hints, defaults, secondary text
    "success":    ("bright_green", "bright_green"),    # confirmations
    "error":      ("bright_red",  "bright_red"),       # blocks, destructive warnings
    "warning":    ("yellow",      "yellow"),           # non-fatal cautions
    "info":       ("cyan",        "bright_cyan"),      # neutral info
    "text":       ("white",       "bright_white"),     # body text
    "dim":        ("grey39",      "grey"),             # disabled, skipped
}

# Rich color names — for Textual widgets (e.g. style="magenta")
PRIMARY = _PALETTE["primary"][0]
ACCENT = _PALETTE["accent"][0]
HIGHLIGHT = _PALETTE["highlight"][0]
MUTED = _PALETTE["muted"][0]
SUCCESS = _PALETTE["success"][0]
ERROR = _PALETTE["error"][0]
WARNING = _PALETTE["warning"][0]
INFO = _PALETTE["info"][0]
TEXT = _PALETTE["text"][0]
DIM = _PALETTE["dim"][0]

# === Raw ANSI escape codes (for the CLI wizard, which doesn't use Rich) ===

_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    # Foreground
    "black": "\033[30m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "bright_red": "\033[91m",
    "bright_green": "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_blue": "\033[94m",
    "bright_magenta": "\033[95m",
    "bright_cyan": "\033[96m",
    "bright_white": "\033[97m",
    "grey": "\033[90m",
    # Screen control
    "clear": "\033[2J\033[H",
    "clear_line": "\033[2K\r",
}

# Map semantic tokens to ANSI names for CLI usage (derived from _PALETTE)
_SEMANTIC_ANSI = {token: names[1] for token, names in _PALETTE.items()}


def _supports_ansi() -> bool:
    """Check if the terminal supports ANSI escape codes."""
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    return True


def color(text: str, token: str, bold: bool = False) -> str:
    """Wrap text in ANSI color codes using a semantic token.

    Args:
        text: The text to colorize.
        token: Semantic token name (primary, accent, highlight, muted, success,
            error, warning, info, text, dim).
        bold: Whether to apply bold formatting.

    Returns:
        Colorized string, or plain text if the terminal doesn't support ANSI.
    """
    if not _supports_ansi():
        return text
    ansi_name = _SEMANTIC_ANSI.get(token, "white")
    code = _ANSI.get(ansi_name, "")
    bold_code = _ANSI["bold"] if bold else ""
    return f"{bold_code}{code}{text}{_ANSI['reset']}"


def clear_screen():
    """Clear the terminal and move cursor to top-left.

    No-op when stdout is not a TTY or NO_COLOR is set — avoids polluting
    piped output and CI logs with control sequences or bulk newlines.
    """
    if _supports_ansi():
        print(_ANSI["clear"], end="", flush=True)


def step_header(step: int, total: int, title: str):
    """Render a step header at the top of a wizard stage.

    Example output:
        ─── Step 3 of 5 ─────────────────────────────
          Pick a template
        ─────────────────────────────────────────────
    """
    width = 60
    step_str = color(f"Step {step} of {total}", "muted")
    print(f"\n{color('─── ', 'primary')}{step_str}{color(' ' + '─' * (width - len(f'Step {step} of {total}') - 5), 'primary')}")
    print(f"  {color(title, 'primary', bold=True)}")
    print(color("─" * width, 'primary'))
    print()


def banner(title: str):
    """Render a prominent banner (used at start of wizard sections)."""
    width = 60
    print()
    print(color("═" * width, "primary"))
    print(f"  {color(title, 'primary', bold=True)}")
    print(color("═" * width, "primary"))
    print()


def prompt_text(label: str, default: str = "", hint: str = "") -> str:
    """Return a formatted prompt string (does not call input()).

    Caller appends this to input() to get the styled prompt.
    """
    if hint:
        hint_str = color(f"  ({hint})", "muted")
    else:
        hint_str = ""
    if default:
        default_str = color(f" [{default}]", "muted")
    else:
        default_str = ""
    label_str = color(label, "highlight")
    return f"  {label_str}{default_str}:{hint_str} "


def success(text: str):
    """Print a success message."""
    print(f"  {color('✓', 'success')} {color(text, 'success')}")


def error(text: str):
    """Print an error message."""
    print(f"  {color('✗', 'error')} {color(text, 'error')}")


def warn(text: str):
    """Print a warning message."""
    print(f"  {color('!', 'warning')} {color(text, 'warning')}")


def info(text: str):
    """Print a neutral info message."""
    print(f"  {color('ℹ', 'info')} {text}")


def muted(text: str):
    """Print dimmed/hint text."""
    print(f"  {color(text, 'muted')}")
