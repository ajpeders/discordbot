import pytest
from music.search.base import SearchProvider


def test_search_provider_is_abstract():
    """SearchProvider cannot be instantiated directly."""
    with pytest.raises(TypeError):
        SearchProvider()
