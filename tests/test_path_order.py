from dots_es.cli import extract_metadata, sanitize_resource_metadata
from dots_es.api.search import first_path_order


def test_path_order_chains_the_positions_in_each_parent():
    root = extract_metadata({"@id": "elec", "title": "Elec"})
    collection = extract_metadata(
        {"@id": "ENCPOS", "title": "Thèses"},
        parent_path=root["path"], parent_path_ids=root["path_ids"], parent_path_order=root["path_order"], position=3
    )
    resource = extract_metadata(
        {"@id": "ENCPOS_1849_02", "title": "Marguilliers"},
        parent_path=collection["path"], parent_path_ids=collection["path_ids"],
        parent_path_order=collection["path_order"], position=24
    )

    assert root["path_order"] == "000001"
    assert resource["path_order"] == "000001/000003/000024"
    assert resource["path_ids"] == ["elec", "ENCPOS", "ENCPOS_1849_02"]


def test_resource_metadata_leaves_path_and_path_order_out():
    cleaned = sanitize_resource_metadata({"path": "Elec > Thèses", "path_ids": ["elec"], "path_order": "000001"})

    assert cleaned == {"path_ids": ["elec"]}


def test_first_path_order_of_a_facet_value():
    bucket = {"first": {"hits": {"hits": [{"_source": {"path_order": "000001/000002"}}]}}}

    assert first_path_order(bucket) == "000001/000002"
    assert first_path_order({}) is None
