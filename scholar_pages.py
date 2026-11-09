"""Recognize usable Scholar search pages and Google's challenge responses.

The HTTP client, Firefox and browser extension must agree on what counts as
an answer. A successful status or an empty results container is insufficient:
Scholar must have supplied result titles or an explicit empty-search notice.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup


_CHALLENGE_TEXT = re.compile(
    r"\bunusual\s+traffic\b|\bautomated\s+quer(?:y|ies)\b|"
    r"\bnot\s+(?:a\s+)?robot\b|\brecaptcha\b|"
    r"\b(?:verify|confirm|prove)\s+(?:that\s+)?(?:you(?:'re|\s+are)?\s+)?"
    r"(?:a\s+)?human\b",
    re.IGNORECASE,
)
_EMPTY_SEARCH = re.compile(
    r"\bdid\s+not\s+match\s+any\b|"
    r"\b(?:no|0)\s+(?:results|articles|cases|opinions|documents|citations)"
    r"\s+(?:(?:were\s+)?found|match(?:ed)?|for\s+(?:your\s+)?(?:search|query))\b|"
    r"\b(?:search|query)\s+(?:returned|yielded|produced)\s+"
    r"(?:no|0)\s+(?:results|articles|cases|opinions|documents)\b|"
    r"\b(?:search|query)\s+did\s+not\s+return\s+any\s+results\b",
    re.IGNORECASE,
)
_TITLE_BADGE = re.compile(r"^\s*\[(?:citation|book|pdf|html|doc)\]\s*", re.IGNORECASE)


def is_search_url(url: str) -> bool:
    """Whether *url* names Scholar's search, citing list or versions list."""
    try:
        parts = urlparse(url or "")
        return (parts.scheme.lower() in {"http", "https"}
                and (parts.hostname or "").lower() == "scholar.google.com"
                and parts.path.rstrip("/") == "/scholar")
    except (TypeError, ValueError):
        return False


def _result_titles(soup: BeautifulSoup) -> bool:
    for title in soup.select(".gs_r .gs_rt"):
        text = title.get_text(" ", strip=True)
        previous = None
        while text != previous:
            previous, text = text, _TITLE_BADGE.sub("", text)
        if text.strip():
            return True
    return False


def _empty_notice(soup: BeautifulSoup) -> bool:
    # Scripts, comments and attributes are not a message from Scholar.
    text = soup.get_text(" ", strip=True)
    column = soup.find(id="gs_res_ccl")
    notices = [text]
    if column is not None:
        notices.append(column.get_text(" ", strip=True))
    return (bool(_EMPTY_SEARCH.search(text))
            or any(notice.casefold().strip(" .\t\r\n") in {"no results", "no articles"}
                   for notice in notices))


def has_search_content(html: str) -> bool:
    """A real result title (linked or citation-only), or a genuine no-match
    notice. An empty ``#gs_res_ccl`` shell and arbitrary ``.gs_r`` text fail.
    """
    if not isinstance(html, str) or not html.strip():
        return False
    soup = BeautifulSoup(html, "html.parser")
    return _result_titles(soup) or _empty_notice(soup)


def is_challenge(url: str, html: str) -> bool:
    """An explicit Google challenge, including those served with HTTP 200.

    Ordinary opinion text and result snippets may quote the same words as a
    challenge. Actual Scholar content takes precedence over phrases alone;
    a challenge URL or an actual CAPTCHA widget still takes precedence.
    """
    try:
        path = urlparse(url or "").path.lower()
        if path == "/sorry" or path.startswith("/sorry/"):
            return True
    except (TypeError, ValueError):
        pass
    if not isinstance(html, str) or not html.strip():
        return False
    soup = BeautifulSoup(html, "html.parser")
    captcha_script = False
    for tag in soup.find_all(True):
        ident = str(tag.get("id", "")).casefold()
        classes = {str(value).casefold() for value in tag.get("class", [])}
        if (ident in {"recaptcha", "recaptcha-form", "captcha-form"}
                or "g-recaptcha" in classes):
            return True
        if tag.name in {"form", "iframe", "script"}:
            address = str(tag.get("action" if tag.name == "form" else "src", "")).casefold()
            if "/sorry/" in address or "recaptcha" in address:
                if tag.name != "script":
                    return True
                captcha_script = True
    if (_result_titles(soup) or _empty_notice(soup)
            or any(node.get_text(" ", strip=True)
                   for node in soup.select("#gs_opinion, .gs_opinion, #gs_howcited_ccl"))):
        return False
    return captcha_script or bool(_CHALLENGE_TEXT.search(soup.get_text(" ", strip=True)))


def response_needs_browser(requested_url: str, final_url: str, html: str,
                           status_code: int = 200) -> bool:
    """Whether a response should enter the browser/CAPTCHA fallback.

    Server errors retain their normal retry handling. For a successful search
    request, redirects to a consent/interstitial page or a content-free search
    shell need the browser just as an explicit challenge does.
    """
    if status_code in (403, 429) or is_challenge(final_url, html):
        return True
    if status_code != 200 or not is_search_url(requested_url):
        return False
    return (not is_search_url(final_url or requested_url)
            or not has_search_content(html))
