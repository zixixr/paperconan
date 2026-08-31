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


def test_nature_esm_links_carry_their_labels_live():
    """The label capture against a real article page, not a fixture.

    A fixture proves the parser reads an anchor; it cannot prove the pages this parser is
    aimed at put text there at all. What is asserted is only what holds for every article:
    each ESM link carries SOME label.

    Deliberately NOT asserted: that a label names a figure. A first draft of this test did,
    and live data refused it -- the labels on this article are all of the "Supplementary
    Information" kind. Figure-naming labels are common and are what makes the label useful
    downstream, but they are a property of an article's supplementary material, not of the
    parser, so no single article can stand as evidence for them.
    """
    from paperconan.fetch._nature import search_nature_esm

    cands = search_nature_esm("10.1038/s41467-022-28338-0", size=5)
    files = [f for c in cands for f in (c.get("all_files") or [])]

    assert files, "no ESM files came back; the fixture cannot stand in for this"
    assert all(f.get("label") for f in files), (
        [f["name"] for f in files if not f.get("label")])
