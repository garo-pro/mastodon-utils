"""Async Mastodon API client with rate-limit handling, retries and pagination."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from . import __version__
from .models import parse_time as _parse_time  # always timezone-aware, so comparisons with now() can't raise
from .target import Target

USER_AGENT = f"mastodon-utils/{__version__} (statistics CLI; +https://pypi.org/project/mastodon-utils/)"


class ApiError(Exception):
    def __init__(self, host: str, path: str, status: int, message: str = ""):
        self.host, self.path, self.status, self.message = host, path, status, message
        detail = f"HTTP {status}" if status else "network error"
        where = path if path.startswith("http") else f"{host}{path}"
        super().__init__(f"{where}: {detail}{': ' + message if message else ''}")


class HostClient:
    """One Mastodon server. Tracks X-RateLimit headers and backs off before running dry."""

    def __init__(self, host: str, http: httpx.AsyncClient, token: str | None, concurrency: int, verbose: bool):
        self.host = host
        self.http = http
        self.token = token
        self.sem = asyncio.Semaphore(concurrency)
        self.remaining: int | None = None
        self.reset_at: datetime | None = None
        self.requests = 0
        self.verbose = verbose
        self._lock = asyncio.Lock()

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"https://{self.host}{path}"

    def _track(self, r: httpx.Response) -> None:
        rem = r.headers.get("x-ratelimit-remaining")
        if rem is not None and rem.isdigit():
            self.remaining = int(rem)
        reset = _parse_time(r.headers.get("x-ratelimit-reset"))
        if reset:
            self.reset_at = reset

    async def _respect_rate_limit(self) -> None:
        async with self._lock:
            if self.remaining is None or self.remaining > 3 or not self.reset_at:
                return
            wait = (self.reset_at - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                wait = min(wait + 1, 330)
                print(f"Rate limit nearly used up on {self.host}; waiting {wait:.0f} seconds.", file=sys.stderr)
                await asyncio.sleep(wait)
            self.remaining = None

    def _retry_after(self, r: httpx.Response, attempt: int) -> float:
        ra = r.headers.get("retry-after")
        if ra and ra.isdigit():
            return min(float(ra), 330)
        reset = _parse_time(r.headers.get("x-ratelimit-reset"))
        if reset:
            return max(1.0, min((reset - datetime.now(timezone.utc)).total_seconds() + 1, 330))
        return 2.0**attempt

    async def request(
        self, path: str, params: dict | None = None, *, auth: bool = True, retries: int = 3, timeout: float | None = None
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token}"} if (self.token and auth) else {}
        attempt = 0
        while True:
            await self._respect_rate_limit()
            async with self.sem:
                try:
                    kwargs: dict[str, Any] = {"params": params, "headers": headers}
                    if timeout is not None:
                        kwargs["timeout"] = timeout
                    r = await self.http.get(self._url(path), **kwargs)
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    attempt += 1
                    if attempt > retries:
                        raise ApiError(self.host, path, 0, str(e) or type(e).__name__) from e
                    await asyncio.sleep(2.0**attempt)
                    continue
            self.requests += 1
            self._track(r)
            if self.verbose:
                print(f"GET {r.request.url} -> {r.status_code}", file=sys.stderr)
            if r.status_code == 429 and attempt < retries:
                attempt += 1
                wait = self._retry_after(r, attempt)
                print(f"Rate limited by {self.host}; retrying in {wait:.0f} seconds.", file=sys.stderr)
                await asyncio.sleep(wait)
                continue
            if r.status_code >= 500 and attempt < retries:
                attempt += 1
                await asyncio.sleep(2.0**attempt)
                continue
            if r.status_code >= 400:
                message = ""
                try:
                    body = r.json()
                    if isinstance(body, dict):
                        message = str(body.get("error") or body.get("error_description") or "")
                except ValueError:
                    pass
                raise ApiError(self.host, path, r.status_code, message)
            return r

    async def get_json(self, path: str, params: dict | None = None, **kw) -> Any:
        r = await self.request(path, params, **kw)
        try:
            return r.json()
        except ValueError as e:
            raise ApiError(self.host, path, r.status_code, "response was not JSON") from e

    async def paginate(self, path: str, params: dict | None = None, max_items: int | None = None) -> AsyncIterator[dict]:
        """Follow Link rel="next" headers. max_items=None or 0 means no cap."""
        url: str | None = path
        p = dict(params or {})
        count = 0
        while url:
            r = await self.request(url, p if url == path else None)
            try:
                items = r.json()
            except ValueError as e:
                raise ApiError(self.host, url, r.status_code, "response was not JSON") from e
            if not isinstance(items, list) or not items:
                return
            for item in items:
                yield item
                count += 1
                if max_items and count >= max_items:
                    return
            nxt = r.links.get("next", {}).get("url")
            url = nxt if nxt and nxt != url else None


class ClientPool:
    """Shares one HTTP connection pool across all hosts; one HostClient per server."""

    def __init__(self, tokens: dict[str, str] | None = None, concurrency: int = 4, timeout: float = 20.0, verbose: bool = False):
        self.tokens = {k.lower(): v for k, v in (tokens or {}).items()}
        self.concurrency = concurrency
        self.timeout = timeout
        self.verbose = verbose
        self._hosts: dict[str, HostClient] = {}
        self.http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ClientPool":
        self.http = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=self.timeout,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self.http:
            await self.http.aclose()

    def host(self, host: str) -> HostClient:
        host = host.lower()
        if host not in self._hosts:
            assert self.http is not None, "use ClientPool as an async context manager"
            self._hosts[host] = HostClient(host, self.http, self.tokens.get(host), self.concurrency, self.verbose)
        return self._hosts[host]

    @property
    def total_requests(self) -> int:
        return sum(c.requests for c in self._hosts.values())


# ---------------------------------------------------------------------------
# Higher level calls
# ---------------------------------------------------------------------------


async def webfinger(pool: ClientPool, user: str, host: str) -> tuple[str, str, str]:
    """Return (api_host, username, handle_domain) for user@host, following WebFinger delegation.

    Handles servers whose handle domain differs from the web domain
    (e.g. @me@example.com served from social.example.com). The handle
    domain comes from the WebFinger subject, which is canonical.
    """
    try:
        data = await pool.host(host).get_json(
            "/.well-known/webfinger", {"resource": f"acct:{user}@{host}"}, auth=False, retries=1
        )
    except ApiError:
        return host, user, host
    api_host, username, domain = host, user, host
    if not isinstance(data, dict):
        return api_host, username, domain
    subject = str(data.get("subject", ""))
    if subject.startswith("acct:") and "@" in subject:
        name, _, dom = subject[5:].partition("@")
        username = name or user
        domain = dom.lower() or host
    for link in data.get("links", []) or []:
        if link.get("rel") == "self" and "activity+json" in str(link.get("type", "")):
            netloc = urlparse(str(link.get("href", ""))).netloc
            if netloc:
                api_host = netloc.lower()
            break
    return api_host, username, domain


async def lookup_account(pool: ClientPool, target: Target) -> tuple[dict, str, str]:
    """Resolve an account on its home server.

    Returns (account_json, api_host, handle_domain). Fetch from api_host; name
    the account (and the server's other local accounts) with handle_domain.
    """
    api_host, username, domain = await webfinger(pool, target.username or "", target.host)
    try:
        account = await pool.host(api_host).get_json("/api/v1/accounts/lookup", {"acct": username})
    except ApiError as e:
        if e.status in (404, 410) and api_host != target.host:
            # WebFinger pointed somewhere unhelpful; try the handle's own domain.
            api_host, domain = target.host, target.host
            try:
                account = await pool.host(api_host).get_json("/api/v1/accounts/lookup", {"acct": target.username})
            except ApiError as e2:
                if e2.status in (404, 410):
                    raise ApiError(api_host, e2.path, e2.status, f"account @{target.acct} not found") from e2
                raise
        elif e.status in (404, 410):
            raise ApiError(api_host, e.path, e.status, f"account @{target.acct} not found") from e
        else:
            raise
    return account, api_host, domain


BOOST_HEAVY_CAP = 5  # fetch at most this many items per requested own post


async def fetch_statuses(
    pool: ClientPool, api_host: str, account_id: str, max_own: int | None, since: datetime | None = None
) -> tuple[list[dict], bool]:
    """Newest-first statuses, replies and boosts included.

    `max_own` counts the account's own posts (originals, threads, replies); boosts
    ride along without counting so boost-heavy accounts still yield enough of
    their own writing. Total items are capped at max_own * BOOST_HEAVY_CAP.
    Returns (statuses, reached_end_of_history).
    """
    client = pool.host(api_host)
    out: list[dict] = []
    own = 0
    reached_end = True
    cap = max_own * BOOST_HEAVY_CAP if max_own else None
    async for s in client.paginate(f"/api/v1/accounts/{account_id}/statuses", {"limit": 40}, None):
        created = _parse_time(s.get("created_at"))
        if since and created and created < since:
            # Pinned posts are returned first on some servers; skip rather than stop on those.
            if s.get("pinned"):
                continue
            break
        out.append(s)
        if not s.get("reblog"):
            own += 1
        if (max_own and own >= max_own) or (cap and len(out) >= cap):
            reached_end = False
            break
    return out, reached_end


async def _optional(coro) -> Any:
    try:
        return await coro
    except ApiError as e:
        return {"__error__": str(e), "__status__": e.status}


async def fetch_server(pool: ClientPool, host: str, timeline_pages: int = 2) -> dict[str, Any]:
    """Collect everything publicly available about a server, tolerating missing endpoints."""
    c = pool.host(host)

    async def nodeinfo() -> Any:
        index = await c.get_json("/.well-known/nodeinfo", auth=False, retries=1)
        if not isinstance(index, dict):
            return {"__error__": "malformed nodeinfo index"}
        links = [l for l in index.get("links") or [] if isinstance(l, dict)]
        links.sort(key=lambda l: str(l.get("rel", "")), reverse=True)
        for link in links:
            href = link.get("href")
            if href:
                return await c.get_json(href, auth=False, retries=1)
        return {"__error__": "no nodeinfo link"}

    async def timeline() -> Any:
        items: list[dict] = []
        async for s in c.paginate("/api/v1/timelines/public", {"local": "true", "limit": 40}, 40 * timeline_pages):
            items.append(s)
        return items

    keys = ["instance_v2", "instance_v1", "activity", "peers", "nodeinfo", "trend_tags", "trend_statuses",
            "trend_links", "timeline", "emojis"]
    coros = [
        c.get_json("/api/v2/instance"),
        c.get_json("/api/v1/instance"),
        c.get_json("/api/v1/instance/activity"),
        c.get_json("/api/v1/instance/peers", timeout=60),
        nodeinfo(),
        c.get_json("/api/v1/trends/tags", {"limit": 10}),
        c.get_json("/api/v1/trends/statuses", {"limit": 10}),
        c.get_json("/api/v1/trends/links", {"limit": 5}),
        timeline(),
        c.get_json("/api/v1/custom_emojis"),
    ]
    results = await asyncio.gather(*(_optional(x) for x in coros))
    data = dict(zip(keys, results))
    if all(isinstance(v, dict) and "__error__" in v for v in (data["instance_v2"], data["instance_v1"], data["nodeinfo"])):
        raise ApiError(host, "/api/v2/instance", data["instance_v1"].get("__status__", 0),
                       "no Mastodon API or NodeInfo found; is this a fediverse server?")
    return data
