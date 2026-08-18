import pytest

from canvas_archive.auth import (
    clean_token,
    looks_like_token,
    normalize_base_url,
    redact,
    remember_token,
    token_shard,
)

# A syntactically valid but entirely fake token. Never put a real one in a test:
# fixtures get committed, and this repository is public.
TOKEN = "1234~EXAMPLEtokenNOTreal0000000000000000000000000000000000000000000000"


@pytest.mark.parametrize(
    "pasted",
    [
        TOKEN,
        f"  {TOKEN}  \n",  # trailing newline from a terminal paste
        f"Bearer {TOKEN}",  # copied from an API doc example
        f"BEARER {TOKEN}",
        f"token={TOKEN}",
        f'"{TOKEN}"',
        f"'{TOKEN}'",
        f"“{TOKEN}”",  # smart quotes from a rich-text editor
        f'Bearer "{TOKEN}"',  # needs two peel passes
        f"{TOKEN}.",  # end of a sentence in an email
        f"{TOKEN},",
        f"﻿{TOKEN}",  # BOM
        f"{TOKEN[:20]}​{TOKEN[20:]}",  # zero-width space from a web page copy
        f" {TOKEN} ",  # non-breaking spaces
        f"{TOKEN[:30]}\n{TOKEN[30:]}",  # wrapped across lines in the source
    ],
)
def test_paste_damage_is_undone(pasted):
    assert clean_token(pasted) == TOKEN


@pytest.mark.parametrize("pasted", [TOKEN, f"Bearer {TOKEN}", f'"{TOKEN}".', ""])
def test_clean_token_is_idempotent(pasted):
    once = clean_token(pasted)
    assert clean_token(once) == once


@pytest.mark.parametrize("empty", ["", None, "   ", "\n\t", "​"])
def test_empty_inputs(empty):
    assert clean_token(empty) == ""


def test_shape_check_is_advisory_not_a_gate():
    assert looks_like_token(TOKEN)
    # Self-hosted Canvas issues tokens with no shard prefix. These must still be
    # usable -- rejecting them would lock out the users with the fewest options.
    assert not looks_like_token("abcdefghijklmnopqrstuvwxyz123456")
    assert clean_token("  abcdefghijklmnopqrstuvwxyz123456  ") == "abcdefghijklmnopqrstuvwxyz123456"


def test_token_shard():
    assert token_shard(TOKEN) == "1234"
    assert token_shard("no-shard-here") is None


@pytest.mark.parametrize(
    "raw",
    [
        "exec-learning.unisg.ch",
        "https://exec-learning.unisg.ch",
        "http://exec-learning.unisg.ch",
        "exec-learning.unisg.ch/",
        "EXEC-Learning.UNISG.ch",
        "https://exec-learning.unisg.ch/courses/812",
        "https://exec-learning.unisg.ch/profile/settings",
    ],
)
def test_url_normalisation(raw):
    assert normalize_base_url(raw) == "https://exec-learning.unisg.ch"


def test_redaction_covers_logs():
    assert TOKEN not in redact(f"GET /api?access_token={TOKEN} failed")
    assert TOKEN not in redact(f"Authorization: Bearer {TOKEN}")


def test_self_hosted_token_is_redacted_once_remembered():
    hosted = "abcdefghijklmnopqrstuvwxyz1234567890"
    remember_token(hosted)
    try:
        assert hosted not in redact(f"Authorization: Bearer {hosted}")
        assert "<redacted-token>" in redact(hosted)
    finally:
        remember_token(None)
