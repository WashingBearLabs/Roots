"""Tests for SSRF URL validation (roots.core.url_validator)."""
from __future__ import annotations

import socket

import pytest

from roots.core.url_validator import SSRFError, validate_url


def _fake_getaddrinfo(ip: str):
    """Return a getaddrinfo-shaped result that resolves any host to `ip`."""

    def _resolver(host: object, port: object, *args: object, **kwargs: object):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        port_num = port if isinstance(port, int) else 0
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port_num))]

    return _resolver


class TestSchemes:
    @pytest.mark.parametrize("scheme", ["file", "ftp", "gopher", "data", "javascript"])
    def test_blocked_schemes(self, scheme: str) -> None:
        with pytest.raises(SSRFError, match="scheme"):
            validate_url(f"{scheme}://whatever/x")

    def test_no_hostname(self) -> None:
        with pytest.raises(SSRFError, match="no hostname"):
            validate_url("http:///path")


class TestLiteralIPs:
    @pytest.mark.parametrize(
        "host",
        ["10.0.0.5", "172.16.0.1", "192.168.1.1", "127.0.0.1", "169.254.169.254"],
    )
    def test_private_ipv4_blocked(self, host: str) -> None:
        with pytest.raises(SSRFError, match="private/internal network"):
            validate_url(f"http://{host}/x")

    def test_loopback_ipv6_blocked(self) -> None:
        with pytest.raises(SSRFError, match="private/internal network"):
            validate_url("http://[::1]/x")

    def test_public_ip_allowed(self) -> None:
        assert validate_url("http://8.8.8.8/x") == "http://8.8.8.8/x"


class TestHostnames:
    @pytest.mark.parametrize(
        "host", ["localhost", "metadata.google.internal", "metadata.aws.internal"]
    )
    def test_known_internal_hostnames_blocked(self, host: str) -> None:
        with pytest.raises(SSRFError, match="internal hostname"):
            validate_url(f"http://{host}/x")

    def test_hostname_resolving_to_private_is_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The core fix: a DNS name that resolves to a private IP must be rejected.
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.1.2.3"))
        with pytest.raises(SSRFError, match="resolves to"):
            validate_url("http://evil.example.com/x")

    def test_hostname_resolving_to_metadata_is_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
        with pytest.raises(SSRFError, match="resolves to"):
            validate_url("http://rebind.example.com/x")

    def test_hostname_resolving_to_public_is_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
        assert validate_url("http://example.com/x") == "http://example.com/x"

    def test_unresolvable_hostname_is_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Fail-open: cannot connect, so no SSRF is reachable.
        def _boom(*args: object, **kwargs: object):
            raise socket.gaierror("name does not resolve")

        monkeypatch.setattr(socket, "getaddrinfo", _boom)
        assert validate_url("http://nope.invalid/x") == "http://nope.invalid/x"


class TestAllowPrivate:
    def test_allow_private_bypasses_checks(self) -> None:
        assert validate_url("http://127.0.0.1/x", allow_private=True) == "http://127.0.0.1/x"
