from __future__ import annotations

import re
from urllib.parse import quote_plus, unquote

import httpx

from ..core.tool import tool

_BODY_LIMIT = 50 * 1024  # 50 KB

_DDG_URL = "https://html.duckduckgo.com/html/"
_RESULT_RE = re.compile(
    r'class="result__a"[^>]*href="[^"]*uddg=([^&"]+)[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


@tool(name="fetch", desc="Fetch the body of a URL via HTTP GET (or other method).")
async def fetch(url: str, method: str = "GET") -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        try:
            resp = await client.request(method.upper(), url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return f"Error: HTTP {exc.response.status_code} for {url}"
        except httpx.HTTPError as exc:
            return f"Error: {exc}"

    text = resp.text
    if len(text) > _BODY_LIMIT:
        text = text[:_BODY_LIMIT] + f"\n... [truncated at {_BODY_LIMIT // 1024} KB]"
    return text


@tool(name="web_search", desc="Search the web via DuckDuckGo and return the top k results.")
async def web_search(query: str, k: int = 5) -> str:
    params = f"q={quote_plus(query)}"
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        try:
            resp = await client.post(
                _DDG_URL,
                content=params,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return f"Error: {exc}"

    results = _RESULT_RE.findall(resp.text)[:k]
    if not results:
        return f"No results found for {query!r}"

    lines: list[str] = []
    for raw_url, raw_title in results:
        url = unquote(raw_url)
        title = _strip_tags(raw_title)
        lines.append(f"- {title}\n  {url}")
    return "\n".join(lines)
