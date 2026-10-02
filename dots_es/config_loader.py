import os
from pathlib import Path
from typing import Any

import yaml

# Single, fixed location for the YAML configuration, relative to the working directory
# Deliberately not an option: one deployment, one config directory.
CONFIG_DIR = Path("config")

# ============================================================
# LOAD YAML CONFIG
# ============================================================

def replace_none_with_empty_string(d: Any) -> Any:
    """Recursively replace all None values in a dict/list with empty strings.

    :param d: Dictionary, list, or value to process
    :type d: any
    :return: Dictionary, list, or value with None replaced by ''
    :rtype: any
    """
    if isinstance(d, dict):
        return {k: replace_none_with_empty_string(v) for k, v in d.items()}
    elif isinstance(d, list):
        return [replace_none_with_empty_string(v) for v in d]
    elif d is None:
        return ""
    else:
        return d


def resolve_env_vars(d: Any) -> Any:
    """Recursively replace environment variable placeholders (e.g., ${VAR}) in strings.

    :param d: Dictionary, list, or value to process
    :type d: any
    :return: Dictionary, list, or value with environment variables expanded
    :rtype: any
    """
    if isinstance(d, dict):
        return {k: resolve_env_vars(v) for k, v in d.items()}
    elif isinstance(d, list):
        return [resolve_env_vars(v) for v in d]
    elif isinstance(d, str):
        return os.path.expandvars(d)
    else:
        return d


def es_basic_auth():
    """Return the Elasticsearch credentials, read from the environment.

    Credentials are deliberately kept out of ELASTICSEARCH_URL: as long as they
    are not part of the URL, no print, traceback or CI log can expose them.

    :return: (user, password) or None when ES_PASSWORD is not set
    :rtype: tuple | None
    """
    password = os.environ.get("ES_PASSWORD")
    if not password:
        return None
    return os.environ.get("ES_USER", "elastic"), password


def load_config(alias: str = "staging") -> dict:
    """Load a YAML configuration file, replace None with empty strings, resolve environment variables,
    and flatten 'source' + 'config' sections for compatibility with App.

    The files live in the ``config`` directory of the deployment, read from the
    working directory at runtime rather than from the installed package: a plain
    ``pip install .`` is enough, and editing a file takes effect on the next run.

    :param alias: Configuration alias corresponding to config/{alias}.yml
    :type alias: str
    :return: Flattened configuration dictionary
    :rtype: dict
    :raises FileNotFoundError: if the YAML config file does not exist
    """
    path = CONFIG_DIR / f"{alias}.yml"

    if not path.is_file():
        raise FileNotFoundError(
            f"Config file not found: {path.resolve()} "
            f"(run the command from the directory holding {CONFIG_DIR}/)"
        )

    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Replace None values with empty strings
    config = replace_none_with_empty_string(config)
    # Resolve environment variables
    config = resolve_env_vars(config)

    # Flatten 'source' and 'config' sections for App compatibility
    flat_config = {}
    flat_config.update(config.get("source", {}))
    flat_config.update(config.get("config", {}))

    # Resources live in their own index; older YAML files do not name it
    flat_config.setdefault("RESOURCE_INDEX", "dots_resources")

    # Ensure ADDITIONAL_EXCLUDED_COLLECTIONS exists and is a lowercase set
    flat_config["ADDITIONAL_EXCLUDED_COLLECTIONS"] = set(
        c.lower() for c in flat_config.get("ADDITIONAL_EXCLUDED_COLLECTIONS", [])
    )
    return flat_config