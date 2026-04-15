"""Tests for Pass R: URLs and emails in English normalizer."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from _en_pass_r_urls import _pass_r_urls_emails  # noqa: E402


def test_simple_email():
    assert _pass_r_urls_emails("user@example.com") == "user at example dot com"


def test_http_url():
    assert _pass_r_urls_emails("http://foo.bar.org") == "http foo dot bar dot org"


def test_https_url():
    out = _pass_r_urls_emails("https://example.com")
    assert out == "https example dot com"


def test_https_url_with_path():
    out = _pass_r_urls_emails("https://example.com/path")
    assert out == "https example dot com slash path"


def test_www_only_url():
    out = _pass_r_urls_emails("www.example.com")
    assert out == "w w w dot example dot com"


def test_url_with_query_string():
    out = _pass_r_urls_emails("https://example.com/search?foo=bar")
    assert "example dot com" in out
    assert "slash search" in out
    assert "foo" in out and "bar" in out
    assert "://" not in out
    assert "?" not in out


def test_url_with_query_multiple_params():
    out = _pass_r_urls_emails("https://a.com/x?y=1&z=2")
    assert "://" not in out
    assert "&" not in out
    assert "?" not in out


def test_embedded_in_sentence():
    out = _pass_r_urls_emails("Visit https://example.com for details.")
    assert out.startswith("Visit ")
    assert "https example dot com" in out
    assert out.endswith(" for details.")


def test_email_embedded_in_sentence():
    out = _pass_r_urls_emails("Email me at user@example.com please.")
    assert "user at example dot com" in out
    assert out.endswith("please.")


def test_idempotence():
    once = _pass_r_urls_emails("https://example.com/path")
    twice = _pass_r_urls_emails(once)
    assert once == twice


def test_idempotence_email():
    once = _pass_r_urls_emails("user@example.com")
    twice = _pass_r_urls_emails(once)
    assert once == twice


def test_empty_string():
    assert _pass_r_urls_emails("") == ""


def test_plain_prose_untouched_dr_smith():
    text = "Dr. Smith went home."
    assert _pass_r_urls_emails(text) == text


def test_plain_prose_untouched_mt_etna():
    text = "We climbed Mt. Etna last summer."
    assert _pass_r_urls_emails(text) == text


def test_plain_prose_untouched_st_peter():
    text = "St. Peter's Basilica is in Rome."
    assert _pass_r_urls_emails(text) == text


def test_filename_untouched():
    text = "Open the file.txt in your editor."
    assert _pass_r_urls_emails(text) == text


def test_abbreviation_etc_untouched():
    text = "apples, oranges, etc. are fruit."
    assert _pass_r_urls_emails(text) == text


def test_multiple_urls():
    out = _pass_r_urls_emails("See https://a.org and http://b.net today.")
    assert "https a dot org" in out
    assert "http b dot net" in out


def test_multiple_emails():
    out = _pass_r_urls_emails("Contact a@x.com or b@y.org.")
    assert "a at x dot com" in out
    assert "b at y dot org" in out


def test_url_trailing_period_dropped():
    # Sentence-final period should not become "dot" at the end.
    out = _pass_r_urls_emails("Visit https://example.com.")
    assert out.endswith(".")
    # The spoken form should not end with "dot"
    assert not out.rstrip(".").endswith(" dot")


def test_url_trailing_comma():
    out = _pass_r_urls_emails("See https://example.com, then leave.")
    assert "https example dot com," in out


def test_scheme_colon_slash_slash_dropped():
    out = _pass_r_urls_emails("https://example.com")
    assert "://" not in out
    assert ":" not in out
    assert "/" not in out


def test_www_with_path():
    out = _pass_r_urls_emails("www.example.com/docs")
    assert out == "w w w dot example dot com slash docs"


def test_email_with_plus_alias():
    out = _pass_r_urls_emails("user+tag@example.com")
    assert "user+tag at example dot com" == out


def test_email_with_dots_in_local():
    out = _pass_r_urls_emails("first.last@example.com")
    assert "first dot last at example dot com" == out


def test_mixed_content():
    out = _pass_r_urls_emails(
        "Email user@x.com or visit https://x.com/home for info."
    )
    assert "user at x dot com" in out
    assert "https x dot com slash home" in out
    assert "for info." in out
