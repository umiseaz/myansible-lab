import os
import re

_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


def resolve(value):
    """Resolve a single "${VAR_NAME}" placeholder string from the environment."""
    if isinstance(value, str):
        m = _PATTERN.match(value)
        if m:
            var = m.group(1)
            try:
                return os.environ[var]
            except KeyError:
                raise RuntimeError(
                    f"Missing required env var {var} — copy .env.example to .env and fill in real values"
                )
    return value


def resolve_deep(obj):
    """Recursively resolve "${VAR_NAME}" placeholders anywhere in a dict/list."""
    if isinstance(obj, dict):
        return {k: resolve_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_deep(v) for v in obj]
    return resolve(obj)
