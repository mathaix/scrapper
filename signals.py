"""Marketing signals from a parsed homepage plus a few polite follow-up fetches."""
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

from fetch import JSON, XML, FetchError

log = logging.getLogger(__name__)

SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "x.com": "x",
    "twitter.com": "x",
    "github.com": "github",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "youtube.com": "youtube",
}
ORG_TYPES = {"Organization", "Corporation", "LocalBusiness", "OnlineBusiness", "NGO"}

# substring of a <script src>/<link href> URL -> tool
ASSET_RULES = {
    "google-analytics.com": "google-analytics",
    "googletagmanager.com/gtag": "google-analytics",
    "googletagmanager.com/gtm": "google-tag-manager",
    "segment.com": "segment",
    "segment.io": "segment",
    "hs-scripts.com": "hubspot",
    "hs-analytics.net": "hubspot",
    "hsforms.net": "hubspot",
    "hubspot.com": "hubspot",
    "munchkin.marketo.net": "marketo",
    "marketo.net": "marketo",
    "pi.pardot.com": "pardot",
    "pardot.com": "pardot",
    "salesforce.com": "salesforce",
    "force.com": "salesforce",
    "intercom.io": "intercom",
    "intercomcdn.com": "intercom",
    "drift.com": "drift",
    "driftt.com": "drift",
    "zdassets.com": "zendesk",
    "zendesk.com": "zendesk",
    "hotjar.com": "hotjar",
    "mixpanel.com": "mixpanel",
    "amplitude.com": "amplitude",
    "posthog.com": "posthog",
    "js.stripe.com": "stripe",
    "cdn.shopify.com": "shopify",
    "myshopify.com": "shopify",
    "/wp-content/": "wordpress",
    "/wp-includes/": "wordpress",
    "webflow.com": "webflow",
    "website-files.com": "webflow",
    "parastorage.com": "wix",
    "wixstatic.com": "wix",
    "squarespace.com": "squarespace",
    "sqspcdn.com": "squarespace",
    "cdnjs.cloudflare.com": "cloudflare",
    "cloudflareinsights.com": "cloudflare",
    "vercel.live": "vercel",
    "/_vercel/": "vercel",
    "/_next/": "nextjs",
    "connect.facebook.net": "facebook-pixel",
    "snap.licdn.com": "linkedin-insight",
    "clarity.ms": "microsoft-clarity",
    "cookiebot.com": "cookiebot",
    "calendly.com": "calendly",
    "typeform.com": "typeform",
    "fullstory.com": "fullstory",
    "heap.io": "heap",
    "crisp.chat": "crisp",
}
# substring of <meta name=generator> -> tool
GENERATOR_RULES = {
    "wordpress": "wordpress",
    "webflow": "webflow",
    "wix": "wix",
    "squarespace": "squarespace",
    "shopify": "shopify",
    "hubspot": "hubspot",
    "ghost": "ghost",
    "next.js": "nextjs",
    "gatsby": "gatsby",
    "framer": "framer",
}

ATS_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "apply.workable.com": "workable",
}
ATS_API = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs",
    "lever": "https://api.lever.co/v0/postings/{}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}",
}
CAREERS_RE = re.compile(r"\b(careers?|jobs|join us|join our team|we'?re hiring|work with us)\b", re.I)
CAREERS_PATH_RE = re.compile(r"/(careers?|jobs|join-us|work-with-us)(/|$)", re.I)
BLOG_RE = re.compile(r"/(blog|news|insights)/[^/?#]+")


