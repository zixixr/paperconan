# tests/fetch/test_live_network.py
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PAPERCONAN_LIVE") != "1",
    reason="live network test; set PAPERCONAN_LIVE=1 to run")


def test_zenodo_search_live():
    from paperconan.fetch import _sources
    cands = _sources.search_zenodo("spreadsheet", size=3)
    assert isinstance(cands, list)
    # at least one Zenodo record should come back for a common term
    assert cands and cands[0]["source"] == "zenodo"
