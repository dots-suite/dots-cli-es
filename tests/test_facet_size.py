import logging

from dots_es.api.search_fields import warn_truncated_facets


def test_truncated_facet_is_logged(caplog):
    aggregations = {"resources_fac": {"filtered": {"values": {"sum_other_doc_count": 12, "buckets": []}}}}

    with caplog.at_level(logging.WARNING):
        warn_truncated_facets(aggregations)

    assert "resources_fac.filtered.values" in caplog.text


def test_complete_facet_is_not_logged(caplog):
    with caplog.at_level(logging.WARNING):
        warn_truncated_facets({"collections_fac": {"values": {"sum_other_doc_count": 0}}})

    assert caplog.text == ""
