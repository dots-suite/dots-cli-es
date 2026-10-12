import pytest

from dots_es.api.search import build_resource_facet_clause

from dots_es.api.search_fields import (
    build_facet_presence_agg,
    build_searchfield_aggs,
    extract_searchfield_facets,
    facet_fields,
    get_facet_es_field,
    present_facet_keys,
    resolve_fragment_sort_field,
    resolve_sort_field,
)


def test_fragment_facets_read_fragment_metadata_and_count_fragments():
    aggs = build_searchfield_aggs(scope="fragment")

    assert aggs["dct:language"] == {
        "terms": {"field": "fragment_metadata.dublincore.language.keyword", "size": 15000}
    }
    assert not any("resource_metadata" in str(agg) for agg in aggs.values())


def test_fragment_facets_are_published_with_fragment_counts():
    aggregations = {"dct:language": {"buckets": [{"key": "lat", "doc_count": 436}]}}

    facets = extract_searchfield_facets(aggregations, scope="fragment")

    assert facets["dublinCore.language"] == [{"value": "lat", "count": 436}]


def test_facet_presence_checks_fragment_fields_in_the_perimeter():
    perimeter = [{"term": {"type.keyword": "fragment"}}, {"terms": {"citeType": ["acte"]}}]

    agg = build_facet_presence_agg(perimeter, scope="fragment")

    assert agg["aggs"]["perimeter"]["filter"] == {"bool": {"filter": perimeter}}
    assert agg["aggs"]["perimeter"]["aggs"]["fields"]["filters"]["filters"]["dublinCore.creator"] == {
        "exists": {"field": "fragment_metadata.dublincore.creator.keyword"}
    }


def test_facets_without_values_in_the_perimeter_are_left_out():
    presence = {"perimeter": {"fields": {"buckets": {
        "dublinCore.creator": {"doc_count": 0},
        "dublinCore.type": {"doc_count": 12743},
    }}}}

    assert present_facet_keys(presence) == {"dublinCore.type"}


def test_facet_presence_without_any_facet():
    agg = build_facet_presence_agg([], exclude_ids={field.key for field in facet_fields()})

    assert agg["aggs"]["perimeter"]["aggs"] == {}
    assert present_facet_keys({"perimeter": {"doc_count": 37}}) == set()


def test_resource_facets_count_resource_documents():
    # One document per resource in RESOURCE_INDEX: doc_count is exact, no cardinality
    aggs = build_searchfield_aggs()

    assert aggs["dct:language"] == {
        "terms": {"field": "resource_metadata.dublincore.language.keyword", "size": 15000}
    }


def test_resource_facets_are_published_with_resource_counts():
    aggregations = {"dct:language": {"buckets": [{"key": "fr", "doc_count": 3131}]}}

    facets = extract_searchfield_facets(aggregations)

    assert facets["dublinCore.language"] == [{"value": "fr", "count": 3131}]


def test_fragment_facet_filter():
    assert get_facet_es_field("dublinCore.language", "fragment") == "fragment_metadata.dublincore.language.keyword"
    assert get_facet_es_field("collections", "fragment") == "collection_facets"


@pytest.mark.parametrize("criteria, expected", [
    ("dublinCore.date", "fragment_temporal.temporal.dublincore.date_start"),
    ("dublinCore.title", "fragment_metadata.dublincore.title.sort"),
    ("extensions.dateCreated", "fragment_temporal.temporal.extensions.dateCreated_start"),
    ("fragment_metadata.dublincore.type.keyword", "fragment_metadata.dublincore.type.sort"),
    ("passage_id", "passage_id"),
    ("path", "path"),
    ("resource_id", "resource_id"),
    ("dublinCore.type", "fragment_metadata.dublincore.type.sort"),
])
def test_fragment_sort(criteria, expected):
    assert resolve_fragment_sort_field(criteria) == expected


@pytest.mark.parametrize("criteria", ["title", "resource_metadata.dublincore.title.keyword", "temporal.temporal.dublincore.created_start"])
def test_resource_sort_criteria_are_refused_at_fragment_scope(criteria):
    with pytest.raises(ValueError):
        resolve_fragment_sort_field(criteria)


def test_resource_sort_is_unchanged():
    assert resolve_sort_field("dublinCore.date") == "temporal.temporal.dublincore.date_start"


def test_resources_facet_filters_on_resource_ids():
    clause = build_resource_facet_clause(["MAUB-AB-01###Cartulaire de Maubuisson", "cid1998###La diplomatique urbaine"])

    assert clause == {"terms": {"resource_id": ["MAUB-AB-01", "cid1998"]}}
