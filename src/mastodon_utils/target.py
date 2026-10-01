"""Detect whether user input is an account handle or a server."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}(?::\d{1,5})?$"
)
USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")

# URL path shapes that point at an account on common fediverse software.
_ACCOUNT_PATHS = [
    re.compile(r"^/@(?P<user>[^/@]+)(?:@(?P<host>[^/]+))?/?$"),  # Mastodon, Misskey
    re.compile(r"^/users/(?P<user>[^/]+)/?$"),  # Mastodon/Pleroma AP id
    re.compile(r"^/u/(?P<user>[^/]+)/?$"),  # Lemmy, Friendica-ish
    re.compile(r"^/profile/(?P<user>[^/]+)/?$"),  # Friendica
]


class TargetError(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    kind: str  # "account" or "server"
    host: str
    username: str | None = None
    raw: str = ""

    @property
    def acct(self) -> str:
        if self.kind != "account":
            raise AttributeError("server targets have no acct")
        return f"{self.username}@{self.host}"

    def __str__(self) -> str:
        return f"@{self.acct}" if self.kind == "account" else self.host


def _clean_host(host: str) -> str:
    host = host.strip().lower().rstrip("/").rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    if not HOST_RE.match(host):
        raise TargetError(f"'{host}' does not look like a valid server name")
    return host


def _clean_user(user: str) -> str:
    user = user.strip()
    if not USER_RE.match(user):
        raise TargetError(f"'{user}' does not look like a valid username")
    return user


def parse_target(raw: str, default_host: str | None = None) -> Target:
    """Classify input as an account or a server.

    Accepted account forms: @user@host, user@host, https://host/@user,
    https://host/@user@remote, https://host/users/user, and @user (needs
    default_host). Server forms: host, https://host, https://host/.
    """
    text = raw.strip()
    if not text:
        raise TargetError("empty target")

    # URL, with or without scheme.
    if re.match(r"^https?://", text, re.I) or (
        "/" in text and not text.startswith("@") and "." in text.split("/")[0]
    ):
        url = text if re.match(r"^https?://", text, re.I) else "https://" + text
        parsed = urlparse(url)
        host = _clean_host(parsed.netloc)
        path = parsed.path or "/"
        if path in ("", "/"):
            return Target("server", host, raw=raw)
        for pattern in _ACCOUNT_PATHS:
            m = pattern.match(path)
            if m:
                user = _clean_user(m.group("user"))
                remote = m.groupdict().get("host")
                return Target("account", _clean_host(remote) if remote else host, user, raw)
        raise TargetError(f"can't tell what '{raw}' points to (not a profile or server URL)")

    had_at = text.startswith("@")
    body = text[1:] if had_at else text
    if "@" in body:
        user, _, host = body.partition("@")
        if not user or not host:
            raise TargetError(f"'{raw}' is not a valid handle")
        return Target("account", _clean_host(host), _clean_user(user), raw)

    if had_at:
        if not default_host:
            raise TargetError(f"'{raw}' has no server; write it as @{body}@server or pass --server")
        return Target("account", _clean_host(default_host), _clean_user(body), raw)

    if "." in body:
        return Target("server", _clean_host(body), raw=raw)

    if default_host:
        return Target("account", _clean_host(default_host), _clean_user(body), raw)
    raise TargetError(
        f"can't tell whether '{raw}' is a user or a server; use @{body}@server for a user "
        "or a full domain name for a server"
    )
