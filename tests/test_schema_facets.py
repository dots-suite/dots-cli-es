import pytest

from dots_es.api.search_fields import facet_fields
from dots_es.cli import allowed_extension_keys


@pytest.mark.parametrize("key", ["about", "genre", "keywords"])
def test_schema_keyword_facets_reach_the_index_and_the_api(key):
    # Domaine, genre and forme of the BFM: kept by the indexer and offered as facets
    assert key in allowed_extension_keys()
    assert f"extensions.{key}" in {field.path for field in facet_fields()}
