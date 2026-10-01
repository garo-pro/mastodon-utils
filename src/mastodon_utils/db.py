"""SQLite storage for scraped accounts, statuses and servers.

Designed for repeated scraping: each run adds an account snapshot (so you get
follower history) and a status snapshot whenever a post's counts changed (so
you can see how engagement accumulates over time). Raw JSON is kept so any
metric can be recomputed later with `--from-db`.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .models import Post

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS accounts (
    acct          TEXT PRIMARY KEY,      -- lower-case user@host
    api_host      TEXT NOT NULL,         -- server whose API we used
    remote_id     TEXT,
    username      TEXT,
    display_name  TEXT,
    url           TEXT,
    created_at    TEXT,
    bot           INTEGER,
    locked        INTEGER,
    first_seen    TEXT,
    last_scraped  TEXT,
    raw           TEXT                   -- latest account JSON
);

CREATE TABLE IF NOT EXISTS account_snapshots (
    acct            TEXT NOT NULL REFERENCES accounts(acct),
    scraped_at      TEXT NOT NULL,
    followers       INTEGER,
    following       INTEGER,
    statuses        INTEGER,
    last_status_at  TEXT,
    PRIMARY KEY (acct, scraped_at)
);

CREATE TABLE IF NOT EXISTS statuses (
    acct             TEXT NOT NULL REFERENCES accounts(acct),
    id               TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    kind             TEXT NOT NULL,      -- original | thread | reply | boost
    url              TEXT,
    text             TEXT,
    language         TEXT,
    visibility       TEXT,
    replies          INTEGER,
    reblogs          INTEGER,
    favourites       INTEGER,
    quotes           INTEGER,
    media_count      INTEGER,
    media_described  INTEGER,
    has_poll         INTEGER,
    has_cw           INTEGER,
    has_link         INTEGER,
    is_quote         INTEGER,
    tags             TEXT,               -- JSON array
    reply_to_acct    TEXT,
    boosted_acct     TEXT,
    first_seen       TEXT,
    last_updated     TEXT,
    raw              TEXT,
    PRIMARY KEY (acct, id)
);
CREATE INDEX IF NOT EXISTS statuses_by_time ON statuses (acct, created_at);

CREATE TABLE IF NOT EXISTS status_snapshots (
    acct        TEXT NOT NULL,
    id          TEXT NOT NULL,
    scraped_at  TEXT NOT NULL,
    replies     INTEGER,
    reblogs     INTEGER,
    favourites  INTEGER,
    quotes      INTEGER,
    PRIMARY KEY (acct, id, scraped_at)
);

CREATE TABLE IF NOT EXISTS server_snapshots (
    domain        TEXT NOT NULL,
    scraped_at    TEXT NOT NULL,
    users         INTEGER,
    active_month  INTEGER,
    local_posts   INTEGER,
    peers         INTEGER,
    health_score  REAL,
    stats         TEXT,                  -- computed stats JSON
    PRIMARY KEY (domain, scraped_at)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT,
    finished_at  TEXT,
    targets      TEXT,
    succeeded    INTEGER,
    failed       INTEGER
);

CREATE VIEW IF NOT EXISTS status_engagement AS
SELECT acct, id, created_at, kind, url,
       replies, reblogs, favourites, quotes,
       replies + reblogs + favourites + quotes AS interactions,
       replies * 3.0 + quotes * 2.5 + reblogs * 2.0 + favourites * 1.0 AS weighted
FROM statuses WHERE kind != 'boost';
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()
    return conn


def save_account(conn: sqlite3.Connection, acct: str, api_host: str, acc: dict,
                 raw_statuses: list[dict], posts: list[Post], scraped_at: str | None = None) -> dict[str, int]:
    """Upsert an account and its statuses. Returns counts of new, updated and unchanged statuses."""
    now = scraped_at or utcnow()
    key = acct.lower()
    with conn:
        conn.execute(
            """INSERT INTO accounts (acct, api_host, remote_id, username, display_name, url, created_at, bot, locked,
                                     first_seen, last_scraped, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(acct) DO UPDATE SET api_host=excluded.api_host, remote_id=excluded.remote_id,
                 username=excluded.username, display_name=excluded.display_name, url=excluded.url,
                 bot=excluded.bot, locked=excluded.locked, last_scraped=excluded.last_scraped, raw=excluded.raw""",
            (key, api_host, str(acc.get("id")), acc.get("username"), acc.get("display_name"), acc.get("url"),
             acc.get("created_at"), int(bool(acc.get("bot"))), int(bool(acc.get("locked"))), now, now,
             json.dumps(acc)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO account_snapshots VALUES (?,?,?,?,?,?)",
            (key, now, acc.get("followers_count"), acc.get("following_count"), acc.get("statuses_count"),
             acc.get("last_status_at")),
        )
        existing = {
            r["id"]: (r["replies"], r["reblogs"], r["favourites"], r["quotes"])
            for r in conn.execute("SELECT id, replies, reblogs, favourites, quotes FROM statuses WHERE acct=?", (key,))
        } if posts else {}
        counts = {"new": 0, "updated": 0, "unchanged": 0}
        for raw, p in zip(raw_statuses, posts):
            nums = (p.replies, p.reblogs, p.favourites, p.quotes)
            old = existing.get(p.id)
            if old is None:
                counts["new"] += 1
            elif tuple(old) != nums:
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
            conn.execute(
                """INSERT INTO statuses (acct, id, created_at, kind, url, text, language, visibility, replies, reblogs,
                       favourites, quotes, media_count, media_described, has_poll, has_cw, has_link, is_quote, tags,
                       reply_to_acct, boosted_acct, first_seen, last_updated, raw)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(acct, id) DO UPDATE SET text=excluded.text, replies=excluded.replies,
                     reblogs=excluded.reblogs, favourites=excluded.favourites, quotes=excluded.quotes,
                     media_count=excluded.media_count, media_described=excluded.media_described,
                     has_cw=excluded.has_cw, has_link=excluded.has_link, tags=excluded.tags,
                     last_updated=excluded.last_updated, raw=excluded.raw""",
                (key, p.id, p.created_at.isoformat(), p.kind, p.url, p.text, p.language, p.visibility,
                 p.replies, p.reblogs, p.favourites, p.quotes, len(p.media_types), p.media_described,
                 int(p.has_poll), int(p.has_cw), int(p.has_link), int(p.is_quote), json.dumps(p.tags),
                 p.reply_to_acct, p.boosted_acct, now, now, json.dumps(raw)),
            )
            if p.kind != "boost" and (old is None or tuple(old) != nums):
                conn.execute("INSERT OR REPLACE INTO status_snapshots VALUES (?,?,?,?,?,?,?)",
                             (key, p.id, now, *nums))
    return counts


def newest_status_time(conn: sqlite3.Connection, acct: str) -> str | None:
    r = conn.execute("SELECT MAX(created_at) AS t FROM statuses WHERE acct=?", (acct.lower(),)).fetchone()
    return r["t"] if r else None


def load_account(conn: sqlite3.Connection, acct: str) -> tuple[dict, str, list[dict]] | None:
    """Latest account JSON, its canonical acct, and stored statuses (newest first).

    Also matches on the API host, so @me@social.example.com finds @me@example.com.
    """
    user, _, host = acct.lower().partition("@")
    row = conn.execute(
        "SELECT acct, raw FROM accounts WHERE acct=? OR (lower(username)=? AND api_host=?) "
        "ORDER BY acct=? DESC LIMIT 1", (acct.lower(), user, host, acct.lower())).fetchone()
    if not row:
        return None
    statuses = [json.loads(r["raw"]) for r in conn.execute(
        "SELECT raw FROM statuses WHERE acct=? ORDER BY created_at DESC", (row["acct"],))]
    return json.loads(row["raw"]), row["acct"], statuses


def follower_history(conn: sqlite3.Connection, acct: str) -> dict[str, Any] | None:
    rows = conn.execute(
        "SELECT scraped_at, followers FROM account_snapshots WHERE acct=? ORDER BY scraped_at", (acct.lower(),)
    ).fetchall()
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    t0 = datetime.fromisoformat(first["scraped_at"])
    t1 = datetime.fromisoformat(last["scraped_at"])
    days = (t1 - t0).total_seconds() / 86400
    change = (last["followers"] or 0) - (first["followers"] or 0)
    per_day = change / days if days > 0 else None
    growth = None
    if days >= 0.5 and first["followers"] and last["followers"] is not None:
        try:
            growth = ((last["followers"] / first["followers"]) ** (30 / days) - 1) * 100
        except OverflowError:
            growth = None
        if growth is not None and (math.isinf(growth) or abs(growth) > 1e6):
            growth = None
    return {
        "snapshots": len(rows),
        "first": first["scraped_at"],
        "last": last["scraped_at"],
        "followers_start": first["followers"],
        "followers_end": last["followers"],
        "followers_change": change,
        "followers_per_day": round(per_day, 3) if per_day is not None else None,
        "growth_pct_30d": round(growth, 2) if growth is not None else None,
        "series": [[r["scraped_at"], r["followers"]] for r in rows],
    }


def save_server(conn: sqlite3.Connection, domain: str, st: dict[str, Any]) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO server_snapshots VALUES (?,?,?,?,?,?,?,?)",
            (domain.lower(), utcnow(), st["users"].get("total"), st["users"].get("active_month"),
             st["users"].get("local_posts"), st["federation"].get("peers"), st["score"].get("health_score"),
             json.dumps(st)),
        )


def list_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT a.acct, a.display_name, a.last_scraped,
                  (SELECT COUNT(*) FROM statuses s WHERE s.acct = a.acct) AS statuses,
                  (SELECT COUNT(*) FROM account_snapshots n WHERE n.acct = a.acct) AS snapshots,
                  (SELECT followers FROM account_snapshots n WHERE n.acct = a.acct
                     ORDER BY scraped_at DESC LIMIT 1) AS followers
           FROM accounts a ORDER BY a.acct"""
    ).fetchall()


def list_servers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT domain, COUNT(*) AS snapshots, MAX(scraped_at) AS last_scraped
           FROM server_snapshots GROUP BY domain ORDER BY domain"""
    ).fetchall()


def record_run(conn: sqlite3.Connection, started: str, targets: list[str], ok: int, failed: int) -> None:
    with conn:
        conn.execute("INSERT INTO scrape_runs (started_at, finished_at, targets, succeeded, failed) VALUES (?,?,?,?,?)",
                     (started, utcnow(), json.dumps(targets), ok, failed))
