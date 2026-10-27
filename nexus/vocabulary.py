"""Weekly vocabulary audit; legacy dictionaries are never written here."""
from copy import deepcopy
from .model import InvalidData, GROUPS, checked_evidence, normal, stamp, digest
from .engine import queue, union_evidence

def apply_review(state, report, now, *, decisions=None):
    from .contracts import VOCABULARY, validate
    validate(report, VOCABULARY)
    updated = deepcopy(state)
    for candidate in report["decisions"]:
        id_, target = candidate["entityId"], candidate["targetId"]
        change_id = digest(candidate)
        try:
            entity = updated["entities"].get(id_)
            if entity is None: raise InvalidData("Vocabulary identity does not exist")
            if candidate["action"] == "keep": continue
            evidence = checked_evidence(candidate["evidence"], updated["sources"])
            action = candidate["action"]
            # Destructive identity changes always remain reviewable and reversible.
            # Approvals name the exact proposal hash; a later edited proposal cannot reuse them.
            approved = (decisions or {}).get(change_id) == "approve"
            if action == "alias":
                alias = candidate["alias"].strip()
                if not alias or len(alias)>160 or not any(normal(alias) in normal(e["quote"]) and
                    any(normal(a) in normal(e["quote"]) for a in [entity["label"]]+entity["aliases"]) for e in evidence):
                    raise InvalidData("Alias and known identity need joint evidence")
                conflicts = [e for e in updated["entities"].values() if e["id"]!=id_ and normal(alias) in {normal(a) for a in [e["label"]]+e["aliases"]}]
                if conflicts: raise InvalidData("Alias conflicts with another identity")
                # Candidate evidence alone cannot establish equivalence; require an explicit second review.
                if not approved: raise InvalidData("Alias equivalence awaits a second review")
                before = deepcopy(entity)
                entity["aliases"] = list(dict.fromkeys(entity["aliases"]+[alias]))
                entity["evidence"] = union_evidence(entity["evidence"], evidence)
            elif action == "reclassify":
                if candidate["group"] not in GROUPS: raise InvalidData("Unknown group")
                if not approved: raise InvalidData("Classification change awaits review")
                before = deepcopy(entity); entity["group"] = candidate["group"]
            elif action == "merge":
                other = updated["entities"].get(target)
                if not other or id_==target or id_ in updated["redirects"] or target in updated["redirects"]:
                    raise InvalidData("Invalid merge identities")
                if entity["kind"] != other["kind"] or normal(entity["scope"]) != normal(other["scope"]):
                    raise InvalidData("Different types or event editions cannot merge")
                if not approved: raise InvalidData("Identity merge awaits review")
                before = {"entity":deepcopy(entity),"target":deepcopy(other),"assertions":deepcopy(updated["assertions"]),"relations":deepcopy(updated["relations"])}
                other["aliases"] = list(dict.fromkeys(other["aliases"]+[entity["label"]]+entity["aliases"]))
                other["evidence"] = union_evidence(other["evidence"],entity["evidence"]+evidence)
                updated["redirects"][id_] = target
                for a in updated["assertions"].values():
                    if a["subject"]==id_:a["subject"]=target
                for r in updated["relations"].values():
                    for side in ("from","to"):
                        if r[side]==id_:r[side]=target
                updated["relations"] = {k:r for k,r in updated["relations"].items() if r["from"]!=r["to"]}
            else:
                raise InvalidData("Split or unknown decisions require explicit entity assignments")
            entity["changedAt"] = stamp(now)
            updated["audit"].append({"at":stamp(now),"action":action,"proposalHash":change_id,"entityId":id_,"targetId":target,"before":before})
        except InvalidData as exc:
            queue(updated, change_id, candidate, str(exc), now)
    updated["vocabularyReviewedAt"] = stamp(now)
    return updated
