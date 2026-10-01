"""Account analytics: engagement, engager, booster, activity factors and more.

Every formula is documented in FORMULAS.md; the short versions live in
render.EXPLAIN so `--explain` can print them next to each number.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from typing import Any

from . import stats as S
from .models import Post, full_acct, html_to_text, parse_time

# How much one interaction of each kind is worth. A reply costs effort and
# starts a conversation; a quote adds commentary and redistributes; a boost
# pushes the post to a new audience; a favourite is a low-effort nod.
DEFAULT_WEIGHTS = {"replies": 3.0, "quotes": 2.5, "reblogs": 2.0, "favourites": 1.0}

# Engagement grows sublinearly with audience size (bigger audiences are
# less attentive per head), so we normalise by followers ** ALPHA rather
# than dividing by followers outright. 0.7 sits in the range reported for
# social platforms and keeps small and large accounts comparable.
AUDIENCE_ALPHA = 0.7

# Typical share of each interaction type, used as a Dirichlet prior so that
# accounts with a handful of interactions don't produce extreme ratios.
MIX_PRIOR = {"replies": 0.12, "reblogs": 0.20, "favourites": 0.63, "quotes": 0.05}
MIX_PRIOR_STRENGTH = 20.0

# Engagement on a Mastodon post mostly arrives within the first day. We model
# accumulated engagement as 1 - exp(-age / TAU) and project young posts forward.
ENGAGEMENT_TAU_HOURS = 10.0
MIN_COMPLETION = 0.15  # don't extrapolate more than ~6.7x

ACTIVITY_HALF_LIFE_DAYS = 14.0
FRESHNESS_HALF_LIFE_DAYS = 7.0


@dataclass
class Options:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    tz: tzinfo = timezone.utc
    top_posts: int = 5
    now: datetime | None = None


def weighted(p: Post, w: dict[str, float]) -> float:
    return (
        p.replies * w["replies"]
        + p.quotes * w["quotes"]
        + p.reblogs * w["reblogs"]
        + p.favourites * w["favourites"]
    )


def completion(age_hours: float) -> float:
    """Expected fraction of a post's lifetime engagement it has already received."""
    return max(MIN_COMPLETION, 1.0 - math.exp(-max(0.0, age_hours) / ENGAGEMENT_TAU_HOURS))


def projected(p: Post, w: dict[str, float], now: datetime) -> float:
    age_h = (now - p.created_at).total_seconds() / 3600.0
    return weighted(p, w) / completion(age_h)


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(float(x), nd)


def _lift(posts: list[Post], values: dict[str, float], pred) -> dict[str, Any] | None:
    yes = [values[p.id] for p in posts if pred(p)]
    no = [values[p.id] for p in posts if not pred(p)]
    if len(yes) < 3 or len(no) < 3:
        return {"posts_with": len(yes), "posts_without": len(no), "lift": None}
    a, b = S.hodges_lehmann(yes) or 0.0, S.hodges_lehmann(no) or 0.0
    # +1 smoothing keeps near-zero baselines from exploding the ratio.
    return {
        "posts_with": len(yes),
        "posts_without": len(no),
        "typical_with": _r(a, 2),
        "typical_without": _r(b, 2),
        "lift": _r((a + 1.0) / (b + 1.0), 3),
    }


def account_summary(acc: dict, home_host: str, now: datetime) -> dict[str, Any]:
    created = parse_time(acc.get("created_at"))
    note, _ = html_to_text(acc.get("note"))
    return {
        "acct": full_acct(acc.get("acct"), home_host),
        "id": str(acc.get("id")),
        "display_name": acc.get("display_name") or acc.get("username"),
        "url": acc.get("url"),
        "bio": note,
        "created_at": created.isoformat() if created else None,
        "age_days": _r((now - created).total_seconds() / 86400, 1) if created else None,
        "bot": bool(acc.get("bot")),
        "locked": bool(acc.get("locked")),
        "group": bool(acc.get("group")),
        "discoverable": acc.get("discoverable"),
        "followers": int(acc.get("followers_count") or 0),
        "following": int(acc.get("following_count") or 0),
        "statuses": int(acc.get("statuses_count") or 0),
        "last_status_at": acc.get("last_status_at"),
        "fields": [
            {"name": f.get("name"), "value": html_to_text(f.get("value"))[0], "verified": bool(f.get("verified_at"))}
            for f in acc.get("fields") or []
        ],
    }


