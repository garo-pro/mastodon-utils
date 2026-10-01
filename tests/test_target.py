import pytest

from mastodon_utils.target import TargetError, parse_target


@pytest.mark.parametrize("raw,kind,host,user", [
    ("@alice@example.social", "account", "example.social", "alice"),
    ("alice@example.social", "account", "example.social", "alice"),
    ("https://example.social/@alice", "account", "example.social", "alice"),
    ("https://example.social/@bob@other.net", "account", "other.net", "bob"),
    ("https://example.social/users/alice", "account", "example.social", "alice"),
    ("example.social/@alice", "account", "example.social", "alice"),
    ("example.social", "server", "example.social", None),
    ("https://Example.Social/", "server", "example.social", None),
    ("  mastodon.social  ", "server", "mastodon.social", None),
])
def test_detection(raw, kind, host, user):
    t = parse_target(raw)
    assert (t.kind, t.host, t.username) == (kind, host, user)


def test_bare_username_needs_default_server():
    with pytest.raises(TargetError):
        parse_target("@alice")
    with pytest.raises(TargetError):
        parse_target("alice")
    assert parse_target("@alice", "example.social").acct == "alice@example.social"
    assert parse_target("alice", "example.social").kind == "account"


@pytest.mark.parametrize("raw", ["@@", "a@", "not a host", "https://example.social/about/more"])
def test_rejects_garbage(raw):
    with pytest.raises(TargetError):
        parse_target(raw)
