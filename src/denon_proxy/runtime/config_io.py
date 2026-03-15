"""Config file I/O and user-facing error reporting."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised via tests that skip when yaml missing
    yaml = None

from pydantic import ValidationError

from denon_proxy.runtime.config import Config


def _load_config_dict_from_file(config_path: Path | None) -> dict[str, Any]:
    """
    Load raw config data (dict) from YAML file.

    Does not apply defaults or environment overrides so it can be tested
    separately from I/O.
    """
    if yaml is None:
        raise ImportError(
            "PyYAML is a dependency of denon-proxy but is not available. "
            "Reinstall denon-proxy: pip install --force-reinstall denon-proxy"
        )

    if config_path:
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        path = config_path
    else:
        # Default: config.yaml in current working directory (project root when run from there)
        path = Path.cwd() / "config.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"Config not found: {path}\n"
                "Copy config.sample.yaml to config.yaml in the project root and edit as needed."
            )

    with path.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a mapping, got {type(data).__name__}")
    return data


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration from YAML file with environment overrides."""
    raw = _load_config_dict_from_file(config_path)
    # Apply environment overrides during model construction
    return Config.load_from_dict(raw)


def load_config_and_report_errors(config_path: Path | None) -> Config | None:
    """
    Helper for CLI/server entrypoints: load config and print user-facing errors.

    Returns Config on success, or None on error (after printing to stderr).
    """
    try:
        return load_config(config_path)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
    except ImportError as e:
        # Handle missing declared dependency (e.g. PyYAML) during config load.
        if "yaml" in str(e).lower():
            print(
                "PyYAML is missing. Reinstall denon-proxy: pip install --force-reinstall denon-proxy",
                file=sys.stderr,
            )
        else:
            print(f"Import error while loading config: {e}", file=sys.stderr)
    except ValidationError as e:
        print("Config validation failed:", file=sys.stderr)
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            msg = err.get("msg", "")
            print(f"  {loc}: {msg}", file=sys.stderr)
    except Exception as e:
        if yaml is not None and isinstance(e, yaml.YAMLError):
            print("Invalid YAML in config file:", str(e), file=sys.stderr)
        else:
            raise
    return None
