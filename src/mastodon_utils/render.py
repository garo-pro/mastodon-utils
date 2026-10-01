"""Plain-text rendering designed to read well in a screen reader.

No box-drawing characters, no ASCII bar charts, no colour. Sections are a
name followed by a colon; each fact is one "Label: value" line so NVDA or
JAWS can move line by line. Tables (only for comparisons) are Markdown pipes.
"""

from __future__ import annotations

from typing import Any, Callable

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

EXPLAIN: dict[str, str] = {
    # account
    "presence": "Weighted geometric mean of reach, resonance, activity, consistency and conversation, 0 to 100. "
                "Geometric, so one weak dimension drags it down; no single strength can carry it.",
    "engagement_factor": "100 x typical weighted engagement per post / (followers + 10) ^ 0.7. Engagement grows "
                         "slower than audience size, so this compares small and large accounts fairly. "
                         "Around 1 is ordinary, above 3 is strong, above 10 is exceptional.",
    "engagement_rate": "Mean raw interactions per post divided by followers. The classic industry number; "
                       "naturally lower for large accounts.",
    "typical": "Hodges-Lehmann estimate (median of all pairwise averages) of weighted engagement per post. "
               "Default weights: reply 3, quote 2.5, boost 2, favourite 1 (change with --weights). Posts younger than a day are projected "
               "forward with a saturation curve so fresh posts aren't undercounted.",
    "consistency": "1 minus the Gini coefficient of engagement across posts. 1 means every post does equally "
                   "well; near 0 means a few hits carry everything.",
    "top10": "Share of all engagement that came from the best tenth of posts.",
    "h_index": "The largest h such that h posts each got at least h interactions. Rewards sustained quality "
               "over a single viral post.",
    "momentum": "Robust (Theil-Sen) trend of log engagement over time, as percent change per 30 days.",
    "mix": "Share of each interaction type, Dirichlet-smoothed toward a typical Mastodon mix so small "
           "samples don't produce extreme ratios.",
    "amplification": "Boosts per favourite. Above 0.5 means people spread the posts, not just like them.",
    "discussion": "(Replies + quotes) per (boosts + favourites). High means posts start conversations.",
    "ratio_index": "Replies per favourite. Above 1 often means posts are contentious (being ratioed).",
    "engager_factor": "Smoothed share of their timeline that is replies to others, times breadth "
                      "(1 - e^(-effective partners / 5)). 0 to 1. High means they talk with many people, "
                      "not just one.",
    "effective_partners": "exp(Shannon entropy) of who they reply to: 10 replies to 10 people is 10, "
                          "10 replies to one person is 1.",
    "booster_factor": "Smoothed share of their timeline that is boosts, times breadth of boosted authors. "
                      "0 to 1. High means an active curator of many voices.",
    "activity_factor": "Saturating recent posting rate x square root of active-day ratio x freshness. 0 to 1.",
    "recent_rate": "Exponentially weighted posts per day with a 14 day half-life, so recent weeks count most.",
    "burstiness": "Goh-Barabasi burstiness of gaps between posts: -1 clockwork regular, 0 random, "
                  "toward 1 comes in bursts.",
    "peak_hour": "Circular mean of posting times (so 23:00 and 01:00 average to midnight, not noon). "
                 "Concentration 1 means always the same time, 0 means spread around the clock.",
    "freshness": "Halves every 7 days since the last post.",
    "best_block": "Three-hour window whose broadcast posts got the best typical engagement (at least 3 posts).",
    "leverage": "log10 of followers per following. 0 is balanced, 1 means ten times more followers.",
    "lift": "Typical engagement of posts with the feature divided by posts without it, +1 smoothed. "
            "Above 1 helps, below 1 hurts. Needs at least 3 posts on each side. Correlation, not proof.",
    "alt_text": "Share of attached images, videos and audio that have a description.",
    # server
    "health": "Weighted geometric mean of engagement, stickiness, momentum and federation, 0 to 100.",
    "mau_ratio": "Monthly active users divided by all registered accounts.",
    "stickiness": "Monthly active divided by half-year active users. Near 1 means people who come stay.",
    "dormant": "Share of registered accounts not active in the last six months.",
    "login_momentum": "Theil-Sen trend of weekly logins on a log scale, as percent per week.",
    "newcomer_share": "Registrations in the last four complete weeks divided by monthly active users.",
    "volatility": "Coefficient of variation of weekly logins. Low is a steady community.",
    "voice_diversity": "Effective number of authors in the local timeline sample divided by sample size. "
                       "1 means every post is from someone different; low means a few accounts dominate.",
    "acceleration": "Today's uses divided by the average of the previous days, +1 smoothed.",
    "tld_diversity": "Effective number of top-level domains among federated peers.",
}


