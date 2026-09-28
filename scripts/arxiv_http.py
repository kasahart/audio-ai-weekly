"""Shared request identity and verified TLS settings for the arXiv API."""

import os
import ssl


def arxiv_request_headers(default_user_agent: str) -> dict[str, str]:
    """Keep optional operator contact details in the runtime environment only."""
    user_agent = (
        os.environ.get("ARXIV_USER_AGENT", "").strip()
        or default_user_agent
    )
    contact = os.environ.get("ARXIV_CONTACT", "").strip()
    if contact:
        user_agent = f"{user_agent} ({contact})"
    return {
        "User-Agent": user_agent,
        "Accept": "application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
    }


def arxiv_ssl_context() -> ssl.SSLContext:
    """Use the tested TLS handshake while retaining server certificate checks."""
    context = ssl.create_default_context()
    context.set_alpn_protocols(["http/1.1"])
    # urllib's implicit context enables PHA. Explicit contexts without that
    # extension succeeded in local comparisons of arXiv's empty HTTP 406s.
    context.post_handshake_auth = False
    return context


