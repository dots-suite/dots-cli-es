from dots_es.api.search import (
    build_structure_facet_agg,
    build_structure_facet_clause,
    extract_structure_facet,
)


def test_structure_facet_clause_reads_the_facet_keys():
    assert build_structure_facet_clause("citeType", ["acte###acte", "partie###partie"]) == {
        "terms": {"citeType": ["acte", "partie"]}
    }


def test_structure_facet_agg_counts_the_field_under_the_given_clauses():
    agg = build_structure_facet_agg("level", [{"match_all": {}}], [{"terms": {"citeType": ["acte"]}}])

    assert agg["aggs"]["filtered"]["filter"]["bool"] == {
        "must": [{"match_all": {}}],
        "filter": [{"terms": {"citeType": ["acte"]}}],
    }
    assert agg["aggs"]["filtered"]["aggs"]["values"]["terms"]["field"] == "level"


def test_structure_facet_values():
    aggregation = {"filtered": {"values": {"buckets": [
        {"key": 1, "doc_count": 12},
        {"key": 2, "doc_count": 3},
    ]}}}

    assert extract_structure_facet(aggregation) == [
        {"id": "1", "label": "1", "count": 12, "facet_key": "1###1"},
        {"id": "2", "label": "2", "count": 3, "facet_key": "2###2"},
    ]


def test_structure_facet_with_a_single_value_offers_no_choice():
    aggregation = {"filtered": {"values": {"buckets": [{"key": "article", "doc_count": 304}]}}}

    assert extract_structure_facet(aggregation) == []
