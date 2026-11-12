from dots_es.api.search import build_fragment_citeTypes_body


def test_cite_types_apply_the_caller_must_and_filters():
    body = build_fragment_citeTypes_body(
        [{"term": {"type.keyword": "fragment"}}, {"match": {"content": "Guérard"}}],
        [
            {"prefix": {"collection_facets": "cartulaires###"}},
            {"terms": {"level": [1]}},
        ],
    )

    assert body["query"]["bool"]["must"] == [
        {"term": {"type.keyword": "fragment"}},
        {"match": {"content": "Guérard"}},
    ]
    assert body["query"]["bool"]["filter"] == [
        {"prefix": {"collection_facets": "cartulaires###"}},
        {"terms": {"level": [1]}},
    ]
    assert body["aggregations"]["cite_types"] == {
        "terms": {"field": "citeType", "size": 100}
    }


def test_cite_types_without_clauses_are_unfiltered():
    body = build_fragment_citeTypes_body()

    assert body["query"]["bool"] == {"must": [], "filter": []}
