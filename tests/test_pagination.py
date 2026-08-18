import pytest

from canvas_archive.http.pagination import next_url, parse_link_header


def test_parses_multiple_rels():
    header = (
        '<https://x/api/v1/courses?page=2>; rel="next", '
        '<https://x/api/v1/courses?page=1>; rel="current", '
        '<https://x/api/v1/courses?page=9>; rel="last"'
    )
    links = parse_link_header(header)
    assert links["next"] == "https://x/api/v1/courses?page=2"
    assert links["last"] == "https://x/api/v1/courses?page=9"


def test_unquoted_rel():
    assert next_url("<https://x/p2>; rel=next") == "https://x/p2"


def test_extra_params_before_rel():
    assert next_url('<https://x/p2>; type="application/json"; rel="next"') == "https://x/p2"


@pytest.mark.parametrize("header", [None, "", "garbage", "<no-rel-here>", "; rel=\"next\""])
def test_missing_or_malformed_yields_none(header):
    assert next_url(header) is None


def test_last_page_has_no_next():
    assert next_url('<https://x/p1>; rel="current", <https://x/p1>; rel="last"') is None


def test_url_containing_commas_and_semicolons_in_query():
    header = '<https://x/api?ids[]=1&state[]=a>; rel="next"'
    assert next_url(header) == "https://x/api?ids[]=1&state[]=a"
