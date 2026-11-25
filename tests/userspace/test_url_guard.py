import socket
from unittest.mock import patch

from tinyagentos.userspace.url_guard import is_safe_public_url, resolve_safe_public_ip


def test_blocks_link_local_metadata_endpoint():
    assert is_safe_public_url("http://169.254.169.254/latest/meta-data") is False


def test_blocks_loopback_and_private():
    assert is_safe_public_url("http://127.0.0.1/x") is False
    assert is_safe_public_url("http://10.0.0.1/x") is False
    assert is_safe_public_url("https://192.168.1.1/x") is False


def test_blocks_non_http_scheme():
    assert is_safe_public_url("ftp://example.com/x") is False
    assert is_safe_public_url("file:///etc/passwd") is False


def test_allows_public_ip():
    # literal public IP -- getaddrinfo returns it without DNS
    assert is_safe_public_url("https://8.8.8.8/app.taosapp") is True


def test_rejects_garbage():
    assert is_safe_public_url("not a url") is False
    assert is_safe_public_url("http://") is False


def _gai(ip):
    # mimic socket.getaddrinfo: list of (family, type, proto, canonname, sockaddr)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def test_resolve_returns_pinned_ip_for_public_literal():
    assert resolve_safe_public_ip("https://8.8.8.8/app.taosapp") == "8.8.8.8"


def test_resolve_rejects_private_and_non_http():
    assert resolve_safe_public_ip("http://169.254.169.254/latest") is None
    assert resolve_safe_public_ip("http://127.0.0.1/x") is None
    assert resolve_safe_public_ip("ftp://8.8.8.8/x") is None


def test_resolve_returns_validated_ip_for_public_hostname():
    with patch("socket.getaddrinfo", return_value=_gai("93.184.216.34")):
        assert resolve_safe_public_ip("https://example.com/x") == "93.184.216.34"
        assert is_safe_public_url("https://example.com/x") is True


def test_resolve_rejects_hostname_resolving_to_private():
    # the DNS-rebinding case: a hostname that resolves to an internal IP
    with patch("socket.getaddrinfo", return_value=_gai("10.1.2.3")):
        assert resolve_safe_public_ip("https://evil.example/x") is None
        assert is_safe_public_url("https://evil.example/x") is False


def test_resolve_rejects_mixed_public_and_private():
    # if ANY resolved address is non-public, reject the whole host
    with patch("socket.getaddrinfo", return_value=_gai("93.184.216.34") + _gai("10.0.0.1")):
        assert resolve_safe_public_ip("https://example.com/x") is None


# Regression coverage for DNS-rebind and alternate-IP-encoding SSRF vectors
# (userinfo host extraction, IPv6 literals, ULA/link-local resolution).


def test_resolve_rejects_userinfo_urls_with_blocked_hosts():
    # urlparse must use the host after @, not credentials in userinfo
    assert resolve_safe_public_ip("http://user:pass@127.0.0.1/") is None
    assert resolve_safe_public_ip("http://anything@169.254.169.254/") is None


def test_resolve_rejects_ipv6_loopback_ula_and_link_local():
    assert resolve_safe_public_ip("http://[::1]/") is None
    assert resolve_safe_public_ip("http://[fc00::1]/") is None
    assert resolve_safe_public_ip("http://[fe80::1]/") is None


def test_resolve_allows_public_ipv6_literal():
    assert resolve_safe_public_ip("http://[2606:4700:4700::1111]/") == "2606:4700:4700::1111"


def _gai6(ip):
    return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0))]


def test_resolve_rejects_hostname_resolving_to_ipv6_ula():
    with patch("socket.getaddrinfo", return_value=_gai6("fc00::dead:beef")):
        assert resolve_safe_public_ip("https://evil.example/x") is None


def test_resolve_rejects_public_first_then_private_in_result_set():
    # ordering must not matter: first public + later private still rejects
    with patch("socket.getaddrinfo", return_value=_gai("93.184.216.34") + _gai("192.168.0.1")):
        assert resolve_safe_public_ip("https://example.com/x") is None


# Coverage for #4377: the flag list alone lets RFC 6598 shared address space
# (100.64.0.0/10 -- CGNAT, and the range Tailscale assigns node addresses
# from) through, because those addresses report none of is_private /
# is_loopback / is_link_local / is_reserved / is_unspecified / is_multicast
# on Python 3.11. They do report is_global=False, so the guard also blocks
# any address that is not globally reachable.


def test_resolve_rejects_hostname_resolving_to_cgnat():
    # a hostname that resolves into 100.64.0.0/10 (e.g. a tailnet address)
    with patch("socket.getaddrinfo", return_value=_gai("100.64.0.1")):
        assert resolve_safe_public_ip("https://evil.example/x") is None
        assert is_safe_public_url("https://evil.example/x") is False


def test_resolve_rejects_cgnat_upper_edge_and_literal():
    with patch("socket.getaddrinfo", return_value=_gai("100.127.255.254")):
        assert resolve_safe_public_ip("https://evil.example/x") is None
    # literal CGNAT host -- getaddrinfo returns it without DNS
    assert resolve_safe_public_ip("http://100.64.0.1/x") is None


def test_resolve_rejects_ipv4_mapped_cgnat():
    # ::ffff:100.64.0.1 also reports is_global=False and slipped the flag list
    with patch("socket.getaddrinfo", return_value=_gai6("::ffff:100.64.0.1")):
        assert resolve_safe_public_ip("https://evil.example/x") is None


def test_resolve_allows_ordinary_public_addresses():
    with patch("socket.getaddrinfo", return_value=_gai("8.8.8.8")):
        assert resolve_safe_public_ip("https://example.com/x") == "8.8.8.8"
    with patch("socket.getaddrinfo", return_value=_gai("93.184.216.34")):
        assert resolve_safe_public_ip("https://example.com/x") == "93.184.216.34"


def test_resolve_rejects_non_global_documentation_and_benchmark_ranges():
    # TEST-NET-1/2/3 and the benchmarking range report is_global=False.
    # On Python 3.11 they are already caught by is_private, so these pin the
    # deliberate alignment of the two classifications rather than a change.
    for ip in ("192.0.2.1", "198.51.100.1", "203.0.113.1", "198.18.0.1"):
        with patch("socket.getaddrinfo", return_value=_gai(ip)):
            assert resolve_safe_public_ip("https://example.com/x") is None


def test_resolve_rejects_multicast_despite_is_global_true():
    # Multicast reports is_global=True on Python 3.11, so it is blocked by
    # is_multicast alone. This pins why the guard ADDS `not ip.is_global` to
    # the flag list instead of replacing the list with it.
    assert resolve_safe_public_ip("http://224.0.0.1/x") is None


def test_resolve_allows_globally_reachable_special_range():
    # 192.0.0.9 (PCP anycast) is special-registry space that reports
    # is_global=True and trips no flag; both classifications allow it and
    # the guard deliberately keeps allowing genuinely global addresses.
    with patch("socket.getaddrinfo", return_value=_gai("192.0.0.9")):
        assert resolve_safe_public_ip("https://example.com/x") == "192.0.0.9"
