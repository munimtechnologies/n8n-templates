"""Checks every node parameter in workflows/ against n8n's own node definitions.

Run: python -m pytest test_node_params.py  (or python test_node_params.py)
Standard library only. Downloads n8n-nodes-base's dist/types/nodes.json once (about 8 MB,
cached in the temp folder). It is the data the n8n editor draws its node forms from, so a
parameter name, option value or collection key that n8n does not know shows up here.
Set SKIP_N8N_PARAMS=1 to skip when offline.
"""

import json
import os
import re
import tempfile
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKFLOWS = sorted((HERE / "workflows").glob("*.json"))
NODES_BASE_VERSION = "2.15.1"
SOURCES = [
    f"https://unpkg.com/n8n-nodes-base@{NODES_BASE_VERSION}/dist/types/nodes.json",
    f"https://cdn.jsdelivr.net/npm/n8n-nodes-base@{NODES_BASE_VERSION}/dist/types/nodes.json",
]
PREFIX = "n8n-nodes-base."
ANY = object()  # an expression: n8n decides at run time, so it matches any condition
SKIPPED_TYPES = {"notice", "hidden", "filter", "resourceLocator", "json", "color", "assignmentCollection",
                 "dateTime", "credentialsSelect", "curlImport", "workflowSelector", "callout"}


def load_definitions():
    cache = Path(tempfile.gettempdir()) / f"n8n-nodes-base-{NODES_BASE_VERSION}-nodes.json"
    if not cache.exists() or cache.stat().st_size < 1_000_000:
        errors = []
        for url in SOURCES:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "munimv2-n8n-templates-test"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    cache.write_bytes(resp.read())
                break
            except Exception as e:  # try the next mirror
                errors.append(f"{url}: {e}")
        else:
            raise RuntimeError("could not download n8n node definitions: " + "; ".join(errors))
    defs = {}
    for desc in json.loads(cache.read_text(encoding="utf-8")):
        name = desc["name"] if desc["name"].startswith(PREFIX) else PREFIX + desc["name"]
        versions = desc["version"] if isinstance(desc["version"], list) else [desc["version"]]
        for v in versions:
            defs[(name, float(v))] = desc
    return defs


def is_expression(value):
    return isinstance(value, str) and value.startswith("=")


def cond_ok(want, value):
    """One entry of a displayOptions list: a plain value or {"_cnd": {op: arg}}."""
    if value is ANY:
        return True
    if isinstance(want, dict) and "_cnd" in want:
        for op, arg in want["_cnd"].items():
            try:
                ok = {
                    "eq": lambda: value == arg,
                    "not": lambda: value != arg,
                    "gte": lambda: value >= arg,
                    "lte": lambda: value <= arg,
                    "gt": lambda: value > arg,
                    "lt": lambda: value < arg,
                    "between": lambda: arg["from"] <= value <= arg["to"],
                    "startsWith": lambda: str(value).startswith(arg),
                    "endsWith": lambda: str(value).endswith(arg),
                    "includes": lambda: arg in str(value),
                    "regex": lambda: re.search(arg, str(value)) is not None,
                    "exists": lambda: value is not None,
                }.get(op, lambda: True)()  # an unknown condition is not guessed at
            except (TypeError, KeyError):
                ok = False
            if not ok:
                return False
        return True
    return want == value


class Node:
    """One workflow node with its definition, able to say which parameters n8n shows."""

    def __init__(self, props, params, version):
        self.props, self.params, self.version = props, params, version

    def value_of(self, key, depth):
        key = key.lstrip("/")
        if key == "@version":
            return self.version
        if key in self.params:
            return ANY if is_expression(self.params[key]) else self.params[key]
        for p in self.props:
            if p["name"] == key and self.visible(p, depth + 1):
                return p.get("default")
        return None

    def visible(self, prop, depth=0):
        if depth > 12:
            return True
        shown = prop.get("displayOptions") or {}
        for key, wants in (shown.get("show") or {}).items():
            value = self.value_of(key, depth)
            if not any(cond_ok(w, value) for w in wants):
                return False
        for key, wants in (shown.get("hide") or {}).items():
            value = self.value_of(key, depth)
            if value is not ANY and any(cond_ok(w, value) for w in wants):
                return False
        return True


def value_problems(prop, value, where):
    """What n8n would not accept for this value, as a list of messages."""
    if is_expression(value):
        return []
    kind = prop.get("type")
    if kind in SKIPPED_TYPES:
        return []
    if kind == "options":
        allowed = [o.get("value") for o in prop.get("options") or []]
        if allowed and value not in allowed:
            return [f"{where}: {value!r} is not one of {allowed[:15]}"]
    elif kind == "multiOptions":
        allowed = [o.get("value") for o in prop.get("options") or []]
        if not isinstance(value, list):
            return [f"{where}: expected a list"]
        bad = [v for v in value if allowed and v not in allowed]
        if bad:
            return [f"{where}: {bad!r} not among {allowed[:15]}"]
    elif kind == "boolean":
        if not isinstance(value, bool):
            return [f"{where}: expected true/false, got {value!r}"]
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{where}: expected a number, got {value!r}"]
    elif kind == "string":
        if not isinstance(value, str):
            return [f"{where}: expected text, got {value!r}"]
    elif kind == "collection":
        if not isinstance(value, dict):
            return [f"{where}: expected an object"]
        return entry_problems(prop.get("options") or [], value, where)
    elif kind == "fixedCollection":
        if not isinstance(value, dict):
            return [f"{where}: expected an object"]
        groups = {o["name"]: o.get("values") or [] for o in prop.get("options") or []}
        out = []
        for key, entries in value.items():
            if key not in groups:
                out.append(f"{where}.{key}: unknown, n8n has {sorted(groups)}")
                continue
            for i, entry in enumerate(entries if isinstance(entries, list) else [entries]):
                if not isinstance(entry, dict):
                    out.append(f"{where}.{key}[{i}]: expected an object")
                else:
                    out += entry_problems(groups[key], entry, f"{where}.{key}[{i}]")
        return out
    return []


