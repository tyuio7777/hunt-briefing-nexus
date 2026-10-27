"""Nexus contracts. Source evidence, validity and display freshness stay separate."""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

VERSION = 2
RULE_VERSION = "nexus-2.0"
GROUPS = {"updates": "게임 업데이트", "events": "이벤트·일정", "equipment": "무기·특성", "world": "몬스터·세계관", "rumors": "루머·미확인 정보", "sources": "공식·커뮤니티 정보"}
ENTITY_TYPES = {"weapon", "trait", "event", "update", "character", "monster", "location", "system", "topic"}
RELATIONS = {"introduced_in", "changed_by", "part_of", "requires", "rewards", "contradicts", "supersedes", "related_to"}
LIFECYCLES = {"current", "upcoming", "ended", "historical", "unresolved", "superseded", "retracted", "conflicting"}

class InvalidData(ValueError):
    pass

def stamp(value=None):
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def instant(value):
    if not isinstance(value, str) or not value:
        raise InvalidData("Missing timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise InvalidData("Invalid timestamp") from e
    if result.tzinfo is None:
        raise InvalidData("Timestamp must include a timezone")
    return result.astimezone(timezone.utc)

def day(value):
    if isinstance(value,str) and "T" in value:
        return instant(value).astimezone(timezone(timedelta(hours=9))).date()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        raise InvalidData("Invalid calendar date")
    return datetime.strptime(value, "%Y-%m-%d").date()

def normal(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encoded(value)).hexdigest()

def key(kind, value):
    return kind + "-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

def public_url(value):
    try:
        u = urlsplit(value)
        if u.scheme != "https" or not u.hostname or u.username or u.password or u.port not in (None, 443):
            raise InvalidData("HTTPS public URL required")
        host = u.hostname.lower()
        if not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host or host.endswith((".local", ".localhost")):
            raise InvalidData("Invalid public host")
        import ipaddress
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise InvalidData("Literal addresses are not allowed")
        query = urlencode([(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}])
        return urlunsplit(("https", host, u.path or "/", query, ""))
    except (ValueError, TypeError) as e:
        raise InvalidData("Invalid public URL") from e

def clean_text(value, limit=6000, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise InvalidData("Invalid text field")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise InvalidData("Control characters are not allowed")
    return value.strip()

def lifecycle(assertion, now):
    if assertion.get("state") in {"superseded", "retracted", "conflicting"}:
        return assertion["state"]
    today = now.astimezone(timezone(timedelta(hours=9))).date()
    if assertion.get("effectiveTo") and (("T" in assertion["effectiveTo"] and instant(assertion["effectiveTo"]) <= now) or ("T" not in assertion["effectiveTo"] and day(assertion["effectiveTo"]) < today)):
        return "ended"
    if assertion.get("effectiveFrom") and (("T" in assertion["effectiveFrom"] and instant(assertion["effectiveFrom"]) > now) or ("T" not in assertion["effectiveFrom"] and day(assertion["effectiveFrom"]) > today)):
        return "upcoming"
    if assertion.get("certainty") == "unconfirmed":
        return "unresolved"
    return "current" if assertion.get("applicability") == "current" else "historical"

def visible_now(assertion, now, recent_days=30, ended_grace=7):
    state = lifecycle(assertion, now)
    if state in {"superseded", "retracted", "conflicting"}:
        return False
    today = now.astimezone(timezone(timedelta(hours=9))).date()
    if state == "upcoming":
        return (day(assertion["effectiveFrom"]) - today).days <= recent_days
    if state == "ended":
        return (today - day(assertion["effectiveTo"])).days <= ended_grace
    if state == "current":
        return True
    return now - instant(assertion["firstPublishedAt"]) <= timedelta(days=recent_days)

def validate_source(source):
    for field in ("id", "url", "title", "text", "contentHash", "publishedAt", "observedAt", "kind", "origin"):
        clean_text(source.get(field), 180000 if field == "text" else 2000)
    public_url(source["url"])
    instant(source["publishedAt"])
    instant(source["observedAt"])
    if source["contentHash"] != digest(source["text"].encode("utf-8")):
        raise InvalidData("Source hash mismatch")
    if source["kind"] not in {"briefing", "original"} or source["origin"] not in {"official", "community", "rumor", "unspecified", "mixed"}:
        raise InvalidData("Invalid provenance")
    if source.get("visibility") != "public":
        raise InvalidData("Only public sources may enter shared Nexus")

def checked_evidence(rows, sources):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        raise InvalidData("At least one bounded source quotation is required")
    result = []
    for item in rows:
        s = sources.get(item.get("sourceId"))
        quote = clean_text(item.get("quote"), 1800)
        if s is None or len(normal(quote)) < 8 or normal(quote) not in normal(s["text"]):
            raise InvalidData("Quotation is absent from supplied source")
        result.append({"sourceId": s["id"], "sourceHash": s["contentHash"], "quote": quote})
    return result

def validate_graph(graph):
    if graph.get("version") != VERSION or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise InvalidData("Unsupported graph contract")
    instant(graph.get("generatedAt"))
    nodes = {n["id"]: n for n in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]) or len(nodes) > 100000 or len(graph["edges"]) > 500000:
        raise InvalidData("Duplicate nodes or oversized graph")
    for n in nodes.values():
        for name in ("id", "kind", "title", "summary", "origin", "verification", "date", "dateLabel", "url", "briefingId", "group"):
            clean_text(n.get(name), 12000, required=name in {"id", "kind", "title"})
        if n["kind"] not in {"group", "topic", "fact", "claim", "event", "source", "briefing", "briefing_item"}:
            raise InvalidData("Unknown node kind")
        if n["url"]:
            public_url(n["url"])
        if not isinstance(n.get("aliases"), list) or any(not isinstance(a, str) for a in n["aliases"]):
            raise InvalidData("Invalid aliases")
        if n.get("lifecycle", "current") not in LIFECYCLES:
            raise InvalidData("Invalid lifecycle")
    seen = set()
    for e in graph["edges"]:
        if e["id"] in seen or e["from"] not in nodes or e["to"] not in nodes:
            raise InvalidData("Invalid graph endpoints")
        seen.add(e["id"])
        if e["type"] not in RELATIONS | {"classified_under", "mentions", "cites", "included_in", "describes"}:
            raise InvalidData("Unknown relation type")
        if not e.get("reason") or not e.get("evidenceRefs") or any(r not in nodes for r in e["evidenceRefs"]):
            raise InvalidData("Relation requires existing evidence")
        if e["type"] in RELATIONS and not e.get("quotations"):
            raise InvalidData("Semantic relations require quotations")
    return graph
