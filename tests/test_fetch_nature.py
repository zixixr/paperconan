import pytest

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

FIGURE_HTML = '''
<a href="/articles/s41467-022-28338-0/figures/1">Fig. 1</a>
<img src="https://media.springernature.com/full/springer-static/image/art%3A10.1038%2Fs41467-022-28338-0/MediaObjects/41467_2022_28338_Fig1_HTML.png">
'''


def test_parse_nature_esm_links_extracts_and_classifies():
    refs = nat.parse_nature_esm_links(FIXTURE_HTML)
    by_ext = {r["ext"]: r for r in refs}
    assert set(by_ext) == {"pdf", "xlsx", "csv"}          # only ESM links, not the figures link
    assert by_ext["xlsx"]["name"] == "41467_2022_28338_MOESM4_ESM.xlsx"
    assert by_ext["xlsx"]["download_url"].startswith("https://static-content.springer.com/esm/")


def test_parse_nature_esm_links_keeps_the_link_text_as_a_label():
    """The article page says which figure a file belongs to; keep it.

    Without it a workbook is just `41467_2022_28338_MOESM4_ESM.xlsx`, and nothing downstream
    can say which figure it holds -- the page knew, and the parser was throwing it away.
    """
    by_ext = {r["ext"]: r for r in nat.parse_nature_esm_links(FIXTURE_HTML)}

    assert by_ext["xlsx"]["label"] == "Source Data Fig 1"
    assert by_ext["csv"]["label"] == "Source Data Fig 2"
    assert by_ext["pdf"]["label"] == "Supplementary Information"


# A label is only taken when the anchor itself carries it. Guessing from surrounding text
# would attach a figure number to a file that may not hold it, and a wrong label is worse
# than none: it would let a finding be credited to a figure it never came from.
UNLABELLED_ESM_HTML = '''
<html><body>
<p>Source Data Fig 3</p>
<a href="https://static-content.springer.com/esm/art%3A10.1038%2Fs41467-022-28338-0/MediaObjects/41467_2022_28338_MOESM7_ESM.xlsx"><span class="icon"></span></a>
</body></html>
'''


def test_parse_nature_esm_links_leaves_the_label_empty_rather_than_guessing():
    refs = nat.parse_nature_esm_links(UNLABELLED_ESM_HTML)

    assert len(refs) == 1
    assert refs[0]["label"] is None


def test_parse_nature_esm_links_strips_markup_from_the_label():
    html = FIXTURE_HTML.replace(
        ">Source Data Fig 1<", "><strong>Source Data</strong>\n  Fig 1<")

    by_ext = {r["ext"]: r for r in nat.parse_nature_esm_links(html)}

    assert by_ext["xlsx"]["label"] == "Source Data Fig 1"


# Newer article pages serve ESM from the media.springernature.com CDN instead.
MEDIA_ESM_HTML = '''
<html><body>
<a href="https://media.springernature.com/original/springer-static/esm/art%3A10.1038%2Fs41586-024-08248-5/MediaObjects/41586_2024_8248_MOESM4_ESM.xlsx">Supplementary Tables</a>
<a href="https://media.springernature.com/original/springer-static/esm/art%3A10.1038%2Fs41586-024-08248-5/MediaObjects/41586_2024_8248_MOESM1_ESM.pdf">Supplementary Information</a>
</body></html>
'''


def test_parse_nature_esm_links_accepts_media_cdn_urls():
    refs = nat.parse_nature_esm_links(MEDIA_ESM_HTML)
    by_ext = {r["ext"]: r for r in refs}
    assert set(by_ext) == {"pdf", "xlsx"}
    assert by_ext["xlsx"]["name"] == "41586_2024_8248_MOESM4_ESM.xlsx"
    assert by_ext["xlsx"]["download_url"].startswith(
        "https://media.springernature.com/original/springer-static/esm/"
    )


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


def test_parse_nature_public_figure_links():
    refs = nat.parse_nature_figure_links(
        FIXTURE_HTML,
        "https://www.nature.com/articles/s41467-022-28338-0",
    )
    assert refs == [
        "https://www.nature.com/articles/s41467-022-28338-0/figures/1"
    ]


def test_parse_nature_figure_links_accepts_exact_relative_and_absolute_pages():
    html = """
    <a href="/articles/s41467-022-28338-0/figures/2">relative</a>
    <a href="figures/1">article-relative</a>
    <a href="https://www.nature.com/articles/s41467-022-28338-0/figures/3">absolute</a>
    """

    refs = nat.parse_nature_figure_links(
        html,
        "https://www.nature.com/articles/s41467-022-28338-0",
    )

    assert refs == [
        "https://www.nature.com/articles/s41467-022-28338-0/figures/1",
        "https://www.nature.com/articles/s41467-022-28338-0/figures/2",
        "https://www.nature.com/articles/s41467-022-28338-0/figures/3",
    ]