def entry_problems(props, values, where):
    """Keys of a collection or fixedCollection entry. Inner visibility is not checked."""
    out = []
    for key, value in values.items():
        candidates = [p for p in props if p["name"] == key]
        if not candidates:
            out.append(f"{where}.{key}: unknown, n8n has {sorted({p['name'] for p in props})}")
            continue
        found = [value_problems(p, value, f"{where}.{key}") for p in candidates]
        if all(found):
            out += found[0]
    return out


def node_problems(node, defs):
    where = node["name"]
    key = (node["type"], float(node["typeVersion"]))
    if key not in defs:
        known = sorted(v for (t, v) in defs if t == node["type"])
        return [f"{where}: {node['type']} has no version {node['typeVersion']} (n8n has {known})"]
    params = node.get("parameters") or {}
    checker = Node(defs[key].get("properties") or [], params, float(node["typeVersion"]))
    out = []
    for name, value in params.items():
        candidates = [p for p in checker.props if p["name"] == name]
        if not candidates:
            out.append(f"{where}.{name}: not a parameter of {node['type']} v{node['typeVersion']}")
            continue
        shown = [p for p in candidates if checker.visible(p)]
        if not shown:
            out.append(f"{where}.{name}: n8n hides this parameter with these settings")
            continue
        found = [value_problems(p, value, f"{where}.{name}") for p in shown]
        if all(found):
            out += found[0]
    return out


class NodeParameterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("SKIP_N8N_PARAMS") == "1":
            raise unittest.SkipTest("SKIP_N8N_PARAMS=1")
        cls.defs = load_definitions()

    def test_definitions_loaded(self):
        self.assertIn(("n8n-nodes-base.httpRequest", 4.2), self.defs)

    def test_every_parameter(self):
        self.assertGreaterEqual(len(WORKFLOWS), 3, "workflows/ not found next to this test")
        problems = []
        for path in WORKFLOWS:
            wf = json.loads(path.read_text(encoding="utf-8"))
            for node in wf["nodes"]:
                problems += [f"{path.name}: {p}" for p in node_problems(node, self.defs)]
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


class CheckerTests(unittest.TestCase):
    """The checker itself, on a small made-up node definition."""

    PROPS = [
        {"name": "resource", "type": "options", "default": "message",
         "options": [{"value": "message"}, {"value": "label"}]},
        {"name": "operation", "type": "options", "default": "send",
         "displayOptions": {"show": {"resource": ["message"]}}, "options": [{"value": "send"}]},
        {"name": "sendTo", "type": "string", "default": "",
         "displayOptions": {"show": {"resource": ["message"], "operation": ["send"]}}},
        {"name": "newer", "type": "boolean", "default": False,
         "displayOptions": {"show": {"@version": [{"_cnd": {"gte": 2.1}}]}}},
        {"name": "options", "type": "collection", "default": {},
         "options": [{"name": "timeout", "type": "number", "default": 0}]},
        {"name": "rule", "type": "fixedCollection", "default": {},
         "options": [{"name": "interval", "values": [
             {"name": "field", "type": "options", "default": "days",
              "options": [{"value": "days"}, {"value": "weeks"}]}]}]},
    ]

    def problems(self, params, version=2.1):
        defs = {("n8n-nodes-base.x", 2.0): {"properties": self.PROPS},
                ("n8n-nodes-base.x", 2.1): {"properties": self.PROPS}}
        node = {"name": "N", "type": "n8n-nodes-base.x", "typeVersion": version, "parameters": params}
        return node_problems(node, defs)

    def test_good(self):
        self.assertEqual(self.problems({"sendTo": "a@b.c", "newer": True, "options": {"timeout": 5},
                                        "rule": {"interval": [{"field": "weeks"}]}}), [])

    def test_expressions_pass(self):
        self.assertEqual(self.problems({"resource": "={{ 'label' }}", "sendTo": "={{ $json.to }}"}), [])

    def test_bad(self):
        self.assertTrue(self.problems({"sendTo": 5}))
        self.assertTrue(self.problems({"unknown": 1}))
        self.assertTrue(self.problems({"resource": "label", "sendTo": "a@b.c"}))  # hidden
        self.assertTrue(self.problems({"newer": True}, version=2.0))  # needs version 2.1
        self.assertTrue(self.problems({"options": {"timeot": 5}}))
        self.assertTrue(self.problems({"rule": {"interval": [{"field": "hours"}]}}))
        self.assertTrue(self.problems({"rule": {"intervals": []}}))
        self.assertTrue(self.problems({}, version=3))


if __name__ == "__main__":
    unittest.main()