def num(x: Any, nd: int = 3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, int):
        return f"{x:,}"
    if isinstance(x, float):
        if x.is_integer() and abs(x) >= 1000:
            return f"{int(x):,}"
        s = f"{x:,.{nd}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(x)


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (plural_word or word + 's')}"


def pct(x: float | None, nd: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{nd}f}%"


def signed_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{'+' if x >= 0 else ''}{x:.1f}%"


def hour_label(h: float | None) -> str:
    if h is None:
        return "n/a"
    minutes = int(round(h * 60)) % (24 * 60)  # round first so 23:59.6 becomes 00:00, not 23:00
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class Writer:
    def __init__(self, explain: bool):
        self.lines: list[str] = []
        self.explain = explain

    def section(self, title: str) -> None:
        if self.lines:
            self.lines.append("")
        self.lines.append(f"{title}:")

    def line(self, text: str, key: str | None = None, indent: int = 2) -> None:
        self.lines.append(" " * indent + text)
        if self.explain and key and key in EXPLAIN:
            self.lines.append(" " * (indent + 2) + "How it works: " + EXPLAIN[key])

    def text(self) -> str:
        return "\n".join(self.lines)


def render_account(st: dict[str, Any], explain: bool = False) -> str:
    w = Writer(explain)
    a = st["account"]
    title = f"@{a['acct']}"
    if a.get("display_name"):
        title += f" ({a['display_name']})"
    w.lines.append(title)
    w.line(f"Profile: {a['url']}")
    if a.get("bio"):
        w.line("Bio: " + " ".join(a["bio"].split())[:300])
    if a.get("created_at"):
        w.line(f"Joined: {a['created_at'][:10]}, {num(a['age_days'], 0)} days ago")
    w.line(f"Followers: {num(a['followers'])}. Following: {num(a['following'])}. Posts: {num(a['statuses'])}.")
    flags = [n for n, on in (("bot", a["bot"]), ("locked", a["locked"]), ("group", a["group"])) if on]
    if flags:
        w.line("Flags: " + ", ".join(flags))
    for f in a.get("fields") or []:
        w.line(f"{f['name']}: {f['value']}{' (verified)' if f['verified'] else ''}")

    s = st["sample"]
    w.section("Sample")
    if not s["items"]:
        w.line("No public posts could be fetched, so only profile numbers are available.")
        return w.text()
    w.line(
        f"Analysed {num(s['items'])} items over {num(s['window_days'], 1)} days: {plural(s['originals'], 'original')}, "
        f"{plural(s['threads'], 'thread post')}, {plural(s['replies'], 'reply', 'replies')} to others, "
        f"{plural(s['boosts'], 'boost')}."
    )
    if not s["complete_history"]:
        w.line("This is the most recent slice of their history; raise --posts or use --days to cover more.")

    sc = st["score"]
    w.section("Presence score")
    w.line(f"Score: {num(sc['presence_score'], 1)} out of 100", "presence")
    c = sc["components"]
    w.line(
        f"Components: reach {num(c['reach'])}, resonance {num(c['resonance'])}, activity {num(c['activity'])}, "
        f"consistency {num(c['consistency'])}, conversation {num(c['conversation'])}"
    )

    e = st["engagement"]
    w.section(f"Engagement, from {e['posts_analysed']} originals and thread posts")
    if e["posts_analysed"]:
        pp = e["per_post"]
        w.line(f"Engagement factor: {num(e['engagement_factor'])}", "engagement_factor")
        w.line(f"Engagement rate: {num(e['engagement_rate_pct'])}% of followers per post", "engagement_rate")
        w.line(f"Typical weighted engagement per post: {num(pp['typical_weighted'], 2)} "
               f"(90th percentile {num(pp['p90_weighted'], 1)})", "typical")
        w.line(f"Average per post: {num(pp['mean_favourites'], 2)} favourites, {num(pp['mean_reblogs'], 2)} boosts, "
               f"{num(pp['mean_replies'], 2)} replies, {num(pp['mean_quotes'], 2)} quotes")
        w.line(f"Median interactions per post: {num(pp['median_interactions'], 1)}; mean {num(pp['mean_interactions'], 1)}")
        t = e["totals"]
        w.line(f"Totals: {num(t['favourites'])} favourites, {num(t['reblogs'])} boosts, {num(t['replies'])} replies, "
               f"{num(t['quotes'])} quotes")
        w.line(f"Consistency: {num(e['consistency'])} (Gini {num(e['gini'])})", "consistency")
        w.line(f"Top 10 percent of posts bring {pct(e['top10_share'])} of engagement", "top10")
        w.line(f"Engagement h-index: {num(e['h_index'])}", "h_index")
        w.line(f"Momentum: {signed_pct(e['momentum_pct_30d'])} per 30 days", "momentum")
    else:
        w.line("No originals or thread posts in the sample.")

    m = st["interaction_mix"]
    w.section("Interaction mix")
    sm = m["shares_smoothed"]
    w.line(f"Favourites {pct(sm['favourites'])}, boosts {pct(sm['reblogs'])}, replies {pct(sm['replies'])}, "
           f"quotes {pct(sm['quotes'])}", "mix")
    w.line(f"Amplification ratio: {num(m['amplification_ratio'])}", "amplification")
    w.line(f"Discussion ratio: {num(m['discussion_ratio'])}", "discussion")
    w.line(f"Ratio index: {num(m['ratio_index'])}", "ratio_index")

    g = st["engager"]
    w.section("Engager, how they talk to others")
    w.line(f"Engager factor: {num(g['engager_factor'])}", "engager_factor")
    w.line(f"Replies to others: {g['replies_to_others']} ({pct(g['reply_share'])} of their timeline), "
           f"{num(g['replies_per_day'], 2)} per day")
    w.line(f"Conversation partners: {g['unique_partners']} unique, {num(g['effective_partners'], 1)} effective",
           "effective_partners")
    w.line(f"Self-thread share of own posts: {pct(g['self_thread_share'])}")
    if g["top_partners"]:
        w.line("Most replied to: " + ", ".join(f"@{k} ({v})" for k, v in g["top_partners"]))

    b = st["booster"]
    w.section("Booster, how they amplify others")
    w.line(f"Booster factor: {num(b['booster_factor'])}", "booster_factor")
    w.line(f"Boosts: {b['boosts']} ({pct(b['boost_share'])} of their timeline), {num(b['boosts_per_day'], 2)} per day")
    w.line(f"Boosted authors: {b['unique_authors']} unique, {num(b['effective_authors'], 1)} effective")
    if b["self_boosts"]:
        w.line(f"Self-boosts: {b['self_boosts']}")
    if b["top_boosted"]:
        w.line("Most boosted: " + ", ".join(f"@{k} ({v})" for k, v in b["top_boosted"]))

    act = st["activity"]
    if act:
        w.section(f"Activity, times in {act['timezone']}")
        w.line(f"Activity factor: {num(act['activity_factor'])}", "activity_factor")
        w.line(f"Recent posts per day: {num(act['recent_posts_per_day'], 2)}", "recent_rate")
        w.line(f"Posts per day over the sample: {num(act['posts_per_day'], 2)}; lifetime average "
               f"{num(act['lifetime_posts_per_day'], 2)}")
        w.line(f"Active on {pct(act['active_day_ratio'], 0)} of days in the sample")
        w.line(f"Last post: {num(act['days_since_last'], 1)} days ago; freshness {num(act['freshness'])}", "freshness")
        w.line(f"Median gap between posts: {num(act['median_gap_hours'], 1)} hours")
        w.line(f"Burstiness: {num(act['burstiness'])}", "burstiness")
        w.line(f"Typical posting time: {hour_label(act['peak_hour'])}, concentration {num(act['hour_concentration'])}",
               "peak_hour")
        hours = act["hours"]
        top_hours = sorted(range(24), key=lambda h: hours[h], reverse=True)[:3]
        w.line("Busiest hours: " + ", ".join(f"{h:02d}:00 ({plural(hours[h], 'post')})" for h in top_hours if hours[h]))
        wd = act["weekdays"]
        w.line("By weekday: " + ", ".join(f"{WEEKDAYS[i]} {wd[i]}" for i in range(7)))
        bb = act.get("best_block")
        if bb:
            w.line(f"Best time to post: {bb['start_hour']:02d}:00 to {bb['end_hour']:02d}:00, typical weighted "
                   f"engagement {num(bb['typical_weighted'], 1)} over {plural(bb['posts'], 'post')}", "best_block")

    au = st["audience"]
    w.section("Audience")
    w.line(f"Follower to following ratio: {num(au['follower_following_ratio'], 2)}; leverage {num(au['leverage'])}",
           "leverage")
    w.line(f"Followers per post written: {num(au['followers_per_post'])}; followers gained per day on average: "
           f"{num(au['followers_per_day'])}")

    ct = st["content"]
    if ct:
        w.section("Content")
        w.line(f"With media: {pct(ct['media_share'])}; polls {pct(ct['poll_share'])}; content warnings "
               f"{pct(ct['cw_share'])}; links {pct(ct['link_share'])}; quote posts {pct(ct['quote_post_share'])}; "
               f"questions {pct(ct['question_share'])}; edited {pct(ct['edited_share'])}")
        if ct.get("media_attachments"):
            w.line(f"Alt text coverage: {pct(ct['alt_text_coverage'], 0)} of {ct['media_attachments']} attachments",
                   "alt_text")
        w.line(f"Length: mean {num(ct['mean_length_chars'], 0)} characters, median {num(ct['median_length_chars'], 0)}")
        w.line(f"Hashtags per post: {num(ct['hashtags_per_post'], 2)}")
        w.line("Languages: " + ", ".join(f"{k} {v}" for k, v in ct["languages"].items()))
        w.line("Visibility: " + ", ".join(f"{k} {v}" for k, v in ct["visibility"].items()))
        if ct["top_tags"]:
            w.line("Top hashtags: " + ", ".join(
                f"#{t} ({plural(c, 'post')}, typical {num(v, 1)})" for t, c, v in ct["top_tags"]))
        lifts = [(k, v) for k, v in ct["lift"].items() if v and v.get("lift") is not None]
        if lifts:
            w.section("What helps engagement")
            names = {"media": "Media", "hashtags": "Hashtags", "links": "Links", "content_warning": "Content warnings",
                     "questions": "Asking questions", "long_posts": "Posts over 280 characters", "polls": "Polls",
                     "quote_posts": "Quote posts"}
            for i, (k, v) in enumerate(sorted(lifts, key=lambda kv: kv[1]["lift"], reverse=True)):
                w.line(f"{names.get(k, k)}: lift {num(v['lift'], 2)} ({plural(v['posts_with'], 'post')} with, "
                       f"{v['posts_without']:,} without)", "lift" if i == 0 else None)

    if st["top_posts"]:
        w.section("Top posts")
        for i, p in enumerate(st["top_posts"], 1):
            w.line(f"{i}. {p['created_at'][:10]}: {plural(p['favourites'], 'favourite')}, {plural(p['reblogs'], 'boost')}, "
                   f"{plural(p['replies'], 'reply', 'replies')}, {plural(p['quotes'], 'quote')}; weighted {num(p['weighted'], 1)}")
            if p["excerpt"]:
                w.line(p["excerpt"], indent=5)
            w.line(p["url"], indent=5)
    if st.get("history"):
        render_history(w, st["history"])
    return w.text()


