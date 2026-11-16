import pytest

from dots_es.cli import collections_purge_query, purge_collections, split_collection_ids


class FakeIndices:
    def __init__(self, existing):
        self.existing = existing

    def exists(self, index):
        return index in self.existing


class FakeES:
    def __init__(self, existing=("frags", "res"), failures=None):
        self.indices = FakeIndices(existing)
        self.failures = failures or []
        self.calls = []

    def options(self, **kwargs):
        return self

    def delete_by_query(self, index, query, **kwargs):
        self.calls.append((index, query, kwargs))
        return {"deleted": 3, "failures": self.failures}


class FakeApp:
    config = {"FRAGMENT_INDEX": "frags", "RESOURCE_INDEX": "res"}


def test_split_collection_ids_drop_blanks_and_spaces():
    assert split_collection_ids(" theater, ENCPOS,,") == ["theater", "ENCPOS"]
    assert split_collection_ids(None) == []


def test_purge_query_matches_every_depth_of_each_collection():
    query = collections_purge_query(["cartulaires", "ENCPOS"])

    should = query["bool"]["should"]
    # collection_facets holds `id###label` for each ancestor collection
    assert {"prefix": {"collection_facets": "cartulaires###"}} in should
    assert {"prefix": {"collection_facets": "ENCPOS###"}} in should
    # a resource id given to --collections has no facet of its own
    assert {"terms": {"resource_id": ["cartulaires", "ENCPOS"]}} in should
    assert query["bool"]["minimum_should_match"] == 1


def test_purge_hits_both_indexes_with_the_same_query():
    es = FakeES()

    purge_collections(FakeApp(), es, ["cartulaires"])

    assert [c[0] for c in es.calls] == ["frags", "res"]
    assert all(c[1] == collections_purge_query(["cartulaires"]) for c in es.calls)
    assert all(c[2]["refresh"] is True for c in es.calls)


def test_purge_skips_a_missing_index():
    es = FakeES(existing=("res",))

    purge_collections(FakeApp(), es, ["cartulaires"])

    assert [c[0] for c in es.calls] == ["res"]


def test_purge_raises_on_failures():
    es = FakeES(failures=[{"cause": "version conflict"}])

    with pytest.raises(RuntimeError):
        purge_collections(FakeApp(), es, ["cartulaires"])
