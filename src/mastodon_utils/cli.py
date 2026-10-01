"""Command line entry point.

    mastodon-utils @user@server            account statistics
    mastodon-utils server.tld              server statistics
    mastodon-utils @a@x @b@y               both, plus a comparison table
    mastodon-utils --scrape @a@x @b@y      store into SQLite (repeat to build history)
    mastodon-utils --from-db @a@x          statistics from stored data, with follower history
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from . import __version__
from . import db as DB
from .account_metrics import DEFAULT_WEIGHTS, Options, compute_account_stats
from .client import ApiError, ClientPool, fetch_server, fetch_statuses, lookup_account
from .models import full_acct, normalize_status, parse_time
from .render import plural, render_account, render_comparison, render_server
from .server_metrics import compute_server_stats
from .target import Target, TargetError, parse_target

EPILOG = """\
examples:
  mastodon-utils @Gargron@mastodon.social
  mastodon-utils fosstodon.org
  mastodon-utils https://mastodon.social/@Mastodon --posts 400 --explain
  mastodon-utils @a@x.social @b@y.social           compares the accounts in a table
  mastodon-utils --scrape @a@x.social @b@y.social --db fedi.db
  mastodon-utils --scrape --file handles.txt --incremental
  mastodon-utils --from-db @a@x.social --db fedi.db
  mastodon-utils --db-list --db fedi.db

targets are detected automatically: @user@host, user@host and profile URLs are
accounts; a bare domain or https://domain is a server.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mastodon-utils",
        description="Statistics, engagement analytics and scraping for Mastodon accounts and servers.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("targets", nargs="*", metavar="TARGET", help="account handle, profile URL or server domain")
    p.add_argument("-f", "--file", action="append", default=[], metavar="PATH",
                   help="read targets from a file, one per line; # starts a comment (repeatable)")
    p.add_argument("--server", metavar="HOST", help="default server for bare usernames such as @alice")

    mode = p.add_argument_group("modes")
    mode.add_argument("-s", "--scrape", action="store_true", help="scrape the targets into the database")
    mode.add_argument("--from-db", action="store_true", help="compute account statistics from the database")
    mode.add_argument("--db-list", action="store_true", help="list what the database contains")

    data = p.add_argument_group("data")
    data.add_argument("--db", default="mastodon.db", metavar="PATH", help="SQLite database path (default: mastodon.db)")
    data.add_argument("-n", "--posts", type=int, default=200, metavar="N",
                      help="own posts to fetch per account (originals, threads and replies; boosts come along "
                           "uncounted, up to 5x this in total); 0 = entire history (default: 200)")
    data.add_argument("--days", type=float, metavar="D", help="only use posts from the last D days")
    data.add_argument("--incremental", action="store_true",
                      help="with --scrape, stop at posts already stored, re-fetching the last 7 days to refresh counts")

    out = p.add_argument_group("output")
    out.add_argument("--json", action="store_true", help="print machine-readable JSON instead of text")
    out.add_argument("--explain", action="store_true", help="print how each factor is calculated")
    out.add_argument("--top", type=int, default=5, metavar="N", help="number of top posts to list (default: 5)")
    out.add_argument("--tz", metavar="ZONE", help="time zone for posting-time analysis, e.g. Europe/Berlin "
                                                  "(default: this computer's zone)")
    out.add_argument("--no-compare", action="store_true", help="skip the comparison table for several targets")

    tune = p.add_argument_group("tuning")
    tune.add_argument("--weights", metavar="SPEC",
                      help="engagement weights, e.g. replies=3,quotes=2.5,boosts=2,favourites=1")
    tune.add_argument("--token", action="append", default=[], metavar="HOST=TOKEN",
                      help="access token for a server (repeatable); also read from MASTODON_TOKENS "
                           "as comma-separated HOST=TOKEN pairs")
    tune.add_argument("--concurrency", type=int, default=3, metavar="N", help="targets processed at once (default: 3)")
    tune.add_argument("--timeout", type=float, default=20.0, metavar="SECONDS", help="HTTP timeout (default: 20)")
    tune.add_argument("-v", "--verbose", action="store_true", help="log every HTTP request to stderr")
    p.add_argument("--version", action="version", version=f"mastodon-utils {__version__}")
    return p


def eprint(*a: Any) -> None:
    print(*a, file=sys.stderr, flush=True)


def parse_weights(spec: str | None) -> dict[str, float]:
    w = dict(DEFAULT_WEIGHTS)
    if not spec:
        return w
    aliases = {"reply": "replies", "replies": "replies", "quote": "quotes", "quotes": "quotes",
               "boost": "reblogs", "boosts": "reblogs", "reblog": "reblogs", "reblogs": "reblogs",
               "fav": "favourites", "favs": "favourites", "favourite": "favourites", "favourites": "favourites",
               "favorite": "favourites", "favorites": "favourites", "like": "favourites", "likes": "favourites"}
    for part in spec.split(","):
        if not part.strip():
            continue
        k, _, v = part.partition("=")
        key = aliases.get(k.strip().lower())
        if not key:
            raise ValueError(f"unknown weight '{k}'; use replies, quotes, boosts or favourites")
        w[key] = float(v)
    return w


