"""SSRF guard for the html_meta ownership check (review finding, ticket 0003)."""

import pytest

from app.pipeline.ownership import UnsafeFetchTarget, assert_public_host


@pytest.mark.parametrize(
    "domain",
    ["169.254.169.254", "127.0.0.1", "10.0.0.5", "[::1]", "localhost", "internal.corp"],
)
def test_private_or_internal_targets_are_refused(domain):
    private = {"localhost": ["127.0.0.1"], "internal.corp": ["10.1.2.3"]}

    def resolve(host):
        return private.get(host, ["93.184.216.34"])

    with pytest.raises(UnsafeFetchTarget):
        assert_public_host(domain, resolve=resolve)


def test_public_domain_is_allowed():
    assert_public_host("acme.example", resolve=lambda h: ["93.184.216.34"])


def test_domain_resolving_to_any_private_address_is_refused():
    with pytest.raises(UnsafeFetchTarget):
        assert_public_host(
            "mixed.example", resolve=lambda h: ["93.184.216.34", "192.168.1.1"]
        )


def test_unresolvable_domain_is_refused():
    def resolve(host):
        raise OSError("no such host")

    with pytest.raises(UnsafeFetchTarget):
        assert_public_host("nope.example", resolve=resolve)
