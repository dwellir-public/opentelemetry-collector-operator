# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: node-exporter binary-url config option."""

from ops.testing import State

URL = "https://example.com/builds/node_exporter-custom-1.10.2"


def test_default_binary_url_is_empty_and_does_not_block(ctx):
    # GIVEN no explicit binary-url config
    # WHEN any hook runs the reconciler
    state_out = ctx.run(ctx.on.update_status(), State())
    # THEN the unit is not blocked over binary-url
    assert "binary-url" not in getattr(state_out.unit_status, "message", "")


def test_valid_binary_url_does_not_block(ctx):
    # GIVEN a well-formed http(s) URL
    state = State(config={"binary-url": URL})
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), state)
    # THEN the unit is not blocked over binary-url
    assert "binary-url" not in getattr(state_out.unit_status, "message", "")


def test_invalid_binary_url_blocks(ctx):
    for value in ("not-a-url", "file:///tmp/node_exporter", "ftp://example.com/node_exporter"):
        # GIVEN a URL that is not http(s)
        state = State(config={"binary-url": value})
        # WHEN the reconciler runs
        state_out = ctx.run(ctx.on.config_changed(), state)
        # THEN the unit blocks with an actionable message
        assert state_out.unit_status.name == "blocked"
        assert state_out.unit_status.message == (
            "Invalid binary-url config: must be an http(s) URL to a node_exporter executable"
        )


def test_whitespace_only_binary_url_is_treated_as_unset(ctx):
    # GIVEN a binary-url that is only whitespace
    state = State(config={"binary-url": "   "})
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), state)
    # THEN it falls back to package-type rather than blocking on a bad URL
    assert "binary-url" not in getattr(state_out.unit_status, "message", "")


def test_binary_url_takes_precedence_over_package_type(ctx):
    for package_type in ("apt", "snap"):
        # GIVEN both a binary-url and a package-type
        state = State(config={"binary-url": URL, "package-type": package_type})
        # WHEN the reconciler runs
        with ctx(ctx.on.config_changed(), state) as manager:
            charm = manager.charm
            manager.run()
        # THEN the charm resolves to the binary flavor
        assert charm._node_exporter_flavor == "binary"


def test_package_type_is_not_validated_while_binary_url_is_set(ctx):
    """package-type is ignored under a binary-url, so a stale value must not block."""
    # GIVEN a binary-url plus a package-type value that would otherwise block
    state = State(config={"binary-url": URL, "package-type": "flatpak"})
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), state)
    # THEN the unit is not blocked over package-type
    assert "package-type" not in getattr(state_out.unit_status, "message", "")


def test_flavor_falls_back_to_package_type_without_a_binary_url(ctx):
    for package_type, expected in (("apt", "apt"), ("snap", "snap")):
        # GIVEN no binary-url
        state = State(config={"package-type": package_type})
        # WHEN the reconciler runs
        with ctx(ctx.on.config_changed(), state) as manager:
            charm = manager.charm
            manager.run()
        # THEN the flavor is the configured package-type
        assert charm._node_exporter_flavor == expected