def parse_tokens(items: list[str]) -> dict[str, str]:
    tokens: dict[str, str] = {}
    env = os.environ.get("MASTODON_TOKENS", "")
    for item in [x for x in env.split(",") if x.strip()] + items:
        host, sep, tok = item.partition("=")
        if not sep or not tok.strip():
            raise ValueError(f"token '{item[:12]}...' must look like HOST=TOKEN")
        tokens[host.strip().lower()] = tok.strip()
    return tokens


def resolve_tz(name: str | None) -> tzinfo:
    if name:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    env = os.environ.get("TZ")
    if env:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(env)
        except Exception:
            pass
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            from zoneinfo import ZoneInfo
            return ZoneInfo(link.split("zoneinfo/", 1)[1])
    except Exception:
        pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def read_target_files(paths: list[str]) -> list[str]:
    out: list[str] = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                for token in line.replace(",", " ").split():
                    if token.startswith("#"):
                        break  # rest of the line is a comment
                    out.append(token)
    return out


# ------------------------------------------------------------------ network work

async def account_stats(pool: ClientPool, t: Target, args, opts: Options) -> dict[str, Any]:
    acc, api_host, domain = await lookup_account(pool, t)
    since = datetime.now(timezone.utc) - timedelta(days=args.days) if args.days else None
    raw, reached_end = await fetch_statuses(pool, api_host, str(acc["id"]), args.posts or None, since)
    posts = [normalize_status(s, str(acc["id"]), domain) for s in raw]
    return compute_account_stats(acc, domain, posts, reached_end, opts)


async def server_stats(pool: ClientPool, t: Target, opts: Options) -> dict[str, Any]:
    data = await fetch_server(pool, t.host)
    return compute_server_stats(t.host, data, weights=opts.weights)


async def run_stats(targets: list[Target], args, opts: Options) -> int:
    sem = asyncio.Semaphore(max(1, args.concurrency))
    async with ClientPool(parse_tokens(args.token), timeout=args.timeout, verbose=args.verbose) as pool:
        async def one(t: Target):
            async with sem:
                if not args.json:
                    eprint(f"Fetching {t} ...")
                try:
                    if t.kind == "account":
                        return t, await account_stats(pool, t, args, opts), None
                    return t, await server_stats(pool, t, opts), None
                except ApiError as e:
                    return t, None, str(e)
        results = await asyncio.gather(*(one(t) for t in targets))
    return emit(results, args)


def emit(results: list[tuple[Target, dict | None, str | None]], args) -> int:
    failures = [(t, err) for t, st, err in results if err]
    ok = [(t, st) for t, st, err in results if st is not None]
    if args.json:
        payload = [{"target": str(t), "kind": t.kind, "stats": st} for t, st in ok]
        payload += [{"target": str(t), "kind": t.kind, "error": err} for t, err in failures]
        print(json.dumps(payload[0] if len(payload) == 1 else payload, indent=2, ensure_ascii=False, default=str))
        return 1 if failures and not ok else 0
    blocks = []
    for t, st in ok:
        blocks.append(render_account(st, args.explain) if t.kind == "account" else render_server(st, args.explain))
    if not args.no_compare:
        for kind in ("account", "server"):
            group = [st for t, st in ok if t.kind == kind]
            if len(group) > 1:
                blocks.append(render_comparison(group, kind))
    if blocks:
        print("\n\n\n".join(blocks))
    for t, err in failures:
        eprint(f"Error for {t}: {err}")
    return 1 if failures else 0


async def run_scrape(targets: list[Target], args, opts: Options) -> int:
    conn = DB.connect(args.db)
    started = DB.utcnow()
    sem = asyncio.Semaphore(max(1, args.concurrency))
    total = len(targets)
    done = {"ok": 0, "failed": 0}

    async with ClientPool(parse_tokens(args.token), timeout=args.timeout, verbose=args.verbose) as pool:
        async def one(i: int, t: Target) -> None:
            async with sem:
                prefix = f"[{i}/{total}] {t}"
                try:
                    if t.kind == "server":
                        st = await server_stats(pool, t, opts)
                        # Key by the host we queried: --from-db looks it up that way, and the instance's
                        # reported domain differs on split-domain servers (social.example.com vs example.com).
                        DB.save_server(conn, t.host, st)
                        eprint(f"{prefix}: server snapshot stored, health score {st['score'].get('health_score')}")
                    else:
                        acc, api_host, domain = await lookup_account(pool, t)
                        acct = full_acct(acc.get("acct"), domain)
                        since = None
                        if args.days:
                            since = datetime.now(timezone.utc) - timedelta(days=args.days)
                        if args.incremental:
                            newest = parse_time(DB.newest_status_time(conn, acct))
                            if newest:
                                inc = newest - timedelta(days=7)
                                since = max(since, inc) if since else inc
                        raw, _ = await fetch_statuses(pool, api_host, str(acc["id"]), args.posts or None, since)
                        posts = [normalize_status(s, str(acc["id"]), domain) for s in raw]
                        counts = DB.save_account(conn, acct, api_host, acc, raw, posts)
                        eprint(f"{prefix}: {plural(len(posts), 'post')} fetched; {counts['new']} new, {counts['updated']} "
                               f"with changed counts, {counts['unchanged']} unchanged; "
                               f"{acc.get('followers_count', 0):,} followers")
                    done["ok"] += 1
                except ApiError as e:
                    done["failed"] += 1
                    eprint(f"{prefix}: failed, {e}")

        await asyncio.gather(*(one(i, t) for i, t in enumerate(targets, 1)))
        requests = pool.total_requests

    DB.record_run(conn, started, [str(t) for t in targets], done["ok"], done["failed"])
    conn.close()
    eprint(f"Done: {done['ok']} stored, {done['failed']} failed, {requests} API requests. Database: {args.db}")
    return 0 if done["failed"] == 0 else 1


