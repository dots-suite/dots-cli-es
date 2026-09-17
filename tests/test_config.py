from importlib import resources

import pytest

from dots_es.config_loader import load_config


def _config_filenames():
    config_dir = resources.files("dots_es") / "config"
    return [p.stem for p in config_dir.iterdir() if p.suffix == ".yml"]


@pytest.mark.parametrize("config_filename", _config_filenames())
def test_config_loads(config_filename):
    load_config(config_filename)
