import pytest

from dots_es.api.search_fields import (
    build_searchfield_aggs,
    extract_searchfield_facets,
    get_facet_es_field,
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


def test_resource_facets_are_unchanged():
    aggs = build_searchfield_aggs()

    assert aggs["dct:language"]["terms"]["field"] == "resource_metadata.dublincore.language.keyword"
    assert "resource_count" in aggs["dct:language"]["aggs"]


def test_fragment_facet_filter():
    assert get_facet_es_field("dublinCore.language", "fragment") == "fragment_metadata.dublincore.language.keyword"
    assert get_facet_es_field("collections", "fragment") == "collection_facets"


@pytest.mark.parametrize("criteria, expected", [
    ("dublinCore.date", "fragment_temporal.temporal.dublincore.date_start"),
    ("dublinCore.title", "fragment_metadata.dublincore.title.sort"),
    ("extensions.dateCreated", "fragment_temporal.temporal.extensions.dateCreated_start"),
    ("fragment_metadata.dublincore.type.keyword", "fragment_metadata.dublincore.type.sort"),
    ("passage_id", "passage_id"),
])
def test_fragment_sort(criteria, expected):
    assert resolve_fragment_sort_field(criteria) == expected


@pytest.mark.parametrize("criteria", ["title", "resource_metadata.dublincore.title.keyword", "temporal.temporal.dublincore.created_start"])
def test_resource_sort_criteria_are_refused_at_fragment_scope(criteria):
    with pytest.raises(ValueError):
        resolve_fragment_sort_field(criteria)


def test_resource_sort_is_unchanged():
    assert resolve_sort_field("dublinCore.date") == "temporal.temporal.dublincore.date_start"