@pytest.mark.parametrize(
    "href",
    [
        "http://www.nature.com/articles/s41467-022-28338-0/figures/1",
        "https://external.example/articles/s41467-022-28338-0/figures/1",
        "//external.example/articles/s41467-022-28338-0/figures/1",
        "https://user@www.nature.com/articles/s41467-022-28338-0/figures/1",
        "https://www.nature.com:444/articles/s41467-022-28338-0/figures/1",
        "/articles/s41467-022-28338-0/figures/1?download=1",
        "/articles/s41467-022-28338-0/figures/1#panel",
        "/articles/s41467-022-99999-9/figures/1",
    ],
    ids=[
        "http",
        "external-origin",
        "protocol-relative-external",
        "credentials",
        "unexpected-port",
        "query",
        "fragment",
        "different-article",
    ],
)
def test_parse_nature_figure_links_rejects_boundary_variants(href):
    refs = nat.parse_nature_figure_links(
        f'<a href="{href}">figure</a>',
        "https://www.nature.com/articles/s41467-022-28338-0",
    )

    assert refs == []


def test_parse_nature_figure_links_caps_pages_deterministically():
    html = "\n".join(
        f'<a href="/articles/s41467-022-28338-0/figures/{number}">figure</a>'
        for number in range(105, 0, -1)
    )

    refs = nat.parse_nature_figure_links(
        html,
        "https://www.nature.com/articles/s41467-022-28338-0",
    )

    assert len(refs) == 100
    assert refs[0].endswith("/figures/1")
    assert refs[-1].endswith("/figures/100")


def test_search_nature_esm_adds_public_full_figure(monkeypatch):
    def stub_get_text(url, **kwargs):
        return FIGURE_HTML if url.endswith("/figures/1") else FIXTURE_HTML

    monkeypatch.setattr(nat._http, "get_text", stub_get_text)
    candidate = nat.search_nature_esm("10.1038/s41467-022-28338-0")[0]
    assert [f["ext"] for f in candidate["image_files"]] == ["png"]
    assert candidate["image_files"][0]["download_url"].startswith(
        "https://media.springernature.com/full/"
    )


def test_search_nature_esm_bounds_article_and_figure_text_fetches(monkeypatch):
    calls = []

    def stub_get_text(url, **kwargs):
        calls.append((url, kwargs))
        return FIGURE_HTML if url.endswith("/figures/1") else FIXTURE_HTML

    monkeypatch.setattr(nat._http, "get_text", stub_get_text)

    nat.search_nature_esm("10.1038/s41467-022-28338-0")

    assert len(calls) == 2
    for _, kwargs in calls:
        assert kwargs["max_bytes"] == 5 * 1024 * 1024
        assert kwargs["allowed_origins"] == {
            "https://www.nature.com",
            "https://idp.nature.com",   # cookie-consent bounce gateway
        }


def test_search_nature_esm_caps_figure_fetch_count(monkeypatch):
    article_html = FIXTURE_HTML + "\n".join(
        f'<a href="/articles/s41467-022-28338-0/figures/{number}">figure</a>'
        for number in range(1, 106)
    )
    figure_calls = []

    def stub_get_text(url, **kwargs):
        if "/figures/" in url:
            figure_calls.append(url)
            return "<html></html>"
        return article_html

    monkeypatch.setattr(nat._http, "get_text", stub_get_text)

    nat.search_nature_esm("10.1038/s41467-022-28338-0")

    assert len(figure_calls) == 100


def test_search_nature_esm_rejects_oversized_article_html(monkeypatch):
    def reject_text(url, **kwargs):
        raise ValueError("text response exceeds byte limit")

    monkeypatch.setattr(nat._http, "get_text", reject_text)

    assert nat.search_nature_esm("10.1038/s41467-022-28338-0") == []


def test_search_nature_esm_skips_oversized_figure_html(monkeypatch):
    def stub_get_text(url, **kwargs):
        if "/figures/" in url:
            raise ValueError("text response exceeds byte limit")
        return FIXTURE_HTML

    monkeypatch.setattr(nat._http, "get_text", stub_get_text)

    candidate = nat.search_nature_esm("10.1038/s41467-022-28338-0")[0]

    assert candidate["image_files"] == []
    assert sorted(ref["ext"] for ref in candidate["all_files"]) == [
        "csv",
        "pdf",
        "xlsx",
    ]
