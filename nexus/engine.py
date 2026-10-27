"""Incremental, reversible knowledge curation with independently gated publication."""
from __future__ import annotations
from copy import deepcopy
from datetime import timedelta
from . import contracts
from .model import (InvalidData, GROUPS, ENTITY_TYPES, RELATIONS, RULE_VERSION, checked_evidence,
                    clean_text, day, digest, instant, key, lifecycle, normal, stamp, validate_graph,
                    validate_source, visible_now)

def initial_state(seed):
    state = {"version": 2, "entities": {}, "assertions": {}, "relations": {}, "sources": {},
             "receipts": {}, "reviewQueue": {}, "audit": [], "redirects": {}, "vocabularyReviewedAt": ""}
    for item in seed:
        name = clean_text(item["name"], 160)
        id_ = key("topic", name)
        state["entities"][id_] = {"id": id_, "label": name, "aliases": list(dict.fromkeys(item.get("aliases", []))),
            "kind": "topic", "scope": "", "group": item.get("group", "sources"), "evidence": [], "seed": True,
            "createdAt": "", "changedAt": "", "lastMentionAt": ""}
    return state

def add_sources(state, sources):
    for s in sources:
        validate_source(s)
        previous = state["sources"].get(s["id"])
        if previous and (previous["url"] != s["url"] or previous["contentHash"] != s["contentHash"]):
            raise InvalidData("Source ID collision")
        # Re-fetching an identical document preserves its original publication and observation.
        if not previous: state["sources"][s["id"]] = deepcopy(s)

def jobs(state):
    result = []
    for s in sorted(state["sources"].values(), key=lambda x: (x["publishedAt"], x["id"])):
        text = s["text"]
        for start in range(0, len(text), 14000):
            chunk = text[max(0, start-400):start+14400]
            id_ = key("job", s["id"] + "\n" + str(start) + "\n" + RULE_VERSION)
            if id_ not in state["receipts"]:
                result.append({"id": id_, "sourceId": s["id"], "sourceHash": s["contentHash"], "offset": start,
                               "text": chunk, "publishedAt": s["publishedAt"], "title": s["title"], "url": s["url"], "kind": s["kind"], "origin": s["origin"]})
    return result

def catalog(state):
    return [{k: e[k] for k in ("id", "label", "aliases", "kind", "scope", "group")} for e in state["entities"].values() if e["id"] not in state["redirects"]]

def queue(state, key_, candidate, reason, now, job=""):
    id_ = key("review", key_)
    # Repeated failures do not reset the original review age.
    old = state["reviewQueue"].get(id_, {})
    state["reviewQueue"][id_] = {"id": id_, "jobId": job, "candidate": deepcopy(candidate), "reason": str(reason)[:1000],
                                "createdAt": old.get("createdAt", stamp(now)), "status": "pending"}

def union_evidence(a, b):
    rows = {digest(e): e for e in a + b}
    return sorted(rows.values(), key=lambda e: (e["sourceId"], e["quote"]))

def dates(candidate, sources):
    start, end = candidate.get("effectiveFrom", ""), candidate.get("effectiveTo", "")
    if start: day(start)
    if end: day(end)
    if start and end and day(end) < day(start): raise InvalidData("Inverted validity interval")
    if start or end:
        evidence = checked_evidence(candidate.get("dateEvidence"), sources)
        # Semantic review checks date interpretation; a quotation must at least contain date digits.
        for date in (start, end):
            if date and not any(re_date_month(date, e["quote"]) for e in evidence):
                raise InvalidData("Date requires explicit quoted support")
    return start, end

def re_date_month(value, quote):
    import re
    d = day(value)
    month = ['January','February','March','April','May','June','July','August','September','October','November','December'][d.month-1]
    english = rf'(?:{month}|{month[:3]}\.?)'
    return bool(re.search(rf"(?<!\d){d.year}[-/.]{d.month:02d}[-/.]{d.day:02d}(?!\d)", quote)
                or re.search(rf"(?<!\d){d.month}월\s*{d.day}일", quote)
                or re.search(rf"(?<!\d){d.month:02d}[-/.]{d.day:02d}(?!\d)", quote)
                or re.search(rf'\b{english}\s+{d.day}(?:st|nd|rd|th)?\b',quote,re.I)
                or re.search(rf'\b{d.day}(?:st|nd|rd|th)?\s+{english}\b',quote,re.I))

