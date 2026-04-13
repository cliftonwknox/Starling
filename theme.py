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
    # Alternative screen buffer (preserves user's shell, swaps to blank canvas)
    "alt_screen_on": "\033[?1049h",
    "alt_screen_off": "\033[?1049l",
    # Default colors — dark bg, light fg. 256-color codes: 234 = #1c1c1c, 255 = #eeeeee
    "default_dark_bg": "\033[48;5;234m",
    "default_light_fg": "\033[38;5;255m",
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
    When the wizard has entered dark-screen mode, re-paints the dark bg
    on every clear so lines below our content remain dark, not terminal-default.
    """
    if not _supports_ansi():
        return
    # Clear + home, then re-establish dark bg + light fg defaults.
    # The bg color persists on newly-written cells but empty rows need
    # explicit painting — do a second pass with spaces to fill visible rows.
    if _dark_screen_active:
        # Paint the full screen with dark bg: clear, home, then fill rows
        print(_ANSI["clear"], end="", flush=True)
        _paint_dark_background()
        print(f"{_ANSI['clear']}{_ANSI['default_dark_bg']}{_ANSI['default_light_fg']}", end="", flush=True)
    else:
        print(_ANSI["clear"], end="", flush=True)


# Track whether we've entered dark-screen mode so downstream helpers
# (clear_screen, banner, step_header) can keep repainting the bg.
_dark_screen_active = False


def _terminal_size():
    """Return (cols, rows) from the terminal, with safe defaults."""
    try:
        import shutil
        sz = shutil.get_terminal_size(fallback=(80, 24))
        return sz.columns, sz.lines
    except Exception:
        return 80, 24


def _paint_dark_background():
    """Fill the visible terminal with dark background color.

    Works by writing a space to every cell of every visible row. After this,
    subsequent content printed without explicit bg color will render on top
    of the dark fill — as long as we keep the default_dark_bg SGR active.
    """
    cols, rows = _terminal_size()
    bg = _ANSI["default_dark_bg"]
    fg = _ANSI["default_light_fg"]
    # Move to top-left, paint each row with spaces on dark bg
    out = []
    out.append("\033[H")  # cursor home
    for r in range(rows):
        out.append(f"\033[{r + 1};1H")  # row N col 1
        out.append(f"{bg}{fg}{' ' * cols}")
    out.append("\033[H")  # back to home
    out.append(f"{bg}{fg}")  # leave default colors active for content
    print("".join(out), end="", flush=True)


def request_terminal_size(cols: int = 120, rows: int = 40):
    """Ask the terminal emulator to resize itself (CSI 8 t).

    Widely supported on xterm, gnome-terminal, some konsole versions.
    IGNORED by iTerm2, Apple Terminal, Alacritty, kitty (they block resize
    requests for security). If the request is blocked, we fall through silently;
    the caller should check the actual size afterwards with _terminal_size().

    Args:
        cols: target columns (width)
        rows: target rows (height)
    """
    if not _supports_ansi():
        return
    print(f"\033[8;{rows};{cols}t", end="", flush=True)


def check_terminal_size(min_cols: int = 100, min_rows: int = 28) -> bool:
    """Check if terminal meets minimum size recommendation. Returns True if OK.

    Prints a user-visible warning if the terminal is smaller than recommended.
    Wizard still works at smaller sizes — this is just a nudge.
    """
    cols, rows = _terminal_size()
    if cols >= min_cols and rows >= min_rows:
        return True
    # Print a visible but non-blocking warning
    warn_msg = (
        f"Your terminal is {cols}x{rows}. "
        f"Recommended: {min_cols}x{min_rows} or larger for best layout."
    )
    print()
    print(color(f"  ⚠ {warn_msg}", "warning"))
    print(color(f"    Try resizing your terminal window for a cleaner experience.", "muted"))
    print()
    return False


def enter_dark_screen():
    """Enter alt screen buffer + paint dark background.

    Safe to call multiple times. Pairs with exit_dark_screen() — use try/finally
    to ensure the alt buffer is released even if the wizard crashes.
    Also requests a comfortable terminal size (120x40) — terminals that block
    the resize request fall through silently; we warn on small terminals.
    """
    global _dark_screen_active
    if not _supports_ansi():
        return
    print(_ANSI["alt_screen_on"], end="", flush=True)
    # Request a larger size — ignored by terminals that block this for security
    request_terminal_size(120, 40)
    _dark_screen_active = True
    _paint_dark_background()


def exit_dark_screen():
    """Restore original screen buffer and user's terminal colors."""
    global _dark_screen_active
    if not _supports_ansi():
        return
    # Reset SGR and leave alt buffer — restores the user's shell with its
    # original terminal theme and scrollback intact.
    print(f"{_ANSI['reset']}{_ANSI['alt_screen_off']}", end="", flush=True)
    _dark_screen_active = False


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
