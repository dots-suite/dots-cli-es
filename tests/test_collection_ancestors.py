from dots_es.api.search import without_collection_ancestors


def test_collections_facet_keeps_only_the_sub_collections():
    values = [{"id": "elec"}, {"id": "ENCPOS"}, {"id": "ENCPOS_1886"}, {"id": "ENCPOS_1887"}]

    assert without_collection_ancestors(values, {"elec", "ENCPOS"}) == [
        {"id": "ENCPOS_1886"},
        {"id": "ENCPOS_1887"},
    ]