def resolve_entity(state, c, evidence, now):
    name, kind_, scope = clean_text(c["label"], 160), c["kind"], clean_text(c["scope"], 160, False)
    if kind_ not in ENTITY_TYPES or c["group"] not in GROUPS: raise InvalidData("Unknown entity type or group")
    aliases = list(dict.fromkeys([name] + [clean_text(a, 160) for a in c["aliases"]]))
    if not any(any(normal(a) in normal(e["quote"]) for a in aliases) for e in evidence):
        raise InvalidData("Entity is absent from quoted evidence")
    supplied = c.get("entityId", "")
    while supplied in state["redirects"]: supplied = state["redirects"][supplied]
    candidates = [e for e in state["entities"].values() if e["id"] not in state["redirects"] and
                  normal(e["scope"]) == normal(scope) and (e["seed"] or e["kind"] == kind_) and
                  normal(name) in {normal(a) for a in [e["label"]] + e["aliases"]}]
    if supplied:
        if supplied not in [e["id"] for e in candidates]: raise InvalidData("Existing entity ID does not establish same identity")
        candidates = [state["entities"][supplied]]
    if len(candidates) > 1: raise InvalidData("Ambiguous entity identity")
    entity = deepcopy(candidates[0]) if candidates else {
        "id": key("entity", kind_+"\n"+normal(name)+"\n"+normal(scope)), "label": name, "aliases": [], "kind": kind_,
        "scope": scope, "group": c["group"], "evidence": [], "seed": False, "createdAt": stamp(now), "changedAt": stamp(now), "lastMentionAt": ""}
    if any(other["id"] != entity["id"] and other["id"] not in state["redirects"] and normal(other["scope"]) == normal(scope)
           and normal(alias) in {normal(a) for a in [other["label"]] + other["aliases"]}
           for alias in aliases for other in state["entities"].values()):
        raise InvalidData("Alias overlaps another identity; review required")
    changes = not set(aliases).issubset(entity["aliases"]) or entity["seed"]
    entity.update(aliases=list(dict.fromkeys(entity["aliases"]+aliases)), evidence=union_evidence(entity["evidence"], evidence), seed=False, kind=kind_, group=c["group"])
    if changes: entity["changedAt"] = stamp(now)
    dates_ = [state["sources"][e["sourceId"]]["publishedAt"] for e in evidence]
    entity["lastMentionAt"] = max([entity["lastMentionAt"]] + dates_)
    state["entities"][entity["id"]] = entity
    return entity["id"]

