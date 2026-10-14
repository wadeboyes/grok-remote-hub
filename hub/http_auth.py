"""Present the same hub_token three ways. This does not skip any route.

Accepted proofs, compared to the configured secret:

- ``Authorization: Bearer <hub_token>``
- ``?token=<hub_token>`` on the request URL
- cookie ``hub_token=<hub_token>`` (set only after one of the proofs above)

An empty configured token still means the hub is open, same as before.
``/health`` is the only route that skips this check, and that skip stays in
the server middleware.
"""

from __future__ import annotations

import re
from urllib.parse import quote

HUB_TOKEN_COOKIE = "hub_token"

# Root-relative urls in HTML attributes and in inline CSS url("...").
_ATTR_URL = re.compile(r'(\b(?:src|href)=")(/[^"]*)(")')
_CSS_URL = re.compile(r'(url\(")(/[^"]*)("\))')


def token_accepted(
    expected: str,
    *,
    authorization: str,
    query_token: str,
    cookie_token: str,
) -> bool:
    """True when ``expected`` is empty or one presented value matches it."""
    secret = expected or ""
    if not secret:
        return True
    auth = authorization or ""
    if auth.lower().startswith("bearer ") and auth[7:].strip() == secret:
        return True
    if (query_token or "") == secret:
        return True
    if (cookie_token or "") == secret:
        return True
    return False


def _stamp_url(url: str, encoded: str) -> str:
    if not url.startswith("/") or url.startswith("//"):
        return url
    if "token=" in url:
        return url
    join = "&" if "?" in url else "?"
    return f"{url}{join}token={encoded}"


def stamp_token_on_asset_urls(html: str, token: str) -> str:
    """Add ``token`` to same-origin asset URLs so CSS, JS, and fonts can load.

    The document request already carried the secret. Subresources do not
    inherit the query string, and they stay behind hub_token.
    """
    secret = token or ""
    if not secret or not html:
        return html
    encoded = quote(secret, safe="")

    def repl_attr(match):
        return match.group(1) + _stamp_url(match.group(2), encoded) + match.group(3)

    def repl_css(match):
        return match.group(1) + _stamp_url(match.group(2), encoded) + match.group(3)

    return _CSS_URL.sub(repl_css, _ATTR_URL.sub(repl_attr, html))
