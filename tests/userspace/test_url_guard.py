from tinyagentos.userspace.url_guard import is_safe_public_url


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
    # literal public IP — getaddrinfo returns it without DNS
    assert is_safe_public_url("https://8.8.8.8/app.taosapp") is True


def test_rejects_garbage():
    assert is_safe_public_url("not a url") is False
    assert is_safe_public_url("http://") is False
