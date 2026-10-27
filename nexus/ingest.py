"""Read-only public feed ingestion. Never imports private memory or writes upstream."""
from __future__ import annotations
import html
import ipaddress
import json
import re
import socket
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlsplit
from .model import InvalidData, public_url, digest, key, stamp, instant, validate_source

FEED_ROOT = "https://raw.githubusercontent.com/tyuio7777/hunt-briefing-feed/main/"
MAX_BYTES = 4_000_000

def check_address(url):
    clean = public_url(url)
    for address in socket.getaddrinfo(urlsplit(clean).hostname, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise InvalidData("Non-public network address")
    return clean

class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_address(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch(url):
    clean = check_address(url)
    request = urllib.request.Request(clean, headers={"User-Agent": "HuntBriefing-Nexus/2.0", "Accept": "application/json,text/html,text/plain"})
    with urllib.request.build_opener(Redirects()).open(request, timeout=35) as response:
        check_address(response.url)
        if int(response.headers.get("Content-Length", "0")) > MAX_BYTES:
            raise InvalidData("Oversized source")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise InvalidData("Oversized source")
        return data.decode("utf-8-sig"), response.url

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}: self.skip += 1
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3", "tr"}: self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"}: self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)

def plain(value):
    p = TextParser(); p.feed(value or "")
    return "\n".join(" ".join(line.split()) for line in "".join(p.parts).splitlines() if line.strip())

def source(url, title, text, published, now, *, kind="briefing", origin="mixed", briefing_id="", links=None):
    url = public_url(url)
    text = text.strip()
    if not text or len(text) > 180000: raise InvalidData("Source text is empty or oversized")
    content_hash = digest(text.encode("utf-8"))
    # Versioned source IDs keep quotations anchored to the exact retrieved revision.
    row = {"id": key("source", url + "\n" + content_hash), "documentId": key("document", url), "url": url,
           "title": title[:1000], "text": text, "contentHash": content_hash, "publishedAt": stamp(instant(published)),
           "observedAt": stamp(now), "kind": kind, "origin": origin, "visibility": "public",
           "briefingId": briefing_id, "links": links or []}
    validate_source(row)
    return row

def parse_briefing(issue, document, body, now):
    if not isinstance(document.get("sections"), list): raise InvalidData("Missing sections")
    if document.get("id", document.get("date", issue["id"])) != issue["id"]: raise InvalidData("Issue mismatch")
    published = issue["published_at"]
    if instant(published) > now: raise InvalidData("Future issue")
    parts = [str(document.get("title", issue.get("title", ""))), str(document.get("summary", ""))]
    links = set()
    for section in document["sections"]:
        if not isinstance(section, dict): continue
        for item in section.get("items", [section]):
            if not isinstance(item, dict): continue
            for name in ("title", "summary", "body", "text"):
                if isinstance(item.get(name), str): parts.append(plain(item[name]))
            urls = item.get("source_urls", []) + [s.get("url", "") for s in item.get("sources", []) if isinstance(s, dict)] + [item.get("source_url", "")]
            for url in urls:
                try: links.add(public_url(url))
                except InvalidData: pass
    for event in document.get("calendar_events", []):
        if isinstance(event, dict): parts.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
    if body: parts.append(plain(body))
    text = "\n\n".join(x for x in parts if x.strip())
    if len(text) > 170000: raise InvalidData("Briefing requires explicit chunking")
    return source(issue["json_url"], issue.get("title") or issue["id"], text, published, now, briefing_id=issue["id"], links=sorted(links))

def collect(now, getter=fetch):
    """The entire published index is examined; fixed keywords do not filter inputs."""
    latest = json.loads(getter(FEED_ROOT + "feed/latest.json")[0])
    index = json.loads(getter(FEED_ROOT + "feed/index.json")[0])
    if latest.get("status") != "ready" or not isinstance(latest.get("issue"), dict):
        raise InvalidData("Upstream latest is not ready; no upstream repair is attempted")
    issues = {i["id"]: i for i in index.get("issues", [])}
    current = latest["issue"]
    if current["id"] not in issues or any(issues[current["id"]].get(k) != current.get(k) for k in ("json_url", "html_url", "published_at")):
        raise InvalidData("Upstream index/latest mismatch")
    rows, failures = [], []
    for issue in sorted(issues.values(), key=lambda i: i["published_at"]):
        try:
            if instant(issue["published_at"]) > now: continue
            for field in ("json_url", "html_url"):
                if not issue.get(field, "").startswith(FEED_ROOT + "feed/"): raise InvalidData("Unexpected feed path")
            doc = json.loads(getter(issue["json_url"])[0])
            body = getter(issue["html_url"])[0]
            rows.append(parse_briefing(issue, doc, body, now))
        except (InvalidData, ValueError, OSError, KeyError) as exc:
            failures.append({"issueId": issue.get("id", ""), "stage": "source", "error": type(exc).__name__})
    if not rows: raise InvalidData("No valid public sources; preserve previous release")
    return rows, failures
