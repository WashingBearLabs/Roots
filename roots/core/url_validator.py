"""URL validation to prevent SSRF attacks."""
from __future__ import annotations
import ipaddress
import socket
import urllib.parse

BLOCKED_SCHEMES = {"file", "ftp", "gopher", "data", "javascript"}
PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local incl. cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]
# Hostnames that resolve to internal services; blocked even if DNS is unavailable.
INTERNAL_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "metadata.aws.internal",
}


class SSRFError(ValueError):
    """Raised when a URL targets a private/internal network."""
    pass


def _is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in network for network in PRIVATE_RANGES)


def validate_url(url: str, *, allow_private: bool = False) -> str:
    """Validate a URL is safe for server-side requests.

    Blocks dangerous schemes and any URL whose host is — or resolves to — a
    private, loopback, link-local, or cloud-metadata address.

    Args:
        url: The URL to validate
        allow_private: If True, skip private-network checks (for development)

    Returns:
        The validated URL string

    Raises:
        SSRFError: If the URL is unsafe

    Note:
        Hostname resolution happens at validation time, which closes the common
        "DNS name pointing at a private IP" case. It does not fully defend
        against DNS rebinding (a name that resolves to a public IP here and a
        private IP at request time); that requires request-time IP pinning.
    """
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme.lower() in BLOCKED_SCHEMES:
        raise SSRFError(f"Blocked URL scheme: {parsed.scheme}")

    if not parsed.hostname:
        raise SSRFError(f"URL has no hostname: {url}")

    if allow_private:
        return url

    hostname = parsed.hostname

    # Case 1: the host is a literal IP address — check it directly.
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if _is_private(ip):
            raise SSRFError(
                f"URL targets private/internal network: {hostname}"
            )
        return url

    # Case 2: the host is a DNS name.
    if hostname.lower() in INTERNAL_HOSTNAMES:
        raise SSRFError(f"URL targets known internal hostname: {hostname}")

    # Resolve and reject if ANY resolved address is private. If the name does
    # not resolve, allow it — it cannot be connected to, so no SSRF is reachable
    # (the actual request will simply fail).
    try:
        infos = socket.getaddrinfo(
            hostname, parsed.port, proto=socket.IPPROTO_TCP
        )
    except (socket.gaierror, UnicodeError, ValueError):
        return url

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_private(ip):
            raise SSRFError(
                f"URL hostname '{hostname}' resolves to "
                f"private/internal address: {addr}"
            )

    return url
