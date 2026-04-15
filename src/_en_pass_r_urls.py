"""Pass R: URLs and emails -> spoken form for English TTS.

Converts URLs and email addresses into TTS-friendly text. Only operates
inside a matched URL/email span so ordinary prose like "Dr. Smith" or
"Mt. Etna" is left untouched.
"""

from __future__ import annotations

import re

# Email: local@domain.tld (tld >=2 letters)
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# URL with explicit scheme (http, https, ftp, etc.)
_URL_SCHEME_RE = re.compile(
    r"\b(?:https?|ftp)://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
    re.IGNORECASE,
)

# URL starting with www. (no scheme)
_URL_WWW_RE = re.compile(
    r"\bwww\.[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+(?:/[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*)?",
    re.IGNORECASE,
)


def _speak_span(span: str) -> str:
    """Convert a matched URL/email span to spoken form."""
    # Drop scheme separator "://" entirely — it's hostile to TTS.
    span = re.sub(r"://", " ", span)
    # Replace structural punctuation with spoken words.
    span = span.replace("@", " at ")
    span = span.replace(".", " dot ")
    span = span.replace("/", " slash ")
    span = span.replace("?", " question ")
    span = span.replace("=", " equals ")
    span = span.replace("&", " and ")
    span = span.replace("#", " hash ")
    span = span.replace(":", " colon ")

    # Spell out "www" as "w w w" for naturalness.
    span = re.sub(r"\bwww\b", "w w w", span, flags=re.IGNORECASE)

    # Collapse whitespace.
    span = re.sub(r"\s+", " ", span).strip()
    return span


def _pass_r_urls_emails(text: str) -> str:
    """Normalize URLs and email addresses in ``text`` for TTS."""
    if not text:
        return text

    # Trailing punctuation that looks sentence-terminal, not URL-part.
    # We strip it before speaking, then re-append.
    def _sub(match: "re.Match[str]") -> str:
        raw = match.group(0)
        trailing = ""
        while raw and raw[-1] in ".,;:!?)":
            # Heuristic: a trailing "." is ambiguous. Keep it if it looks
            # like part of a TLD/path; drop into spoken "dot" via normal
            # path. We peel only obvious sentence terminators: , ; : ! ? )
            if raw[-1] == "." and len(raw) > 1 and raw[-2].isalpha():
                # Could be TLD boundary at end-of-sentence. Peel it.
                trailing = raw[-1] + trailing
                raw = raw[:-1]
                break
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        return _speak_span(raw) + trailing

    text = _EMAIL_RE.sub(_sub, text)
    text = _URL_SCHEME_RE.sub(_sub, text)
    text = _URL_WWW_RE.sub(_sub, text)
    return text
