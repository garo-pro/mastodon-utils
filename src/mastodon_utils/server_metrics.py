"""Server analytics from the instance, activity, nodeinfo, trends and timeline endpoints."""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from . import stats as S
from .account_metrics import DEFAULT_WEIGHTS, _excerpt, _r, weighted
from .models import full_acct, html_to_text, normalize_status


def _ok(x: Any) -> bool:
    return x is not None and not (isinstance(x, dict) and "__error__" in x)


def _int(x: Any) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _dig(d: Any, *path, default=None):
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return default
        d = d[key]
    return d


def compute_server_stats(host: str, data: dict[str, Any], now: datetime | None = None,
                         weights: dict[str, float] | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    w = weights or DEFAULT_WEIGHTS
    v2 = data.get("instance_v2") if _ok(data.get("instance_v2")) else {}
    v1 = data.get("instance_v1") if _ok(data.get("instance_v1")) else {}
    ni = data.get("nodeinfo") if _ok(data.get("nodeinfo")) else {}
    unavailable = sorted(k for k, v in data.items() if not _ok(v))

    description = v2.get("description") or v1.get("short_description") or v1.get("description") or ""
    registrations_v2 = v2.get("registrations") or {}
    contact = _dig(v2, "contact", "account") or v1.get("contact_account") or {}
    out: dict[str, Any] = {
        "server": {
            "domain": v2.get("domain") or v1.get("uri") or host,
            "title": v2.get("title") or v1.get("title") or _dig(ni, "metadata", "nodeName"),
            "software": _dig(ni, "software", "name") or ("mastodon" if (v1 or v2) else None),
            "version": v2.get("version") or v1.get("version") or _dig(ni, "software", "version"),
            "description": html_to_text(description)[0][:400],
            "registrations_open": registrations_v2.get("enabled", v1.get("registrations", ni.get("openRegistrations"))),
            "approval_required": registrations_v2.get("approval_required", v1.get("approval_required")),
            "languages": v2.get("languages") or v1.get("languages") or [],
            "contact": ("@" + full_acct(contact.get("acct"), host)) if contact.get("acct") else None,
            "contact_email": _dig(v2, "contact", "email") or v1.get("email") or None,
            "rules": len(v2.get("rules") or v1.get("rules") or []),
            "max_post_chars": _dig(v2, "configuration", "statuses", "max_characters")
            or _dig(v1, "configuration", "statuses", "max_characters"),
            "custom_emojis": len(data["emojis"]) if isinstance(data.get("emojis"), list) else None,
        },
        "unavailable_endpoints": unavailable,
    }

    # -------------------------------------------------------------- users
    total_users = _int(_dig(v1, "stats", "user_count")) or _int(_dig(ni, "usage", "users", "total"))
    mau = _int(_dig(v2, "usage", "users", "active_month")) or _int(_dig(ni, "usage", "users", "activeMonth"))
    halfyear = _int(_dig(ni, "usage", "users", "activeHalfyear"))
    local_posts = _int(_dig(v1, "stats", "status_count")) or _int(_dig(ni, "usage", "localPosts"))
    mau_ratio = (mau / total_users) if (mau is not None and total_users) else None
    stickiness = (mau / halfyear) if (mau is not None and halfyear) else None
    out["users"] = {
        "total": total_users,
        "active_month": mau,
        "active_halfyear": halfyear,
        "local_posts": local_posts,
        "mau_ratio": _r(mau_ratio, 4),
        "stickiness": _r(min(1.0, stickiness), 3) if stickiness is not None else None,
        "dormant_share": _r(1 - (halfyear / total_users), 3) if (halfyear is not None and total_users) else None,
        "posts_per_user": _r(local_posts / total_users, 1) if (local_posts and total_users) else None,
    }

    # ---------------------------------------------------------- activity
    act_out: dict[str, Any] = {"available": False}
    activity = data.get("activity")
    rows = []
    if isinstance(activity, list):
        for a in activity:
            ts = _int(a.get("week")) if isinstance(a, dict) else None
            if ts is None:
                continue
            rows.append({
                "week_start": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
                "_ts": ts,
                "statuses": _int(a.get("statuses")) or 0,
                "logins": _int(a.get("logins")) or 0,
                "registrations": _int(a.get("registrations")) or 0,
            })
    if len(rows) >= 3:
        rows.sort(key=lambda r: r["_ts"])
        current = rows[-1]
        complete = rows[:-1]
        elapsed_days = (now.timestamp() - current["_ts"]) / 86400
        projected_current = None
        if 1.0 <= elapsed_days < 7.0:
            f = 7.0 / elapsed_days
            projected_current = {k: round(current[k] * f) for k in ("statuses", "logins", "registrations")}
        elif elapsed_days >= 7.0:
            complete = rows
            projected_current = None

        def momentum(key: str) -> float | None:
            ys = [math.log1p(r[key]) for r in complete]
            slope = S.theil_sen(list(range(len(ys))), ys)
            return None if slope is None else (math.exp(slope) - 1) * 100

        last4 = complete[-4:]
        reg4 = sum(r["registrations"] for r in last4)
        act_out = {
            "available": True,
            "weeks": [{k: v for k, v in r.items() if k != "_ts"} for r in complete],
            "current_week_so_far": {k: current[k] for k in ("week_start", "statuses", "logins", "registrations")},
            "current_week_projected": projected_current,
            "avg_weekly_statuses": _r(S.mean([r["statuses"] for r in complete]), 1),
            "avg_weekly_logins": _r(S.mean([r["logins"] for r in complete]), 1),
            "avg_weekly_registrations": _r(S.mean([r["registrations"] for r in complete]), 1),
            "posts_per_login": _r(S.median([r["statuses"] / r["logins"] for r in complete if r["logins"]]), 2),
            "login_momentum_pct_week": _r(momentum("logins"), 2),
            "status_momentum_pct_week": _r(momentum("statuses"), 2),
            "registration_momentum_pct_week": _r(momentum("registrations"), 2),
            "newcomer_share_of_mau": _r(reg4 / mau, 4) if mau else None,
            "login_volatility": _r(
                (math.sqrt(sum((r["logins"] - m) ** 2 for r in complete) / len(complete)) / m)
                if (m := S.mean([r["logins"] for r in complete])) else None, 3),
        }
    out["activity"] = act_out

    # --------------------------------------------------------- federation
    peers = data.get("peers")
    peer_count = len(peers) if isinstance(peers, list) else None
    tld = Counter()
    if isinstance(peers, list):
        for p in peers:
            parts = str(p).rsplit(".", 1)
            if len(parts) == 2:
                tld[parts[1].lower()] += 1
    out["federation"] = {
        "peers": peer_count,
        "known_domains": _int(_dig(v1, "stats", "domain_count")),
        "peer_tld_diversity": _r(S.effective_number(tld.values()), 2) if tld else None,
        "top_peer_tlds": S.top_counts(tld, 6) if tld else [],
    }

    # ------------------------------------------------------- local timeline
    tl = data.get("timeline")
    tl_out: dict[str, Any] = {"available": False}
    if isinstance(tl, list) and tl:
        posts = [normalize_status(s, str(_dig(s, "account", "id")), host) for s in tl]
        authors = Counter(full_acct(_dig(s, "account", "acct"), host) for s in tl)
        times = sorted(p.created_at for p in posts)
        span_h = (times[-1] - times[0]).total_seconds() / 3600 if len(times) > 1 else None
        n_media = sum(len(p.media_types) for p in posts)
        tl_out = {
            "available": True,
            "sample_size": len(posts),
            "span_hours": _r(span_h, 2),
            "posts_per_hour": _r((len(posts) - 1) / span_h, 2) if span_h else None,
            "unique_authors": len(authors),
            "effective_authors": _r(S.effective_number(authors.values()), 2),
            "voice_diversity": _r(S.effective_number(authors.values()) / len(posts), 3),
            "typical_weighted_engagement": _r(S.hodges_lehmann([weighted(p, w) for p in posts]), 2),
            "reply_share": _r(sum(p.kind != "original" for p in posts) / len(posts), 3),
            "media_share": _r(sum(p.has_media for p in posts) / len(posts), 3),
            "alt_text_coverage": _r(sum(p.media_described for p in posts) / n_media, 3) if n_media else None,
            "cw_share": _r(sum(p.has_cw for p in posts) / len(posts), 3),
            "languages": dict(Counter(p.language or "unknown" for p in posts).most_common(6)),
            "top_posters": S.top_counts(authors, 5),
        }
    elif isinstance(tl, dict) and "__error__" in tl:
        tl_out["reason"] = tl["__error__"]
    out["timeline"] = tl_out

    # ---------------------------------------------------------------- trends
    trends: dict[str, Any] = {"tags": [], "statuses": [], "links": []}
    if isinstance(data.get("trend_tags"), list):
        for t in data["trend_tags"]:
            hist = t.get("history") or []
            uses = [_int(h.get("uses")) or 0 for h in hist]
            accounts = [_int(h.get("accounts")) or 0 for h in hist]
            prior = uses[1:]
            # Acceleration: today vs the average of the preceding days, +1 smoothed.
            accel = (uses[0] + 1) / ((sum(prior) / len(prior)) + 1) if prior else None
            trends["tags"].append({
                "name": t.get("name"),
                "uses_today": uses[0] if uses else None,
                "uses_7d": sum(uses),
                "accounts_7d": sum(accounts),
                "acceleration": _r(accel, 2),
            })
    if isinstance(data.get("trend_statuses"), list):
        for s in data["trend_statuses"][:5]:
            p = normalize_status(s, str(_dig(s, "account", "id")), host)
            trends["statuses"].append({
                "acct": full_acct(_dig(s, "account", "acct"), host),
                "url": p.url,
                "replies": p.replies, "reblogs": p.reblogs, "favourites": p.favourites, "quotes": p.quotes,
                "weighted": _r(weighted(p, w), 1),
                "excerpt": _excerpt(p.text, 100),
            })
    if isinstance(data.get("trend_links"), list):
        for l in data["trend_links"][:5]:
            trends["links"].append({
                "title": l.get("title"),
                "url": l.get("url"),
                "uses_7d": sum(_int(h.get("uses")) or 0 for h in l.get("history") or []),
            })
    out["trends"] = trends

    # ----------------------------------------------------------------- score
    comps: dict[str, float] = {}
    cw: dict[str, float] = {}
    if mau_ratio is not None:
        comps["engagement"], cw["engagement"] = S.saturate(mau_ratio, 0.15), 0.3
    if stickiness is not None:
        comps["stickiness"], cw["stickiness"] = min(1.0, stickiness), 0.25
    lm = act_out.get("login_momentum_pct_week")
    if lm is not None:
        comps["momentum"], cw["momentum"] = S.logistic(lm / 5.0), 0.2
    if peer_count is not None:
        comps["federation"], cw["federation"] = min(1.0, math.log10(peer_count + 1) / 5.0), 0.25
    out["score"] = {
        # Fewer than three signals (e.g. non-Mastodon software) is too thin to score honestly.
        "health_score": _r(100 * S.weighted_geometric_mean(comps, cw), 1) if len(comps) >= 3 else None,
        "components": {k: _r(v, 3) for k, v in comps.items()},
        "component_weights": cw,
    }
    return out
