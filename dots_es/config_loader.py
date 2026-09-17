import os
from importlib import resources
from typing import Any

import yaml

from dots_es.config_schema import IndexingConfig, SearchConfig, SourceConfig

# ============================================================
# LOAD YAML CONFIG
# ============================================================

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


def load_config(alias: str) -> dict:
    """Load a YAML configuration file, resolve environment variables, validate it with Pydantic,
    and flatten 'source' + 'config' sections for compatibility with App.

    :param alias: Configuration alias corresponding to config/{alias}.yml
    :type alias: str
    :return: Flattened, validated configuration dictionary
    :rtype: dict
    :raises FileNotFoundError: if the YAML config file does not exist
    :raises pydantic.ValidationError: if the configuration does not match the schema
    """
    config_path = resources.files("dots_es") / "config" / f"{alias}.yml"

    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Resolve environment variables
    config = resolve_env_vars(config)

    # Validate the 'source', 'config' and 'indexing' sections against the Pydantic schema
    source = SourceConfig.model_validate(config.get("source", {}))
    search = SearchConfig.model_validate(config.get("config", {}))
    indexing = IndexingConfig.model_validate(config.get("indexing", {}))

    # Flatten validated sections for App compatibility
    flat_config = (
        source.model_dump(mode="json")
        | search.model_dump(mode="json")
        | indexing.model_dump(mode="json")
    )

    # Ensure ADDITIONAL_EXCLUDED_COLLECTIONS is a lowercase set
    flat_config["ADDITIONAL_EXCLUDED_COLLECTIONS"] = set(
        source.ADDITIONAL_EXCLUDED_COLLECTIONS
    )
    return flat_config