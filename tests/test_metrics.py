"""End-to-end metric tests on synthetic statuses (no network)."""

from datetime import datetime, timedelta, timezone

from mastodon_utils.account_metrics import Options, completion, compute_account_stats
from mastodon_utils.db import connect, follower_history, load_account, save_account
from mastodon_utils.models import normalize_status
from mastodon_utils.render import render_account, render_comparison

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
ACC = {"id": "1", "acct": "me", "username": "me", "display_name": "Me", "url": "https://x.social/@me",
       "created_at": "2024-01-01T00:00:00.000Z", "followers_count": 1000, "following_count": 200,
       "statuses_count": 900, "note": "<p>Hi</p>", "fields": []}


def status(i, hours_ago, kind="original", fav=5, boost=1, reply=1, quote=0, media=0, alt=0, tags=(), text="hello"):
    s = {
        "id": str(1000 + i), "created_at": (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
        "in_reply_to_id": None, "in_reply_to_account_id": None, "reblog": None,
        "favourites_count": fav, "reblogs_count": boost, "replies_count": reply, "quotes_count": quote,
        "url": f"https://x.social/@me/{i}", "content": f"<p>{text}</p>", "visibility": "public",
        "language": "en", "media_attachments": [{"type": "image", "description": "a cat" if n < alt else ""}
                                                for n in range(media)],
        "tags": [{"name": t} for t in tags], "mentions": [], "spoiler_text": "", "card": None, "poll": None,
    }
    if kind == "thread":
        s.update(in_reply_to_id="1", in_reply_to_account_id="1")
    elif kind == "reply":
        other = f"friend{i % 3}"
        s.update(in_reply_to_id="9", in_reply_to_account_id=f"9{i % 3}",
                 mentions=[{"id": f"9{i % 3}", "acct": f"{other}@y.social"}])
    elif kind == "boost":
        s["reblog"] = {"url": "https://y.social/@z/1", "account": {"acct": f"author{i % 4}"}}
    return s


def build():
    raw = []
    for i in range(40):
        raw.append(status(i, 24 * i + 5, fav=5 + (i % 5), media=1 if i % 2 else 0, alt=1 if i % 4 == 1 else 0,
                          tags=("python",) if i % 3 == 0 else ()))
    for i in range(40, 55):
        raw.append(status(i, 24 * (i - 40) + 2, kind="reply", fav=1, boost=0, reply=0))
    for i in range(55, 70):
        raw.append(status(i, 24 * (i - 55) + 9, kind="boost"))
    raw.append(status(99, 12, fav=900, boost=300, reply=50))  # viral post
    return raw


def stats(raw=None):
    raw = raw or build()
    posts = [normalize_status(s, "1", "x.social") for s in raw]
    return compute_account_stats(ACC, "x.social", posts, True, Options(now=NOW))


def test_classification_and_sample():
    st = stats()
    s = st["sample"]
    assert (s["originals"], s["replies"], s["boosts"]) == (41, 15, 15)
    assert st["account"]["acct"] == "me@x.social"


def test_engagement_is_robust_to_viral_post():
    st = stats()
    e = st["engagement"]
    assert e["per_post"]["mean_interactions"] > 3 * e["per_post"]["median_interactions"]
    assert e["per_post"]["typical_weighted"] < 30  # the viral post doesn't define typical
    assert e["top10_share"] > 0.5
    assert 0 < e["engagement_factor"] < 10


def test_engager_and_booster_breadth():
    st = stats()
    assert st["engager"]["unique_partners"] == 3
    assert 2.5 < st["engager"]["effective_partners"] <= 3
    assert st["booster"]["unique_authors"] == 4
    assert 0 < st["engager"]["engager_factor"] < 1
    assert 0 < st["booster"]["booster_factor"] < 1


def test_activity_and_content():
    st = stats()
    a = st["activity"]
    assert 0 < a["activity_factor"] <= 1
    assert a["recent_posts_per_day"] > 1
    c = st["content"]
    assert c["media_attachments"] == 20
    assert c["alt_text_coverage"] == 0.5
    assert c["top_tags"][0][0] == "python"
    assert 0 <= st["score"]["presence_score"] <= 100


def test_young_posts_are_projected():
    assert completion(1) < 0.2
    assert completion(48) > 0.99


def test_empty_account_renders():
    st = compute_account_stats(ACC, "x.social", [], True, Options(now=NOW))
    text = render_account(st)
    assert "No public posts" in text


def test_render_and_compare():
    st = stats()
    text = render_account(st, explain=True)
    assert "Engagement factor" in text and "How it works" in text
    assert "─" not in text  # no box drawing characters
    table = render_comparison([st, st], "account")
    assert table.splitlines()[3] == "|---|---|---|"


def test_db_roundtrip(tmp_path):
    raw = build()
    posts = [normalize_status(s, "1", "x.social") for s in raw]
    conn = connect(str(tmp_path / "t.db"))
    c1 = save_account(conn, "me@x.social", "x.social", ACC, raw, posts, "2026-09-01T00:00:00+00:00")
    assert c1["new"] == len(raw)
    raw[0]["favourites_count"] += 10
    posts = [normalize_status(s, "1", "x.social") for s in raw]
    acc2 = dict(ACC, followers_count=1100)
    c2 = save_account(conn, "me@x.social", "x.social", acc2, raw, posts, "2026-09-11T00:00:00+00:00")
    assert c2 == {"new": 0, "updated": 1, "unchanged": len(raw) - 1}
    acc, acct, stored = load_account(conn, "ME@x.social")
    assert acct == "me@x.social" and len(stored) == len(raw)
    h = follower_history(conn, "me@x.social")
    assert h["followers_change"] == 100 and h["followers_per_day"] == 10
    snaps = conn.execute("SELECT COUNT(*) FROM status_snapshots").fetchone()[0]
    assert snaps == 57  # 56 non-boost statuses on first scrape + 1 changed


def test_hour_label_rounds_across_the_hour():
    from mastodon_utils.render import hour_label
    assert hour_label(23.999) == "00:00"
    assert hour_label(9.9999) == "10:00"
    assert hour_label(13.5) == "13:30"


def test_follower_growth_does_not_overflow(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    save_account(conn, "me@x.social", "x.social", dict(ACC, followers_count=1), [], [], "2026-09-01T00:00:00+00:00")
    save_account(conn, "me@x.social", "x.social", dict(ACC, followers_count=10_000_000), [], [],
                 "2026-09-01T12:00:00+00:00")
    h = follower_history(conn, "me@x.social")
    assert h["followers_change"] == 9_999_999 and h["growth_pct_30d"] is None


def test_server_activity_ignores_malformed_weeks():
    from mastodon_utils.server_metrics import compute_server_stats
    data = {"instance_v1": {"uri": "x.social"}, "activity": [{"week": "bad"}, {}, "nope", {"week": "1"}]}
    st = compute_server_stats("x.social", data, now=NOW)
    assert st["activity"] == {"available": False}


def test_timezone_names_resolve():
    from mastodon_utils.cli import resolve_tz
    assert resolve_tz("Europe/Berlin").utcoffset(NOW).total_seconds() == 7200
