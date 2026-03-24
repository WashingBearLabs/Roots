"""URL validation to prevent SSRF attacks."""
from __future__ import annotations
import ipaddress
import urllib.parse

BLOCKED_SCHEMES = {"file", "ftp", "gopher", "data", "javascript"}
PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

class SSRFError(ValueError):
    """Raised when a URL targets a private/internal network."""
    pass

def validate_url(url: str, *, allow_private: bool = False) -> str:
    """Validate a URL is safe for server-side requests.

    Args:
        url: The URL to validate
        allow_private: If True, skip private IP checks (for development)

    Returns:
        The validated URL string

    Raises:
        SSRFError: If the URL is unsafe
    """
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme.lower() in BLOCKED_SCHEMES:
        raise SSRFError(f"Blocked URL scheme: {parsed.scheme}")

    if not parsed.hostname:
        raise SSRFError(f"URL has no hostname: {url}")

    if not allow_private:
        try:
            ip = ipaddress.ip_address(parsed.hostname)
            for network in PRIVATE_RANGES:
                if ip in network:
                    raise SSRFError(f"URL targets private/internal network: {parsed.hostname}")
        except ValueError:
            # hostname is not an IP — could be a DNS name that resolves to private IP
            # We can't fully prevent DNS rebinding here, but block obvious cases
            hostname = parsed.hostname.lower()
            if hostname in ("localhost", "metadata.google.internal", "metadata.aws.internal"):
                raise SSRFError(f"URL targets known internal hostname: {hostname}")

    return url
