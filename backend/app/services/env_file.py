import re
from pathlib import Path

# Matches pydantic-settings' own resolution of `env_file=".env"` in config.py - relative to the
# process's current working directory (wherever uvicorn was actually launched from), not this
# file's location. Settings written here have to land in the exact same file Settings() reads,
# or a GUI edit would silently do nothing.
ENV_PATH = Path(".env")

_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def read_env_file() -> dict[str, str]:
    """Every KEY=value line currently in the .env file, ignoring blanks/comments. Values are
    exactly as written - no quote-stripping - since write_env_updates below never adds quotes
    either, keeping round-trips lossless for the plain, unquoted values this app itself writes."""
    if not ENV_PATH.exists():
        return {}
    values: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _LINE_RE.match(stripped)
        if m:
            values[m.group(1)] = m.group(2)
    return values


def write_env_updates(updates: dict[str, str | None]) -> None:
    """Applies `updates` (env var name -> new value, or None to drop the line entirely) onto the
    .env file, preserving every other line - order, comments, and any key not mentioned in
    `updates` - exactly as they were. A key in `updates` not already present as a line is
    appended. Written via a temp-file-then-rename so a crash mid-write can never leave a
    truncated/corrupt .env behind."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        m = _LINE_RE.match(stripped) if stripped and not stripped.startswith("#") else None
        if m and m.group(1) in updates:
            key = m.group(1)
            seen.add(key)
            value = updates[key]
            if value is None:
                continue  # drop the line - falls back to the process default/env var
            new_lines.append(f"{key}={value}")
        else:
            new_lines.append(line)
    for key, value in updates.items():
        if key not in seen and value is not None:
            new_lines.append(f"{key}={value}")

    tmp_path = ENV_PATH.with_suffix(ENV_PATH.suffix + ".tmp")
    tmp_path.write_text("\n".join(new_lines) + "\n" if new_lines else "")
    tmp_path.replace(ENV_PATH)
