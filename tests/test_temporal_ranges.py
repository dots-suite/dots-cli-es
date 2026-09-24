import pytest

from dots_es.api.temporal import (
    foreign_range_fields,
    scope_sort_field,
    build_open_range,
    build_range_clause,
    build_temporal_aggs,
    extract_temporal_facets,
    iso_bound,
)

FIELD = "fragment_temporal.temporal.dublincore.date"


@pytest.mark.parametrize("value, expected", [
    ("1241", "1241||/y"),
    ("1241-03", "1241-03||/M"),
    ("1241-03-17", "1241-03-17||/d"),
    ("-0050", "-0050||/y"),
    ("-0050-03", "-0050-03||/M"),
    (" 0911-06-17 ", "0911-06-17||/d"),
])
def test_iso_bound_is_rounded_to_its_precision(value, expected):
    assert iso_bound(value) == expected


@pytest.mark.parametrize("value", ["-50", "1241-3", "1241/1300", "mars 1241", "1241-03-17T00:00", ""])
def test_iso_bound_rejects_other_values(value):
    with pytest.raises(ValueError):
        iso_bound(value)


def test_year_range_is_sent_as_is():
    assert build_range_clause({f"{FIELD}_start": {"lte": "1241"}}) == {
        "range": {f"{FIELD}_start": {"lte": "1241"}}
    }


def test_iso_range_is_rounded():
    assert build_range_clause({f"{FIELD}_start_iso": {"gte": "1241", "lte": "1241-03"}}) == {
        "range": {
            f"{FIELD}_start_iso": {
                "gte": "1241||/y",
                "lte": "1241-03||/M",
                "format": "strict_date||strict_year_month||strict_year",
            }
        }
    }


def test_unknown_operator_is_rejected():
    with pytest.raises(ValueError):
        build_range_clause({f"{FIELD}_start": {"from": "1241"}})


def test_open_range_keeps_documents_without_the_field():
    clause = build_open_range({f"{FIELD}_end_iso": {"gte": "1241-03"}})

    should = clause["bool"]["should"]
    assert should[0]["range"][f"{FIELD}_end_iso"]["gte"] == "1241-03||/M"
    assert should[1] == {"bool": {"must_not": [{"exists": {"field": f"{FIELD}_end_iso"}}]}}


def test_facet_ignores_its_own_iso_ranges():
    other = "fragment_temporal.temporal.tei.date"
    ranges = [
        {f"{FIELD}_start_iso": {"lte": "1241-03"}},
        {f"{other}_start": {"lte": "1300"}},
    ]

    aggs = build_temporal_aggs([FIELD], [], [], ranges, frozenset({FIELD}))
    filtered = aggs[f"{FIELD.replace('.', '__')}_available"]["aggs"]["filtered"]

    assert filtered["filter"]["bool"]["filter"] == [{"range": {f"{other}_start": {"lte": "1300"}}}]
    assert filtered["aggs"]["min_iso"] == {"min": {"field": f"{FIELD}_start_iso"}}
    assert filtered["aggs"]["max_iso"] == {"max": {"field": f"{FIELD}_end_iso"}}


def test_facet_exposes_iso_bounds():
    aggregations = {
        f"{FIELD.replace('.', '__')}_available": {
            "filtered": {
                "min": {"value": 528.0},
                "max": {"value": 1715.0},
                "min_iso": {"value": -45505152000000.0, "value_as_string": "0528-01-01"},
                "max_iso": {"value": -8029584000000.0, "value_as_string": "1715-07-22"},
            }
        }
    }

    [facet] = extract_temporal_facets(aggregations, [FIELD], frozenset({FIELD}))

    assert facet["key"] == "dublinCore.date"
    assert (facet["min"], facet["max"]) == (528, 1715)
    assert (facet["min_iso"], facet["max_iso"]) == ("0528-01-01", "1715-07-22")
    assert facet["start_field_iso"] == f"{FIELD}_start_iso"
    assert facet["end_field_iso"] == f"{FIELD}_end_iso"


def test_facet_without_iso_dates_keeps_the_year_shape():
    aggregations = {
        f"{FIELD.replace('.', '__')}_available": {
            "filtered": {"min": {"value": 528.0}, "max": {"value": 1715.0}}
        }
    }

    [facet] = extract_temporal_facets(aggregations, [FIELD])

    assert "min_iso" not in facet and "start_field_iso" not in facet


RESOURCE_FIELD = "temporal.temporal.dublincore.coverage"


def test_resource_scope_rejects_fragment_dates():
    ranges = [{f"{RESOURCE_FIELD}_start": {"lte": "1300"}}, {f"{FIELD}_end": {"gte": "1200"}}]

    assert foreign_range_fields(ranges, "resource") == [f"{FIELD}_end"]


def test_fragment_scope_rejects_resource_dates():
    ranges = [{f"{RESOURCE_FIELD}_start": {"lte": "1300"}}, {f"{FIELD}_end": {"gte": "1200"}}]

    assert foreign_range_fields(ranges, "fragment") == [f"{RESOURCE_FIELD}_start"]


def test_ranges_on_other_fields_are_not_scoped():
    assert foreign_range_fields([{"level": {"gte": "1"}}], "fragment") == []


def test_date_sort_follows_the_scope():
    resource_sort = "temporal.temporal.dublincore.created_start"

    assert scope_sort_field(resource_sort, "resource") == resource_sort
    assert scope_sort_field(resource_sort, "fragment") == f"fragment_{resource_sort}"
    assert scope_sort_field("resource_metadata.dublincore.title.sort", "fragment") == "resource_metadata.dublincore.title.sort"
