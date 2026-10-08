"""Polite, SSRF-safe HTTP fetching.

Every hop (initial URL and each redirect) is resolved and rejected if it points at a loopback,
private, link-local, reserved or multicast address. Redirects are followed manually (max 5), bodies
are capped at 5 MB, robots.txt is honoured, hosts get at most one concurrent request, and one
Fetcher (= one company) makes at most 6 requests.
"""
import ipaddress
import socket
import threading
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "SignalsBot/1.0 (+https://mmathew--webscraper-web.modal.run)"
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_REQUESTS = 6

HTML = ("text/html", "application/xhtml+xml")
XML = ("application/xml", "text/xml", "application/rss+xml", "application/atom+xml")
JSON = ("application/json",)
TEXT = ("text/plain",)

_locks: dict = {}
_locks_guard = threading.Lock()


class FetchError(Exception):
    """A problem with the input or the target site, safe to show to the user."""


def _host_lock(host: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(host, threading.Lock())


def _blocked(ip) -> bool:
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any((ip.is_loopback, ip.is_private, ip.is_link_local, ip.is_reserved, ip.is_multicast, ip.is_unspecified))


def default_resolver(host: str, port: int) -> list:
    return [ai[4][0] for ai in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _caused_by_dns(e: BaseException) -> bool:
    while e is not None:
        if isinstance(e, socket.gaierror):
            return True
        e = e.__cause__ or e.__context__
    return False


class Fetcher:
    def __init__(self, allow_private=False, resolver=default_resolver, transport=None, timeout=15):
        # allow_private is for local test servers only; the guard itself is never weakened.
        self.allow_private = allow_private
        self.resolver = resolver
        self.requests = 0
        self._robots: dict = {}
        self._client = httpx.Client(transport=transport, timeout=timeout, follow_redirects=False)

    def close(self):
        self._client.close()

    def check_url(self, url: str) -> None:
        try:
            p = urlparse(url)
            port = p.port
        except ValueError:
            raise FetchError(f"Invalid URL: {url}")
        if p.scheme not in ("http", "https") or not p.hostname:
            raise FetchError("URL must start with http:// or https://")
        if self.allow_private:
            return
        try:
            addrs = self.resolver(p.hostname, port or (443 if p.scheme == "https" else 80))
        except (socket.gaierror, UnicodeError):
            raise FetchError(f"Couldn't resolve {p.hostname} – check the domain")
        for addr in addrs:
            if _blocked(ipaddress.ip_address(addr.split("%")[0])):
                raise FetchError(f"Refusing to fetch {p.hostname}: it points to a private or reserved address")

    def _get_once(self, url, accept):
        host = urlparse(url).hostname
        headers = {"User-Agent": USER_AGENT, "Accept": ", ".join(accept)}
        with _host_lock(host):
            try:
                with self._client.stream("GET", url, headers=headers) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        return urljoin(url, r.headers["location"]), None, None
                    if r.status_code >= 400:
                        raise FetchError(f"{host} returned HTTP {r.status_code}")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype not in accept:
                        raise FetchError(f"{host} didn't return a supported page ({ctype or 'unknown type'})")
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf += chunk
                        if len(buf) >= MAX_BYTES:
                            del buf[MAX_BYTES:]
                            break
                    try:
                        text = bytes(buf).decode(r.encoding or "utf-8", errors="replace")
                    except LookupError:
                        text = bytes(buf).decode("utf-8", errors="replace")
                    return None, text, ctype
            except FetchError:
                raise
            except httpx.TimeoutException:
                raise FetchError(f"Timed out waiting for {host}")
            except httpx.HTTPError as e:
                if _caused_by_dns(e):
                    raise FetchError(f"Couldn't resolve {host} – check the domain")
                raise FetchError(f"Couldn't reach {host}")

    def fetch(self, url: str, accept=HTML, robots=True):
        """Return (final_url, text, content_type)."""
        if self.requests >= MAX_REQUESTS:
            raise FetchError("Request limit reached for this company")
        self.requests += 1
        if robots and not self._robots_for(url).can_fetch("SignalsBot", url):
            raise FetchError(f"robots.txt on {urlparse(url).hostname} doesn't allow fetching {url}")
        for _ in range(MAX_REDIRECTS + 1):
            self.check_url(url)
            location, text, ctype = self._get_once(url, accept)
            if location is None:
                return url, text, ctype
            url = location
        raise FetchError("Too many redirects")

    def _robots_for(self, url: str) -> RobotFileParser:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            try:
                rp.parse(self.fetch(origin + "/robots.txt", accept=TEXT, robots=False)[1].splitlines())
            except FetchError:
                rp.parse([])  # missing or unreadable: allow
            self._robots[origin] = rp
        return self._robots[origin]

    def sitemaps(self, url: str) -> list:
        return self._robots_for(url).site_maps() or []
