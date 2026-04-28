from __future__ import annotations

import configparser
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


FIELD_CODE_PATTERN = re.compile(r"%[fFuUdDnNickvm]")


@dataclass(frozen=True)
class DesktopEntry:
    path: Path
    name: str
    exec_command: tuple[str, ...]
    startup_wm_class: str = ""
    no_display: bool = False
    terminal: bool = False


def load_desktop_entry(path: Path | str) -> DesktopEntry:
    target = Path(path)
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(target.read_text(encoding="utf-8"))
    if "Desktop Entry" not in parser:
        raise ValueError(f"desktop file missing [Desktop Entry]: {target}")

    section = parser["Desktop Entry"]
    entry_type = section.get("Type", "").strip()
    if entry_type and entry_type != "Application":
        raise ValueError(f"unsupported desktop entry type in {target}: {entry_type}")

    exec_line = section.get("Exec", "").strip()
    if not exec_line:
        raise ValueError(f"desktop entry missing Exec in {target}")

    return DesktopEntry(
        path=target,
        name=section.get("Name", "").strip(),
        exec_command=split_desktop_exec(exec_line),
        startup_wm_class=section.get("StartupWMClass", "").strip(),
        no_display=_truthy(section.get("NoDisplay", "")),
        terminal=_truthy(section.get("Terminal", "")),
    )


def split_desktop_exec(exec_line: str) -> tuple[str, ...]:
    normalized = FIELD_CODE_PATTERN.sub("", exec_line).strip()
    if not normalized:
        raise ValueError("desktop Exec command becomes empty after removing field codes")
    try:
        tokens = tuple(token for token in shlex.split(normalized) if token)
    except ValueError as exc:
        raise ValueError(f"failed to parse desktop Exec command: {exec_line}") from exc
    if not tokens:
        raise ValueError("desktop Exec command becomes empty after parsing")
    return tokens


def choose_desktop_entry(
    entries: tuple[DesktopEntry, ...],
    *,
    preferred: str = "",
) -> DesktopEntry:
    if not entries:
        raise ValueError("no desktop entries available")

    normalized_preference = preferred.strip()
    if normalized_preference:
        for entry in entries:
            if (
                entry.path.name == normalized_preference
                or str(entry.path) == normalized_preference
                or normalized_preference in entry.path.name
            ):
                return entry
        raise ValueError(f"preferred desktop entry not found: {preferred}")

    visible_entries = tuple(entry for entry in entries if not entry.no_display)
    if visible_entries:
        non_terminal_entries = tuple(entry for entry in visible_entries if not entry.terminal)
        if non_terminal_entries:
            return non_terminal_entries[0]
        return visible_entries[0]
    return entries[0]


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
