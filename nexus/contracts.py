"""Strict provider-neutral schemas for extraction and semantic review."""
def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
def array(items): return {"type": "array", "items": items}
S = {"type": "string"}
B = {"type": "boolean"}
E = obj({"sourceId": S, "quote": S})
ENTITY = obj({"key": S, "label": S, "kind": S, "scope": S, "group": S, "entityId": S, "aliases": array(S), "evidence": array(E)})
ASSERTION = obj({"key": S, "subject": S, "aspect": S, "value": S, "statement": S, "applicability": S,
                 "certainty": S, "effectiveFrom": S, "effectiveTo": S, "dateEvidence": array(E), "supersedes": array(S), "evidence": array(E)})
RELATION = obj({"key": S, "from": S, "to": S, "type": S, "statement": S, "effectiveFrom": S, "effectiveTo": S, "dateEvidence": array(E), "evidence": array(E)})
EXTRACTION = obj({"entities": array(ENTITY), "assertions": array(ASSERTION), "relations": array(RELATION)})
REVIEW = obj({"decisions": array(obj({"key": S, "supported": B, "identityCorrect": B, "temporalCorrect": B, "reason": S}))})
VOCABULARY = obj({"decisions": array(obj({"action": S, "entityId": S, "targetId": S, "alias": S, "group": S, "reason": S, "evidence": array(E)}))})

def validate(value, schema):
    from .model import InvalidData
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["properties"]): raise InvalidData("Object schema mismatch")
        for k, s in schema["properties"].items(): validate(value[k], s)
    elif kind == "array":
        if not isinstance(value, list) or len(value) > 400: raise InvalidData("Array schema mismatch")
        for item in value: validate(item, schema["items"])
    elif kind == "string":
        if not isinstance(value, str) or len(value) > 12000: raise InvalidData("String schema mismatch")
    elif kind == "boolean":
        if type(value) is not bool: raise InvalidData("Boolean schema mismatch")