def apply_package(state, job, package, now):
    """All mutations are made on a copy; callers publish only after graph validation."""
    if job["id"] in state["receipts"]: return state, False
    if package.get("jobId") != job["id"] or package.get("sourceHash") != job["sourceHash"]:
        raise InvalidData("Proposal is for a different source revision")
    extraction, review = package.get("extraction"), package.get("review")
    contracts.validate(extraction, contracts.EXTRACTION); contracts.validate(review, contracts.REVIEW)
    if package.get("reviewedExtractionHash") != digest(extraction): raise InvalidData("Review is not bound to this extraction")
    all_candidates = extraction["entities"] + extraction["assertions"] + extraction["relations"]
    keys = [c["key"] for c in all_candidates]
    if len(keys) != len(set(keys)) or any(not k or len(k)>160 for k in keys): raise InvalidData("Duplicate candidate keys")
    decisions = {d["key"]: d for d in review["decisions"]}
    if len(decisions) != len(review["decisions"]) or set(decisions) != set(keys): raise InvalidData("Review must cover every candidate exactly once")
    next_ = deepcopy(state)
    sources = {job["sourceId"]: next_["sources"][job["sourceId"]]}
    mapped, accepted = {}, []
    for collection in ("entities", "assertions", "relations"):
        for c in extraction[collection]:
            try:
                d = decisions[c["key"]]
                if not all(d[k] for k in ("supported", "identityCorrect", "temporalCorrect")):
                    raise InvalidData(clean_text(d["reason"], 1000))
                evidence = checked_evidence(c["evidence"], sources)
                if collection == "entities":
                    mapped[c["key"]] = resolve_entity(next_, c, evidence, now)
                elif collection == "assertions":
                    subject = mapped.get(c["subject"])
                    if not subject: raise InvalidData("Assertion has no accepted subject")
                    if c["applicability"] not in {"current", "historical", "unknown"} or c["certainty"] not in {"attributed", "unconfirmed"}:
                        raise InvalidData("Unsupported assertion status")
                    start, end = dates(c, sources)
                    aspect, value = clean_text(c["aspect"], 160), clean_text(c["value"], 1600)
                    statement = clean_text(c["statement"], 6000)
                    id_ = key("assertion", "\n".join((subject, normal(aspect), normal(value), start, end)))
                    old = next_["assertions"].get(id_)
                    pub = min(sources[e["sourceId"]]["publishedAt"] for e in evidence)
                    row = {"id": id_, "subject": subject, "aspect": aspect, "value": value, "statement": statement,
                        "applicability": c["applicability"], "certainty": c["certainty"], "effectiveFrom": start, "effectiveTo": end,
                        "evidence": union_evidence(old["evidence"] if old else [], evidence), "firstPublishedAt": min(pub, old["firstPublishedAt"]) if old else pub,
                        "createdAt": old["createdAt"] if old else stamp(now), "changedAt": old["changedAt"] if old else stamp(now), "state": old["state"] if old else "active"}
                    # No deletion or implicit replacement just because a newer mention exists.
                    for target in c["supersedes"]:
                        previous = next_["assertions"].get(target)
                        if not previous or previous["subject"] != subject or normal(previous["aspect"]) != normal(aspect) or previous["firstPublishedAt"] >= pub:
                            raise InvalidData("Invalid supersession target")
                    for other in next_["assertions"].values():
                        if (other["subject"] == subject and normal(other["aspect"]) == normal(aspect)
                            and normal(other["value"]) != normal(value) and other["state"] in {"active","conflicting"}
                            and other["id"] not in c["supersedes"] and c["applicability"] == other["applicability"] == "current"
                            and not (end and other["effectiveFrom"] and end < other["effectiveFrom"])
                            and not (start and other["effectiveTo"] and start > other["effectiveTo"])):
                            queue(next_, "conflict/"+"/".join(sorted([id_,other["id"]])),
                                  {"assertionIds":[id_,other["id"]]}, "같은 대상·항목의 적용 기간과 값이 달라 추가 검토가 필요합니다.", now, job["id"])
                            if normal(aspect) in {"damage","데미지","피해량","출시일","종료일","시작일","release date","end date","start date"}:
                                row["state"] = "conflicting"; other["state"] = "conflicting"
                    next_["assertions"][id_] = row
                    for target in c["supersedes"]:
                        next_["assertions"][target]["state"] = "superseded"
                        next_["assertions"][target]["supersededBy"] = id_
                    mapped[c["key"]] = id_
                else:
                    a, b = mapped.get(c["from"]), mapped.get(c["to"])
                    if not a or not b or a == b or a not in next_["entities"] or b not in next_["entities"] or c["type"] not in RELATIONS:
                        raise InvalidData("Semantic relation requires two accepted entities")
                    start, end = dates(c, sources)
                    id_ = key("relation", "\n".join((a,b,c["type"],start,end)))
                    old = next_["relations"].get(id_)
                    pub = min(sources[e["sourceId"]]["publishedAt"] for e in evidence)
                    next_["relations"][id_] = {"id": id_, "from": a, "to": b, "type": c["type"], "statement": clean_text(c["statement"], 3000),
                        "effectiveFrom": start, "effectiveTo": end, "evidence": union_evidence(old["evidence"] if old else [], evidence),
                        "firstPublishedAt": min(pub, old["firstPublishedAt"]) if old else pub, "createdAt": old["createdAt"] if old else stamp(now)}
                accepted.append(c["key"])
                review_id = key("review", job["id"]+"/"+c["key"])
                if review_id in next_["reviewQueue"]: next_["reviewQueue"][review_id]["status"] = "resolved"
            except (InvalidData, KeyError) as exc:
                queue(next_, job["id"]+"/"+c["key"], c, str(exc), now, job["id"])
    next_["receipts"][job["id"]] = {"sourceHash": job["sourceHash"], "packageHash": digest(package), "accepted": accepted,
                                      "processedAt": stamp(now), "provider": package.get("provider", "agent"), "model": package.get("model", "unspecified")}
    next_["audit"].append({"at": stamp(now), "action": "ingest", "job": job["id"], "accepted": len(accepted), "reviewed": len(keys), "packageHash": digest(package)})
    return next_, True

def node(id_, kind_, title, summary="", **kw):
    return {"id": id_, "kind": kind_, "title": title, "summary": summary, "origin": "mixed", "verification": "근거별 확인 필요", "date": "", "dateLabel": "", "url": "", "briefingId": "", "group": "sources", "aliases": [], "lifecycle": "current", "recent": False, "effectiveFrom": "", "effectiveTo": "", "changedAt": "", "evidenceRefs": [], **kw}

