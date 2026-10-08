import httpx
import pytest

from fetch import Fetcher, FetchError


def resolver(mapping):
    return lambda host, port: [mapping[host]]


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://169.254.169.254/latest", "http://10.0.0.1", "http://[::1]/",
                                 "http://[::ffff:127.0.0.1]/", "http://224.0.0.1/"])
def test_private_literals_rejected(url):
    def boom(request):
        raise AssertionError("no request should be made")

    f = Fetcher(transport=httpx.MockTransport(boom))
    with pytest.raises(FetchError, match="private or reserved"):
        f.fetch(url)


def test_redirect_to_private_rejected():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://10.0.0.1/admin"})

    f = Fetcher(resolver=resolver({"public.test": "93.184.216.34", "10.0.0.1": "10.0.0.1"}),
                transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError, match="private or reserved"):
        f.fetch("http://public.test/")
    assert seen == ["http://public.test/robots.txt", "http://public.test/"]


def test_dns_failure_message():
    import socket

    def fail(host, port):
        raise socket.gaierror("nope")

    with pytest.raises(FetchError, match=r"Couldn't resolve nope.invalid – check the domain"):
        Fetcher(resolver=fail).fetch("http://nope.invalid/")


def test_redirect_limit():
    f = Fetcher(resolver=resolver({"a.test": "93.184.216.34"}),
                transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"Location": "/x"}
                                                                       if r.url.path != "/robots.txt" else {})))
    with pytest.raises(FetchError, match="Too many redirects"):
        f.fetch("http://a.test/")


def test_robots_disallow_and_user_agent():
    agents = []

    def handler(request):
        agents.append(request.headers["user-agent"])
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n", headers={"content-type": "text/plain"})
        return httpx.Response(200, text="<p>ok</p>", headers={"content-type": "text/html"})

    f = Fetcher(resolver=resolver({"a.test": "93.184.216.34"}), transport=httpx.MockTransport(handler))
    assert f.fetch("http://a.test/")[1] == "<p>ok</p>"
    with pytest.raises(FetchError, match="robots.txt"):
        f.fetch("http://a.test/private/x")
    assert all(a.startswith("SignalsBot/1.0 (+http") for a in agents)


def test_non_html_rejected_and_size_capped():
    def handler(request):
        if request.url.path == "/img":
            return httpx.Response(200, content=b"x", headers={"content-type": "image/png"})
        return httpx.Response(200, content=b"a" * (6 * 1024 * 1024), headers={"content-type": "text/html"})

    f = Fetcher(allow_private=True, transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError, match="supported page"):
        f.fetch("http://a.test/img", robots=False)
    assert len(f.fetch("http://a.test/big", robots=False)[1]) == 5 * 1024 * 1024


def test_request_budget():
    f = Fetcher(allow_private=True, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text="x", headers={"content-type": "text/html"})))
    for _ in range(6):
        f.fetch("http://a.test/", robots=False)
    with pytest.raises(FetchError, match="Request limit"):
        f.fetch("http://a.test/", robots=False)
