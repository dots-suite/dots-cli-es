from dots_es.api.search import build_scope_filter


def test_scope_matches_every_branch_of_a_resource():
    # collection_facets holds all the parents, path_ids only the branch indexed last
    assert build_scope_filter("moliere") == {"prefix": {"collection_facets": "moliere###"}}


def test_scope_prefix_does_not_match_sibling_ids():
    # "ENCPOS###" must not match "ENCPOS_1886###..."
    assert not "ENCPOS_1886###1886".startswith(build_scope_filter("ENCPOS")["prefix"]["collection_facets"])


def test_no_collection_means_no_scope():
    assert build_scope_filter(None) == {"match_all": {}}
