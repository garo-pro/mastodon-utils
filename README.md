# mastodon-utils

[![Tests](https://github.com/garo-pro/mastodon-utils/actions/workflows/test.yml/badge.svg)](https://github.com/garo-pro/mastodon-utils/actions/workflows/test.yml)

A command-line tool for Mastodon account and server statistics. It computes engagement analytics that go beyond raw counts, and it can scrape accounts into SQLite to track them over time.

Pass it a handle or a server and it works out which you meant:

```
mastodon-utils @Gargron@mastodon.social        account statistics
mastodon-utils fosstodon.org                   server statistics
mastodon-utils @a@x.social @b@y.social         both, plus a comparison table
mastodon-utils --scrape @a@x.social @b@y.social
```

Output is plain text designed for screen readers. Each fact is on its own "Label: value" line under a section name. There are no box-drawing characters, ASCII charts or colour. Comparisons are Markdown tables. `--json` gives everything in machine-readable form.

## Install

Requires Python 3.10 or newer. The only dependency is `httpx`.

```
pip install .
# or, for development
pip install -e ".[dev]" && pytest
```

## What it detects

| Input | Treated as |
|---|---|
| `@user@host`, `user@host` | account |
| `https://host/@user`, `https://host/@user@remote`, `https://host/users/user` | account |
| `@user` or `user` together with `--server host` | account |
| `host.tld`, `https://host.tld/` | server |

Handles are resolved with WebFinger, so split-domain setups work. For example, `@garo@leons.cc` is served from `social.leons.cc`: the tool fetches from the right server but keeps the canonical handle.

## Account statistics

- **Presence score** (0 to 100): reach, resonance, activity, consistency and conversation, combined with a geometric mean.
- **Engagement**: engagement factor (audience-size-adjusted), classic engagement rate, typical weighted engagement (Hodges-Lehmann), consistency (1 − Gini), top-10% share, engagement h-index, and momentum per 30 days (Theil-Sen).
- **Interaction mix**: smoothed shares of favourites, boosts, replies and quotes, plus the amplification, discussion and ratio indices.
- **Engager factor**: how much they reply, and to how many different people (Shannon-entropy "effective partners").
- **Booster factor**: how much they boost, and how many different voices.
- **Activity factor**: recency-weighted posting rate, active-day ratio, freshness, burstiness, typical posting time (circular mean), busiest hours and weekdays, and best time to post.
- **Content**: media, polls, content warnings, links, quote posts, questions, **alt text coverage**, languages, visibility, and top hashtags with their typical engagement.
- **What helps engagement**: lift for media, hashtags, links, content warnings, questions, long posts, polls and quote posts.
- **Top posts** by weighted engagement.

## Server statistics

- **Health score**, monthly active ratio, stickiness and dormant share.
- **Weekly activity**: averages, momentum for logins, posts and registrations, posts per login, newcomer share, volatility, and a projection for the current week.
- **Federation**: peer count and top-level-domain diversity.
- **Local timeline sample**: posts per hour, voice diversity, alt text coverage and languages.
- **Trends**: trending hashtags (with acceleration), posts and links.

It also works on non-Mastodon software (Misskey, Pleroma and others) through NodeInfo, although fewer numbers are available.

Every formula and the reasoning behind it are in [FORMULAS.md](FORMULAS.md). Add `--explain` to print a one-line explanation under each number.

## Scraping

```
mastodon-utils --scrape @a@x.social @b@y.social --db fedi.db
mastodon-utils --scrape --file handles.txt --db fedi.db
mastodon-utils --scrape --file handles.txt --db fedi.db --incremental
mastodon-utils --from-db @a@x.social --db fedi.db
mastodon-utils --db-list --db fedi.db
```

The handles file takes one or more targets per line, separated by spaces or commas. `#` starts a comment. Servers in the list get a stored stats snapshot.

Each scrape adds an account snapshot. Scraping on a schedule therefore builds follower history, and `--from-db` reports growth per day and per 30 days. A status snapshot is written whenever a post's counts change, so you can study how engagement accumulates. The raw JSON is kept, so every metric can be recomputed offline.

`--incremental` stops at posts that are already stored, but still re-fetches the last 7 days so recent counts stay fresh.

| Table | Contents |
|---|---|
| `accounts` | one row per account (canonical `user@domain`), latest raw JSON |
| `account_snapshots` | followers, following and post count per scrape |
| `statuses` | every post with counts, kind, flags, tags and raw JSON |
| `status_snapshots` | counts over time, written only when they change |
| `server_snapshots` | computed server stats per scrape |
| `scrape_runs` | log of runs |
| `status_engagement` (view) | posts with interactions and weighted engagement, ready to query |

## Options

| Option | Default | Meaning |
|---|---|---|
| `-s`, `--scrape` | off | store targets in the database |
| `--from-db` | off | compute stats from stored data, including follower history |
| `--db-list` | off | list what the database contains |
| `--db PATH` | `mastodon.db` | SQLite file |
| `-f`, `--file PATH` | none | read targets from a file (repeatable) |
| `--server HOST` | none | default server for bare usernames |
| `-n`, `--posts N` | 200 | own posts to fetch (boosts ride along, up to 5x in total); 0 fetches everything |
| `--days D` | none | only posts from the last D days |
| `--incremental` | off | with `--scrape`, only fetch new posts plus the last 7 days |
| `--json` | off | JSON output |
| `--explain` | off | print how each factor works |
| `--top N` | 5 | top posts to list |
| `--tz ZONE` | system zone | time zone for posting-time analysis |
| `--weights SPEC` | `replies=3,quotes=2.5,boosts=2,favourites=1` | engagement weights |
| `--token HOST=TOKEN` | none | access token for servers that restrict their public API (repeatable; also the `MASTODON_TOKENS` environment variable) |
| `--concurrency N` | 3 | targets processed at once |
| `--no-compare` | off | skip the comparison table |
| `-v`, `--verbose` | off | log HTTP requests to stderr |

## Being a good citizen

The client reads Mastodon's `X-RateLimit-*` headers. It pauses before running out, backs off on HTTP 429, and retries server errors with exponential backoff. It sends an identifying User-Agent. Only public API endpoints are used unless you supply a token.

## License

MIT. See [LICENSE](LICENSE).
