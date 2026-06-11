import paperconan.fetch._nature as nat

# Minimal but realistic: a Nature article page exposes supplementary files as
# static-content.springer.com ESM links with the real extension in the path.
FIXTURE_HTML = '''
<html><body>
<a href="https://static-content.springer.com/esm/art%3A10.1038%2Fs41467-022-28338-0/MediaObjects/41467_2022_28338_MOESM1_ESM.pdf">Supplementary Information</a>
<a href="https://static-content.springer.com/esm/art%3A10.1038%2Fs41467-022-28338-0/MediaObjects/41467_2022_28338_MOESM4_ESM.xlsx">Source Data Fig 1</a>
<a href="https://static-content.springer.com/esm/art%3A10.1038%2Fs41467-022-28338-0/MediaObjects/41467_2022_28338_MOESM5_ESM.csv">Source Data Fig 2</a>
<a href="https://www.nature.com/articles/s41467-022-28338-0/figures/1">Fig 1</a>
</body></html>
'''


def test_parse_nature_esm_links_extracts_and_classifies():
    refs = nat.parse_nature_esm_links(FIXTURE_HTML)
    by_ext = {r["ext"]: r for r in refs}
    assert set(by_ext) == {"pdf", "xlsx", "csv"}          # only ESM links, not the figures link
    assert by_ext["xlsx"]["name"] == "41467_2022_28338_MOESM4_ESM.xlsx"
    assert by_ext["xlsx"]["download_url"].startswith("https://static-content.springer.com/esm/")


def test_search_nature_esm_builds_confident_candidate(monkeypatch):
    monkeypatch.setattr(nat._http, "get_text", lambda url, **k: FIXTURE_HTML)
    cands = nat.search_nature_esm("10.1038/s41467-022-28338-0")
    assert len(cands) == 1
    c = cands[0]
    assert c["source"] == "nature_esm"
    assert c["match_signals"] == {"doi_in_related": True}
    tab_exts = sorted(f["ext"] for f in c["tabular_files"])
    assert tab_exts == ["csv", "xlsx"]                     # pdf is not tabular


def test_search_nature_esm_non_doi_returns_empty():
    assert nat.search_nature_esm("some free text query") == []
