from http_server import parse_http_request


def test_parse_http_request_incomplete_returns_none():
    buf = b"GET / HTTP/1.1\r\nHost: example"
    assert parse_http_request(buf) is None


def test_parse_http_request_basic_get_without_body():
    buf = b"GET /api/status HTTP/1.1\r\nHost: example\r\n\r\n"
    method, path, headers, body = parse_http_request(buf)
    assert method == "GET"
    assert path == "/api/status"
    assert b"Host: example" in headers
    assert body == b""


def test_parse_http_request_with_body_and_querystring():
    buf = (
        b"POST /api/command?foo=bar HTTP/1.1\r\n"
        b"Host: example\r\n"
        b"Content-Type: application/json\r\n"
        b"\r\n"
        b'{"command":"PWON"}'
    )
    method, path, headers, body = parse_http_request(buf)
    assert method == "POST"
    # Query string should be stripped
    assert path == "/api/command"
    assert b"Content-Type: application/json" in headers
    assert body == b'{"command":"PWON"}'