def render_history(w: Writer, h: dict[str, Any]) -> None:
    w.section(f"Follower history from the database, {plural(h['snapshots'], 'snapshot')}")
    w.line(f"From {h['first'][:10]} to {h['last'][:10]}: followers {num(h['followers_start'])} to "
           f"{num(h['followers_end'])} ({'+' if h['followers_change'] >= 0 else ''}{num(h['followers_change'])})")
    if h.get("followers_per_day") is not None:
        w.line(f"Growth: {num(h['followers_per_day'], 2)} followers per day, "
               f"{signed_pct(h.get('growth_pct_30d'))} per 30 days")


def render_server(st: dict[str, Any], explain: bool = False) -> str:
    w = Writer(explain)
    s = st["server"]
    w.lines.append(f"{s['domain']}" + (f" ({s['title']})" if s.get("title") and s["title"] != s["domain"] else ""))
    w.line(f"Software: {s.get('software') or 'unknown'} {s.get('version') or ''}".rstrip())
    if s.get("description"):
        w.line("Description: " + " ".join(s["description"].split())[:300])
    reg = "open" if s.get("registrations_open") else "closed"
    if s.get("registrations_open") and s.get("approval_required"):
        reg = "open, with approval"
    w.line(f"Registrations: {reg}")
    if s.get("languages"):
        w.line("Languages: " + ", ".join(s["languages"]))
    if s.get("contact") or s.get("contact_email"):
        w.line("Contact: " + ", ".join(x for x in (s.get("contact"), s.get("contact_email")) if x))
    w.line(f"Rules: {s['rules']}; max post length: {num(s.get('max_post_chars'))} characters; "
           f"custom emoji: {num(s.get('custom_emojis'))}")

    sc = st["score"]
    w.section("Health score")
    if sc.get("health_score") is not None:
        w.line(f"Score: {num(sc['health_score'], 1)} out of 100", "health")
        w.line("Components: " + ", ".join(f"{k} {num(v)}" for k, v in sc["components"].items()))
    else:
        w.line("Not enough public data for a health score; it needs at least three of: active-user "
               "ratio, stickiness, weekly activity, federation.")

    u = st["users"]
    w.section("Users")
    w.line(f"Registered: {num(u['total'])}; active this month: {num(u['active_month'])}; "
           f"active in six months: {num(u['active_halfyear'])}")
    w.line(f"Monthly active ratio: {pct(u['mau_ratio'], 2)}", "mau_ratio")
    w.line(f"Stickiness: {num(u['stickiness'])}", "stickiness")
    w.line(f"Dormant accounts: {pct(u['dormant_share'])}", "dormant")
    w.line(f"Local posts: {num(u['local_posts'])}; posts per registered user: {num(u['posts_per_user'], 1)}")

    a = st["activity"]
    if a.get("available"):
        w.section(f"Weekly activity, {len(a['weeks'])} complete weeks")
        w.line(f"Average per week: {num(a['avg_weekly_statuses'], 0)} posts, {num(a['avg_weekly_logins'], 0)} logins, "
               f"{num(a['avg_weekly_registrations'], 0)} registrations")
        w.line(f"Posts per login: {num(a['posts_per_login'], 2)}")
        w.line(f"Login momentum: {signed_pct(a['login_momentum_pct_week'])} per week", "login_momentum")
        w.line(f"Post momentum: {signed_pct(a['status_momentum_pct_week'])} per week; registration momentum "
               f"{signed_pct(a['registration_momentum_pct_week'])} per week")
        w.line(f"Newcomer share of monthly actives: {pct(a['newcomer_share_of_mau'], 2)}", "newcomer_share")
        w.line(f"Login volatility: {num(a['login_volatility'])}", "volatility")
        cur = a["current_week_so_far"]
        proj = a.get("current_week_projected")
        text = f"This week so far: {num(cur['statuses'])} posts, {num(cur['logins'])} logins"
        if proj:
            text += f"; projected {num(proj['statuses'])} posts, {num(proj['logins'])} logins"
        w.line(text)
        w.line("Recent weeks, newest first:")
        for r in reversed(a["weeks"][-6:]):
            w.line(f"Week of {r['week_start']}: {num(r['statuses'])} posts, {num(r['logins'])} logins, "
                   f"{plural(r['registrations'], 'registration')}", indent=4)

    f = st["federation"]
    w.section("Federation")
    w.line(f"Federated peers: {num(f['peers'])}; known domains: {num(f['known_domains'])}")
    if f.get("peer_tld_diversity") is not None:
        w.line(f"Peer domain diversity: {num(f['peer_tld_diversity'], 1)} effective top-level domains", "tld_diversity")
        w.line("Most common: " + ", ".join(f".{k} ({num(v)})" for k, v in f["top_peer_tlds"]))

    t = st["timeline"]
    w.section("Local timeline sample")
    if t.get("available"):
        w.line(f"{t['sample_size']} recent posts over {num(t['span_hours'], 1)} hours: "
               f"{num(t['posts_per_hour'], 1)} posts per hour")
        w.line(f"Authors: {t['unique_authors']} unique, {num(t['effective_authors'], 1)} effective; "
               f"voice diversity {num(t['voice_diversity'])}", "voice_diversity")
        w.line(f"Typical weighted engagement: {num(t['typical_weighted_engagement'], 2)}")
        w.line(f"Replies {pct(t['reply_share'])}, media {pct(t['media_share'])}, content warnings {pct(t['cw_share'])}")
        if t.get("alt_text_coverage") is not None:
            w.line(f"Alt text coverage: {pct(t['alt_text_coverage'], 0)}", "alt_text")
        w.line("Languages: " + ", ".join(f"{k} {v}" for k, v in t["languages"].items()))
    else:
        w.line("Not available" + (f": {t['reason']}" if t.get("reason") else "")
               + ". Some servers require sign-in for the public timeline; pass --token.")

    tr = st["trends"]
    if tr["tags"]:
        w.section("Trending hashtags")
        for i, tag in enumerate(tr["tags"], 1):
            w.line(f"{i}. #{tag['name']}: {num(tag['uses_today'])} uses today, {num(tag['uses_7d'])} this week by "
                   f"{num(tag['accounts_7d'])} accounts; acceleration {num(tag['acceleration'], 2)}",
                   "acceleration" if i == 1 else None)
    if tr["statuses"]:
        w.section("Trending posts")
        for i, p in enumerate(tr["statuses"], 1):
            w.line(f"{i}. @{p['acct']}: {plural(p['favourites'], 'favourite')}, {plural(p['reblogs'], 'boost')}, "
                   f"{plural(p['replies'], 'reply', 'replies')}")
            if p["excerpt"]:
                w.line(p["excerpt"], indent=5)
            w.line(p["url"], indent=5)
    if tr["links"]:
        w.section("Trending links")
        for i, l in enumerate(tr["links"], 1):
            w.line(f"{i}. {l['title']} ({num(l['uses_7d'])} shares this week)")
            w.line(l["url"], indent=5)
    if st.get("unavailable_endpoints"):
        w.section("Notes")
        w.line("Unavailable on this server: " + ", ".join(st["unavailable_endpoints"]))
    return w.text()


