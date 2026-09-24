import json
from importlib import resources

import pytest


@pytest.mark.parametrize("index", ["dots_document", "dots_collection"])
def test_strings_are_never_detected_as_dates(index):
    # With date detection, the first "1241-03" fixes the field as date and every "1160–1196" rejects its whole document
    with resources.files("dots_es").joinpath("elasticsearch", f"{index}.conf.json").open() as f:
        mappings = json.load(f)["mappings"]

    assert mappings["date_detection"] is False
