"""Normalize raw Mastodon JSON into compact records the metrics work on."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    """HTML to plain text; also notes whether the post links anywhere besides mentions/hashtags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.external_links = 0
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "br":
            self.parts.append("\n")
        elif tag == "p" and self.parts:
            self.parts.append("\n\n")
        elif tag == "a":
            rel = a.get("rel") or ""
            if "mention" not in cls and "hashtag" not in cls and "tag" not in rel.split():
                self.external_links += 1
        elif tag == "span" and "invisible" in cls:
            self._skip += 1
        elif tag == "span" and self._skip:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "span" and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str | None) -> tuple[str, int]:
    if not html:
        return "", 0
    p = _TextExtractor()
    p.feed(html)
    p.close()
    return "".join(p.parts).strip(), p.external_links


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def full_acct(acct: str | None, home_host: str) -> str:
    """Mastodon omits the domain for local accounts; add it back so handles are comparable."""
    if not acct:
        return ""
    acct = acct.lstrip("@")
    return acct if "@" in acct else f"{acct}@{home_host}"


@dataclass
class Post:
    id: str
    created_at: datetime
    kind: str  # original | thread | reply | boost
    replies: int = 0
    reblogs: int = 0
    favourites: int = 0
    quotes: int = 0
    url: str = ""
    text: str = ""
    language: str | None = None
    visibility: str = "public"
    media_types: list[str] = field(default_factory=list)
    media_described: int = 0
    has_poll: bool = False
    has_cw: bool = False
    has_link: bool = False
    is_quote: bool = False
    tags: list[str] = field(default_factory=list)
    reply_to_acct: str | None = None
    boosted_acct: str | None = None
    edited: bool = False

    @property
    def has_media(self) -> bool:
        return bool(self.media_types)

    @property
    def interactions(self) -> int:
        return self.replies + self.reblogs + self.favourites + self.quotes

    @property
    def is_broadcast(self) -> bool:
        """Posts written for the audience (not replies to others, not boosts)."""
        return self.kind in ("original", "thread")

    @property
    def is_question(self) -> bool:
        return "?" in self.text


def normalize_status(s: dict, account_id: str, home_host: str) -> Post:
    created = parse_time(s.get("created_at")) or datetime.now(timezone.utc)
    reblog = s.get("reblog")
    if reblog:
        return Post(
            id=str(s["id"]),
            created_at=created,
            kind="boost",
            url=reblog.get("url") or reblog.get("uri") or "",
            boosted_acct=full_acct((reblog.get("account") or {}).get("acct"), home_host),
            visibility=s.get("visibility") or "public",
        )

    reply_to_account = s.get("in_reply_to_account_id")
    if s.get("in_reply_to_id") is None:
        kind = "original"
    elif str(reply_to_account) == str(account_id):
        kind = "thread"
    else:
        kind = "reply"

    reply_to_acct = None
    if kind == "reply":
        for m in s.get("mentions") or []:
            if str(m.get("id")) == str(reply_to_account):
                reply_to_acct = full_acct(m.get("acct"), home_host)
                break
        if reply_to_acct is None:
            reply_to_acct = f"id:{reply_to_account}"

    text, ext_links = html_to_text(s.get("content"))
    return Post(
        id=str(s["id"]),
        created_at=created,
        kind=kind,
        replies=int(s.get("replies_count") or 0),
        reblogs=int(s.get("reblogs_count") or 0),
        favourites=int(s.get("favourites_count") or 0),
        quotes=int(s.get("quotes_count") or 0),
        url=s.get("url") or s.get("uri") or "",
        text=text,
        language=s.get("language"),
        visibility=s.get("visibility") or "public",
        media_types=[str(m.get("type") or "unknown") for m in (s.get("media_attachments") or [])],
        media_described=sum(1 for m in (s.get("media_attachments") or []) if (m.get("description") or "").strip()),
        has_poll=bool(s.get("poll")),
        has_cw=bool((s.get("spoiler_text") or "").strip()),
        has_link=bool(s.get("card")) or ext_links > 0,
        is_quote=bool(s.get("quote")),
        tags=[str(t.get("name", "")).lower() for t in (s.get("tags") or []) if t.get("name")],
        reply_to_acct=reply_to_acct,
        edited=bool(s.get("edited_at")),
    )