def run_from_db(targets: list[Target], args, opts: Options) -> int:
    if not os.path.exists(args.db):
        eprint(f"No database at {args.db}. Run with --scrape first.")
        return 1
    conn = DB.connect(args.db)
    results: list[tuple[Target, dict | None, str | None]] = []
    for t in targets:
        if t.kind != "account":
            row = conn.execute("SELECT stats FROM server_snapshots WHERE domain=? ORDER BY scraped_at DESC LIMIT 1",
                               (t.host,)).fetchone()
            if row:
                results.append((t, json.loads(row["stats"]), None))
            else:
                results.append((t, None, "server not in database"))
            continue
        loaded = DB.load_account(conn, t.acct)
        if not loaded:
            results.append((t, None, "account not in database; scrape it first"))
            continue
        acc, acct, raw = loaded
        domain = acct.split("@", 1)[1]
        if args.days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
            raw = [s for s in raw if (parse_time(s.get("created_at")) or cutoff) >= cutoff]
        if args.posts:
            kept, own = [], 0
            for s in raw:  # newest first; same own-post counting as the live fetch
                kept.append(s)
                own += 0 if s.get("reblog") else 1
                if own >= args.posts:
                    break
            raw = kept
        posts = [normalize_status(s, str(acc["id"]), domain) for s in raw]
        st = compute_account_stats(acc, domain, posts, True, opts)
        st["history"] = DB.follower_history(conn, acct)
        results.append((t, st, None))
    conn.close()
    return emit(results, args)


def run_db_list(args) -> int:
    if not os.path.exists(args.db):
        eprint(f"No database at {args.db}.")
        return 1
    conn = DB.connect(args.db)
    accounts, servers = DB.list_accounts(conn), DB.list_servers(conn)
    if args.json:
        print(json.dumps({"accounts": [dict(r) for r in accounts], "servers": [dict(r) for r in servers]}, indent=2))
        return 0
    print(f"Database {args.db}: {plural(len(accounts), 'account')}, {plural(len(servers), 'server')}.")
    if accounts:
        print("\nAccounts:")
        for r in accounts:
            print(f"  @{r['acct']}: {plural(r['statuses'], 'post')} stored, {plural(r['snapshots'], 'snapshot')}, "
                  f"{(r['followers'] or 0):,} followers, last scraped {r['last_scraped']}")
    if servers:
        print("\nServers:")
        for r in servers:
            print(f"  {r['domain']}: {plural(r['snapshots'], 'snapshot')}, last scraped {r['last_scraped']}")
    return 0


def _utf8_output() -> None:
    """Posts are full of emoji; a redirected stdout on Windows defaults to cp1252 and would crash on them."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if enc != "utf8" and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.db_list:
        return run_db_list(args)

    try:
        raw_targets = list(args.targets) + read_target_files(args.file)
        weights = parse_weights(args.weights)
        tz = resolve_tz(args.tz)
        parse_tokens(args.token)
    except (OSError, ValueError) as e:
        parser.error(str(e))
        return 2
    except Exception as e:  # zoneinfo errors
        parser.error(f"invalid time zone: {e}")
        return 2

    if not raw_targets:
        parser.print_help(sys.stderr)
        return 2

    targets: list[Target] = []
    seen: set[str] = set()
    bad = 0
    for raw in raw_targets:
        try:
            t = parse_target(raw, args.server)
        except TargetError as e:
            eprint(f"Skipping {raw}: {e}")
            bad += 1
            continue
        key = str(t).lower()
        if key not in seen:
            seen.add(key)
            targets.append(t)
    if not targets:
        return 2

    opts = Options(weights=weights, tz=tz, top_posts=args.top)
    try:
        if args.scrape:
            code = asyncio.run(run_scrape(targets, args, opts))
        elif args.from_db:
            code = run_from_db(targets, args, opts)
        else:
            code = asyncio.run(run_stats(targets, args, opts))
    except KeyboardInterrupt:
        eprint("Interrupted.")
        return 130
    return code or (1 if bad else 0)


if __name__ == "__main__":
    raise SystemExit(main())
