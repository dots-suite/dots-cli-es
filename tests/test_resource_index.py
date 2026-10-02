from dots_es.api.search import is_resource_filter
from dots_es.cli import apply_collection_scope


SCOPES = {
    "ENCPOS_1886_01": {
        "collections": [{"collection_id": "ENCPOS_1886"}, {"collection_id": "ENCPOS_1887"}],
        "collection_facets": ["ENCPOS_1886###1886", "ENCPOS_1887###1887"],
    }
}


def test_resource_document_receives_the_union_of_its_collections():
    doc = {"resource_id": "ENCPOS_1886_01", "type": "Resource", "collections": [{"collection_id": "ENCPOS_1886"}]}

    apply_collection_scope(doc, SCOPES)

    assert doc["collections"] == SCOPES["ENCPOS_1886_01"]["collections"]
    assert doc["collection_facets"] == SCOPES["ENCPOS_1886_01"]["collection_facets"]


def test_passage_only_receives_the_collection_facets():
    # The fragment mapping is strict and has no `collections` field
    doc = {"resource_id": "ENCPOS_1886_01", "type": "fragment", "collection_facets": ["ENCPOS_1886###1886"]}

    apply_collection_scope(doc, SCOPES)

    assert "collections" not in doc
    assert doc["collection_facets"] == SCOPES["ENCPOS_1886_01"]["collection_facets"]


def test_filters_are_routed_to_the_index_holding_the_field():
    def clause(field):
        return {"query_string": {"query": "x", "fields": [field]}}

    assert is_resource_filter(clause("resource_metadata.dublincore.creator"))
    assert is_resource_filter(clause("temporal.temporal.dublincore.created_start"))
    assert not is_resource_filter(clause("content"))
    assert not is_resource_filter(clause("fragment_metadata.dublincore.type"))