# ---------------------------------------------------------------- comparisons

def _get(d: dict, path: str) -> Any:
    for k in path.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


ACCOUNT_ROWS: list[tuple[str, str, Callable[[Any], str]]] = [
    ("Followers", "account.followers", num),
    ("Presence score", "score.presence_score", lambda x: num(x, 1)),
    ("Engagement factor", "engagement.engagement_factor", num),
    ("Engagement rate", "engagement.engagement_rate_pct", lambda x: "n/a" if x is None else f"{num(x)}%"),
    ("Typical weighted engagement", "engagement.per_post.typical_weighted", lambda x: num(x, 1)),
    ("Consistency", "engagement.consistency", num),
    ("Engagement h-index", "engagement.h_index", num),
    ("Momentum per 30 days", "engagement.momentum_pct_30d", signed_pct),
    ("Amplification ratio", "interaction_mix.amplification_ratio", num),
    ("Engager factor", "engager.engager_factor", num),
    ("Booster factor", "booster.booster_factor", num),
    ("Activity factor", "activity.activity_factor", num),
    ("Recent posts per day", "activity.recent_posts_per_day", lambda x: num(x, 2)),
    ("Alt text coverage", "content.alt_text_coverage", lambda x: pct(x, 0)),
]

SERVER_ROWS: list[tuple[str, str, Callable[[Any], str]]] = [
    ("Health score", "score.health_score", lambda x: num(x, 1)),
    ("Registered users", "users.total", num),
    ("Monthly active", "users.active_month", num),
    ("Monthly active ratio", "users.mau_ratio", lambda x: pct(x, 2)),
    ("Stickiness", "users.stickiness", num),
    ("Login momentum per week", "activity.login_momentum_pct_week", signed_pct),
    ("Posts per login", "activity.posts_per_login", num),
    ("Federated peers", "federation.peers", num),
    ("Voice diversity", "timeline.voice_diversity", num),
    ("Version", "server.version", str),
]


def render_comparison(results: list[dict[str, Any]], kind: str) -> str:
    if kind == "account":
        rows, names = ACCOUNT_ROWS, [f"@{r['account']['acct']}" for r in results]
    else:
        rows, names = SERVER_ROWS, [r["server"]["domain"] for r in results]
    out = [f"Comparison of {len(results)} {'accounts' if kind == 'account' else 'servers'}:", "",
           "| Metric | " + " | ".join(names) + " |",
           "|---|" + "---|" * len(names)]
    for label, path, fmt in rows:
        cells = [fmt(_get(r, path)) if _get(r, path) is not None else "n/a" for r in results]
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(out)