def project(state, now, notes=None):
    nodes, edges = {}, {}
    def add(n): nodes[n["id"]] = n
    def edge(a,b,type_,reason,evidence,**kw):
        id_ = key("edge", a+"\n"+b+"\n"+type_)
        edges[id_] = {"id": id_, "from": a, "to": b, "type": type_, "reason": reason, "evidenceRefs": sorted(set(evidence)), "recordedAt": stamp(now), "ruleVersion": RULE_VERSION, "quotations": [], **kw}
    for s in state["sources"].values():
        add(node(s["id"], "source", s["title"], "확보한 공개 자료의 저장본입니다.", url=s["url"], origin=s["origin"], date=s["publishedAt"], dateLabel="자료 발행" if s.get("dateBasis", "published")=="published" else "브리핑 수록 · 원문 발행일 미확인", briefingId=s.get("briefingId", ""), lifecycle="historical", recent=now-instant(s["publishedAt"])<=timedelta(days=30), verification="공개 브리핑" if s["kind"]=="briefing" else "원문 확보 · 주장별 검토"))
    used = {a["subject"] for a in state["assertions"].values()}
    used |= {r[k] for r in state["relations"].values() for k in ("from","to")}
    used |= {e["id"] for e in state["entities"].values() if e["evidence"]}
    for id_ in sorted(used):
        if id_ in state["redirects"]: continue
        e = state["entities"][id_]
        assertions = [a for a in state["assertions"].values() if a["subject"]==id_]
        active = [a for a in assertions if visible_now(a, now)]
        ev = [x["sourceId"] for x in e["evidence"]]
        first = min([state["sources"][x["sourceId"]]["publishedAt"] for x in e["evidence"]], default="")
        recent = bool(active) or (not assertions and bool(first) and now-instant(first)<=timedelta(days=30))
        summary = "\n".join(a["statement"] for a in sorted(active,key=lambda x:x["firstPublishedAt"],reverse=True)[:3]) or "언급 자료와 과거 이력을 확인할 수 있습니다."
        add(node(id_, "topic", e["label"], summary, group=e["group"], aliases=e["aliases"], recent=recent,
            lifecycle="current" if active else "historical", changedAt=max([a["firstPublishedAt"] for a in assertions]+[e["lastMentionAt"]]), evidenceRefs=ev, entityType=e["kind"], scope=e["scope"], verification="출처 기반 정리 · 독립 사실 검증 아님"))
        group = "group-"+e["group"]
        if group not in nodes: add(node(group,"group",GROUPS[e["group"]],"주제별 탐색 분류",group=e["group"],recent=True))
        if ev: edge(group,id_,"classified_under","정리된 대표 주제의 탐색 분류",ev)
        for ref in sorted(set(ev)): edge(id_,ref,"mentions","이 명칭을 언급한 공개 자료",[ref])
    for a in state["assertions"].values():
        if a["subject"] not in nodes: continue
        ev = sorted({e["sourceId"] for e in a["evidence"]})
        state_ = lifecycle(a, now)
        origins = {state["sources"][ref]["origin"] for ref in ev}
        origin = "rumor" if a["certainty"]=="unconfirmed" else next(iter(origins)) if len(origins)==1 else "mixed"
        add(node(a["id"],"claim" if origin=="rumor" else "fact",a["aspect"],a["statement"],group=nodes[a["subject"]]["group"],origin=origin,
            verification="미확인 주장" if origin=="rumor" else "인용·의미 검토 통과 · 출처에 귀속된 설명",date=a["firstPublishedAt"],dateLabel="최초 근거 발행",lifecycle=state_,recent=visible_now(a,now),
            effectiveFrom=a["effectiveFrom"],effectiveTo=a["effectiveTo"],changedAt=a["firstPublishedAt"],evidenceRefs=ev,quotations=a["evidence"],supersededBy=a.get("supersededBy", "")))
        edge(a["subject"],a["id"],"describes","이 대상에 관한 출처 기반 설명",ev,quotations=a["evidence"])
        for ref in ev: edge(a["id"],ref,"cites","설명을 뒷받침하는 원문 발췌",[ref],quotations=[e for e in a["evidence"] if e["sourceId"]==ref])
    for r in state["relations"].values():
        if r["from"] not in nodes or r["to"] not in nodes: continue
        ev = [e["sourceId"] for e in r["evidence"]]
        # Scope/date are part of relation identity; preserve parallel historical versions.
        id_ = r["id"]
        validity = {**r,"applicability":"current" if r["effectiveFrom"] or r["effectiveTo"] else "historical"}
        edges[id_] = {"id":id_, "from":r["from"], "to":r["to"], "type":r["type"], "reason":r["statement"],"evidenceRefs":sorted(set(ev)),
            "recordedAt":r["firstPublishedAt"],"ruleVersion":RULE_VERSION,"quotations":r["evidence"],"effectiveFrom":r["effectiveFrom"],"effectiveTo":r["effectiveTo"],
            "recent":visible_now(validity,now),"lifecycle":lifecycle(validity,now)}
    graph = {"version":2,"generatedAt":stamp(now),"nodes":sorted(nodes.values(),key=lambda n:n["id"]),"edges":sorted(edges.values(),key=lambda e:e["id"]),
             "notes":notes or [],"revision":digest({"entities":state["entities"],"assertions":state["assertions"],"relations":state["relations"]}),
             "reviewPending":sum(r["status"]=="pending" for r in state["reviewQueue"].values()),"ruleVersion":RULE_VERSION}
    return validate_graph(graph)