def _host(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _social_key(url):
    host = _host(url)
    path = urlparse(url).path.lower()
    if path.startswith(("/share", "/intent", "/sharer")) or path in ("", "/"):
        return None
    for domain, key in SOCIAL_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return key


def _ld_nodes(blocks):
    for b in blocks:
        if isinstance(b, list):
            yield from _ld_nodes(b)
        elif isinstance(b, dict):
            yield b
            yield from _ld_nodes(b.get("@graph", []))


def _types(node):
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else [x for x in t if isinstance(x, str)]


def profile(page, final_url):
    nodes = list(_ld_nodes(page.jsonld))
    types = []
    for n in nodes:
        for t in _types(n):
            if t not in types:
                types.append(t)
    org = next((n for n in nodes if ORG_TYPES & set(_types(n))), {})
    logo = org.get("logo")
    if isinstance(logo, dict):
        logo = logo.get("url")
    logo = logo if isinstance(logo, str) and logo else page.meta.get("og:image")
    same_as = org.get("sameAs", [])
    candidates = [same_as] if isinstance(same_as, str) else [u for u in same_as if isinstance(u, str)]
    candidates += [u for u, _ in page.anchors]
    socials = dict.fromkeys(("linkedin", "x", "github", "facebook", "instagram", "youtube"))
    for u in candidates:
        key = _social_key(u)
        if key and socials[key] is None:
            socials[key] = u
    name = org.get("name") if isinstance(org.get("name"), str) else None
    return {
        "name": name or page.meta.get("og:site_name") or page.title or None,
        "description": page.meta.get("description") or page.meta.get("og:description"),
        "logo": urljoin(final_url, logo) if logo else None,
        "canonical_url": page.canonical or final_url,
        "socials": socials,
        "jsonld_types": types,
    }


def tech(page):
    found = {tool for pattern, tool in ASSET_RULES.items() for a in page.assets if pattern in a.lower()}
    gen = page.meta.get("generator", "").lower()
    found |= {tool for pattern, tool in GENERATOR_RULES.items() if pattern in gen}
    return sorted(found)


def _ats_of(url):
    host = _host(url)
    return ATS_HOSTS.get(host), (urlparse(url).path.strip("/").split("/") or [""])[0]


def _find_careers(anchors):
    for url, _ in anchors:
        if _host(url) in ATS_HOSTS:
            return url
    for url, text in anchors:
        if CAREERS_PATH_RE.search(urlparse(url).path) or CAREERS_RE.search(text):
            return url


def _count_roles(fetcher, ats, slug):
    if ats not in ATS_API or not re.fullmatch(r"[\w.-]+", slug):
        return None
    try:
        data = json.loads(fetcher.fetch(ATS_API[ats].format(slug), accept=JSON, robots=False)[1])
        jobs = data.get("jobs") if isinstance(data, dict) else data
        return len(jobs) if isinstance(jobs, list) else None
    except (FetchError, ValueError):
        return None


def hiring(fetcher, page, parse):
    out = {"careers_url": None, "ats": None, "open_roles": None}
    url = _find_careers(page.anchors)
    if not url:
        return out
    out["careers_url"] = url
    ats, slug = _ats_of(url)
    if not ats:
        try:
            final, html, _ = fetcher.fetch(url)
            sub = _find_ats(parse(html, final).anchors)
            if sub:
                ats, slug = _ats_of(sub)
        except FetchError:
            pass
    out["ats"] = ats
    if ats:
        out["open_roles"] = _count_roles(fetcher, ats, slug)
    return out


def _find_ats(anchors):
    return next((u for u, _ in anchors if _host(u) in ATS_HOSTS), None)


def _date(s):
    s = (s or "").strip()
    try:
        if re.match(r"\d{4}-\d\d-\d\d", s):
            return date.fromisoformat(s[:10])
        return parsedate_to_datetime(s).date()
    except (ValueError, TypeError):
        return None


def _local(el):
    return el.tag.rsplit("}", 1)[-1]


def _child_text(el, *names):
    for c in el:
        if _local(c) in names and c.text:
            return c.text
    return None


def _sitemap_dates(xml):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for el in root.iter():
        if _local(el) == "url":
            loc, mod = _child_text(el, "loc"), _date(_child_text(el, "lastmod"))
            if loc and mod and BLOG_RE.search(urlparse(loc.strip()).path):
                out.append(mod)
    return out


def _feed_dates(xml):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    items = [e for e in root.iter() if _local(e) in ("item", "entry")]
    dates = [_date(_child_text(i, "pubDate", "published", "updated")) for i in items]
    return [d for d in dates if d]


def activity(fetcher, page, final_url, today):
    dates, source = [], None
    origin = "{0.scheme}://{0.netloc}".format(urlparse(final_url))
    try:
        sitemap = (fetcher.sitemaps(final_url) or [origin + "/sitemap.xml"])[0]
        dates = _sitemap_dates(fetcher.fetch(sitemap, accept=XML)[1])
        source = "sitemap" if dates else None
    except FetchError:
        pass
    if not dates and page.feeds:
        try:
            dates = _feed_dates(fetcher.fetch(page.feeds[0], accept=XML)[1])
            source = "rss" if dates else None
        except FetchError:
            pass
    if not source:
        return {"latest_post_date": None, "posts_last_90d": None, "source": None}
    cutoff = today - timedelta(days=90)
    return {
        "latest_post_date": max(dates).isoformat(),
        "posts_last_90d": sum(1 for d in dates if d >= cutoff),
        "source": source,
    }


def ai_text(page, limit=20000):
    parts = [page.title, page.meta.get("description", ""), *page.headings, " ".join(" ".join(page.text).split())]
    return "\n".join(p for p in parts if p)[:limit]


def collect(fetcher, page, final_url, parse, classify=None, today=None):
    ai = None
    if classify:
        try:
            ai = classify(ai_text(page))
        except Exception:
            log.exception("AI classifier failed")
    return {
        "profile": profile(page, final_url),
        "tech": tech(page),
        "hiring": hiring(fetcher, page, parse),
        "activity": activity(fetcher, page, final_url, today or date.today()),
        "ai": ai,
    }