def compute_account_stats(acc: dict, home_host: str, posts: list[Post], reached_end: bool, opts: Options) -> dict[str, Any]:
    now = opts.now or datetime.now(timezone.utc)
    w = opts.weights
    info = account_summary(acc, home_host, now)
    followers = info["followers"]

    posts = sorted(posts, key=lambda p: p.created_at)
    kinds = Counter(p.kind for p in posts)
    broadcast = [p for p in posts if p.is_broadcast]
    own = [p for p in posts if p.kind != "boost"]
    out: dict[str, Any] = {"account": info}

    window_days = None
    if posts:
        window_days = max(1.0 / 24, (now - posts[0].created_at).total_seconds() / 86400)
    out["sample"] = {
        "items": len(posts),
        "originals": kinds["original"],
        "threads": kinds["thread"],
        "replies": kinds["reply"],
        "boosts": kinds["boost"],
        "window_days": _r(window_days, 2),
        "oldest": posts[0].created_at.isoformat() if posts else None,
        "newest": posts[-1].created_at.isoformat() if posts else None,
        "complete_history": reached_end,
    }

    # ---------------------------------------------------------------- engagement
    raw = [float(p.interactions) for p in broadcast]
    proj = {p.id: projected(p, w, now) for p in broadcast}
    proj_list = [proj[p.id] for p in broadcast]
    totals = {
        "replies": sum(p.replies for p in broadcast),
        "reblogs": sum(p.reblogs for p in broadcast),
        "favourites": sum(p.favourites for p in broadcast),
        "quotes": sum(p.quotes for p in broadcast),
    }
    totals["interactions"] = sum(totals.values())
    totals["weighted"] = _r(sum(weighted(p, w) for p in broadcast), 2)

    typical = S.hodges_lehmann(proj_list)
    engagement_factor = None
    if typical is not None:
        engagement_factor = 100.0 * typical / ((followers + 10) ** AUDIENCE_ALPHA)
    mean_raw = S.mean(raw)
    momentum = None
    if len(broadcast) >= 8:
        xs = [(p.created_at - broadcast[0].created_at).total_seconds() / 86400 for p in broadcast]
        ys = [math.log1p(proj[p.id]) for p in broadcast]
        slope = S.theil_sen(xs, ys)
        if slope is not None:
            momentum = (math.exp(slope * 30) - 1) * 100
    g = S.gini(raw)
    out["engagement"] = {
        "posts_analysed": len(broadcast),
        "totals": totals,
        "per_post": {
            "mean_interactions": _r(mean_raw, 2),
            "median_interactions": _r(S.median(raw), 2),
            "mean_replies": _r(S.mean([p.replies for p in broadcast]), 2),
            "mean_reblogs": _r(S.mean([p.reblogs for p in broadcast]), 2),
            "mean_favourites": _r(S.mean([p.favourites for p in broadcast]), 2),
            "mean_quotes": _r(S.mean([p.quotes for p in broadcast]), 2),
            "typical_weighted": _r(typical, 2),
            "p90_weighted": _r(S.quantile(proj_list, 0.9), 2),
        },
        "engagement_rate_pct": _r(100.0 * mean_raw / followers, 3) if (mean_raw is not None and followers) else None,
        "engagement_factor": _r(engagement_factor, 3),
        "gini": _r(g, 3),
        "consistency": _r(1 - g, 3) if g is not None else None,
        "top10_share": _r(S.top_share(raw, 0.1), 3),
        "h_index": S.h_index(raw) if raw else None,
        "momentum_pct_30d": _r(momentum, 1),
        "weights": w,
    }

    # ----------------------------------------------------------- interaction mix
    counts = {k: float(totals[k]) for k in MIX_PRIOR}
    total_i = sum(counts.values())
    smooth = S.dirichlet_smooth(counts, MIX_PRIOR, MIX_PRIOR_STRENGTH)
    out["interaction_mix"] = {
        "shares_raw": {k: _r(v / total_i, 3) if total_i else None for k, v in counts.items()},
        "shares_smoothed": {k: _r(v, 3) for k, v in smooth.items()},
        "amplification_ratio": _r(smooth["reblogs"] / smooth["favourites"], 3),
        "discussion_ratio": _r((smooth["replies"] + smooth["quotes"]) / (smooth["reblogs"] + smooth["favourites"]), 3),
        "ratio_index": _r(smooth["replies"] / smooth["favourites"], 3),
    }

    # ----------------------------------------------------------------- engager
    replies_to_others = [p for p in posts if p.kind == "reply"]
    partners = Counter(p.reply_to_acct for p in replies_to_others if p.reply_to_acct)
    eff_partners = S.effective_number(partners.values())
    reply_share_s = S.beta_smooth(len(replies_to_others), len(posts), 0.30, 10.0) if posts else None
    engager_factor = reply_share_s * S.saturate(eff_partners, 5.0) if reply_share_s is not None else None
    out["engager"] = {
        "replies_to_others": len(replies_to_others),
        "reply_share": _r(len(replies_to_others) / len(posts), 3) if posts else None,
        "reply_share_smoothed": _r(reply_share_s, 3),
        "unique_partners": len(partners),
        "effective_partners": _r(eff_partners, 2),
        "replies_per_day": _r(len(replies_to_others) / window_days, 3) if window_days else None,
        "self_thread_share": _r(kinds["thread"] / len(own), 3) if own else None,
        "engager_factor": _r(engager_factor, 3),
        "top_partners": S.top_counts(partners, 5),
    }

    # ----------------------------------------------------------------- booster
    boosts = [p for p in posts if p.kind == "boost"]
    boosted = Counter(p.boosted_acct for p in boosts if p.boosted_acct)
    self_acct = info["acct"].lower()
    self_boosts = sum(v for k, v in boosted.items() if k.lower() == self_acct)
    eff_authors = S.effective_number(boosted.values())
    boost_share_s = S.beta_smooth(len(boosts), len(posts), 0.25, 10.0) if posts else None
    booster_factor = boost_share_s * S.saturate(eff_authors, 5.0) if boost_share_s is not None else None
    out["booster"] = {
        "boosts": len(boosts),
        "boost_share": _r(len(boosts) / len(posts), 3) if posts else None,
        "boost_share_smoothed": _r(boost_share_s, 3),
        "unique_authors": len(boosted),
        "effective_authors": _r(eff_authors, 2),
        "self_boosts": self_boosts,
        "boosts_per_day": _r(len(boosts) / window_days, 3) if window_days else None,
        "booster_factor": _r(booster_factor, 3),
        "top_boosted": S.top_counts(boosted, 5),
    }

    # ---------------------------------------------------------------- activity
    act: dict[str, Any] = {}
    if posts and window_days:
        lam = math.log(2) / ACTIVITY_HALF_LIFE_DAYS
        ages = [(now - p.created_at).total_seconds() / 86400 for p in posts]
        # Exponentially weighted Poisson rate: sum of decayed events over the decayed exposure time.
        exposure = (1 - math.exp(-lam * window_days)) / lam
        ewma_rate = sum(math.exp(-lam * a) for a in ages) / exposure
        local_days = {p.created_at.astimezone(opts.tz).date() for p in posts}
        span_days = max(1, math.ceil(window_days))
        active_ratio = min(1.0, len(local_days) / span_days)
        days_since_last = max(0.0, ages[-1])  # clock skew can put the newest post slightly in the future
        freshness = 0.5 ** (days_since_last / FRESHNESS_HALF_LIFE_DAYS)
        gaps = [
            (posts[i].created_at - posts[i - 1].created_at).total_seconds() / 3600 for i in range(1, len(posts))
        ]
        local = [p.created_at.astimezone(opts.tz) for p in posts]
        hours_f = [d.hour + d.minute / 60 for d in local]
        peak, concentration = S.circular_hour(hours_f)
        hour_hist = [0] * 24
        wd_hist = [0] * 7
        for d in local:
            hour_hist[d.hour] += 1
            wd_hist[d.weekday()] += 1

        # Which 3-hour block gets the best typical engagement for broadcast posts?
        blocks: dict[int, list[float]] = defaultdict(list)
        for p in broadcast:
            blocks[p.created_at.astimezone(opts.tz).hour // 3].append(proj[p.id])
        block_stats = {
            b: S.hodges_lehmann(v) for b, v in blocks.items() if len(v) >= 3
        }
        best_block = max(block_stats, key=lambda b: block_stats[b]) if block_stats else None

        age_days = info["age_days"] or window_days
        activity_factor = S.saturate(ewma_rate, 2.0) * math.sqrt(active_ratio) * freshness
        act = {
            "posts_per_day": _r(len(posts) / window_days, 3),
            "recent_posts_per_day": _r(ewma_rate, 3),
            "lifetime_posts_per_day": _r(info["statuses"] / max(age_days, 1), 3),
            "active_day_ratio": _r(active_ratio, 3),
            "days_since_last": _r(days_since_last, 2),
            "freshness": _r(freshness, 3),
            "median_gap_hours": _r(S.median(gaps), 2),
            "burstiness": _r(S.burstiness(gaps), 3),
            "peak_hour": _r(peak, 2),
            "hour_concentration": _r(concentration, 3),
            "hours": hour_hist,
            "weekdays": wd_hist,
            "best_block": (
                {"start_hour": best_block * 3, "end_hour": best_block * 3 + 3,
                 "typical_weighted": _r(block_stats[best_block], 2), "posts": len(blocks[best_block])}
                if best_block is not None else None
            ),
            "activity_factor": _r(activity_factor, 3),
            "timezone": str(opts.tz),
        }
    out["activity"] = act

    # ---------------------------------------------------------------- audience
    following = info["following"]
    out["audience"] = {
        "follower_following_ratio": _r((followers + 1) / (following + 1), 3),
        "leverage": _r(math.log10((followers + 1) / (following + 1)), 3),
        "followers_per_post": _r(followers / info["statuses"], 3) if info["statuses"] else None,
        "followers_per_day": _r(followers / info["age_days"], 3) if info["age_days"] else None,
    }

    # ----------------------------------------------------------------- content
    content: dict[str, Any] = {}
    if own:
        n = len(own)
        tag_counter = Counter(t for p in broadcast for t in p.tags)
        tag_values: dict[str, list[float]] = defaultdict(list)
        for p in broadcast:
            for t in set(p.tags):
                tag_values[t].append(proj[p.id])
        media_types = Counter(t for p in own for t in p.media_types)
        content = {
            "media_share": _r(sum(p.has_media for p in own) / n, 3),
            "media_types": dict(media_types),
            "media_attachments": sum(media_types.values()),
            "alt_text_coverage": _r(
                sum(p.media_described for p in own) / sum(media_types.values()), 3
            ) if media_types else None,
            "poll_share": _r(sum(p.has_poll for p in own) / n, 3),
            "cw_share": _r(sum(p.has_cw for p in own) / n, 3),
            "link_share": _r(sum(p.has_link for p in own) / n, 3),
            "quote_post_share": _r(sum(p.is_quote for p in own) / n, 3),
            "question_share": _r(sum(p.is_question for p in own) / n, 3),
            "edited_share": _r(sum(p.edited for p in own) / n, 3),
            "hashtags_per_post": _r(sum(len(p.tags) for p in own) / n, 3),
            "mean_length_chars": _r(S.mean([len(p.text) for p in own]), 1),
            "median_length_chars": _r(S.median([len(p.text) for p in own]), 1),
            "languages": dict(Counter(p.language or "unknown" for p in own).most_common(6)),
            "visibility": dict(Counter(p.visibility for p in posts)),
            "top_tags": [
                [t, c, _r(S.hodges_lehmann(tag_values[t]), 2)] for t, c in tag_counter.most_common(8)
            ],
            "lift": {
                "media": _lift(broadcast, proj, lambda p: p.has_media),
                "hashtags": _lift(broadcast, proj, lambda p: bool(p.tags)),
                "links": _lift(broadcast, proj, lambda p: p.has_link),
                "content_warning": _lift(broadcast, proj, lambda p: p.has_cw),
                "questions": _lift(broadcast, proj, lambda p: p.is_question),
                "long_posts": _lift(broadcast, proj, lambda p: len(p.text) > 280),
                "polls": _lift(broadcast, proj, lambda p: p.has_poll),
                "quote_posts": _lift(broadcast, proj, lambda p: p.is_quote),
            },
        }
    out["content"] = content

    # --------------------------------------------------------------- top posts
    ranked = sorted(broadcast, key=lambda p: proj[p.id], reverse=True)[: opts.top_posts]
    out["top_posts"] = [
        {
            "url": p.url,
            "created_at": p.created_at.isoformat(),
            "replies": p.replies,
            "reblogs": p.reblogs,
            "favourites": p.favourites,
            "quotes": p.quotes,
            "weighted": _r(weighted(p, w), 2),
            "projected": _r(proj[p.id], 2),
            "excerpt": _excerpt(p.text),
        }
        for p in ranked
    ]

    # ------------------------------------------------------------------- score
    comps = {
        "reach": min(1.0, math.log10(followers + 1) / 6.0),
        "resonance": S.saturate(engagement_factor or 0.0, 3.0),
        "activity": act.get("activity_factor") or 0.0,
        "consistency": (1 - g) if g is not None else 0.0,
        # Probabilistic OR: strong if they reply a lot OR draw a lot of replies.
        "conversation": 1 - (1 - (engager_factor or 0.0)) * (1 - min(1.0, 3 * smooth["replies"])),
    }
    weights = {"reach": 0.2, "resonance": 0.3, "activity": 0.2, "consistency": 0.15, "conversation": 0.15}
    out["score"] = {
        "presence_score": _r(100 * S.weighted_geometric_mean(comps, weights), 1),
        "components": {k: _r(v, 3) for k, v in comps.items()},
        "component_weights": weights,
    }
    return out


def _excerpt(text: str, n: int = 110) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"
