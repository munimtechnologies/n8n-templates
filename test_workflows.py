"""Structural checks for the n8n workflow templates in workflows/.

Run: python -m pytest test_workflows.py  (or python test_workflows.py)
Standard library only. Checks what n8n's template review looks at: valid JSON, wired
connections, descriptive node names, a full description sticky note, no credentials,
and that every $('Node name') reference in code and expressions points at a real node.
test_node_params.py checks node parameters against n8n's own node definitions.
"""

import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKFLOWS = sorted((HERE / "workflows").glob("*.json"))

KNOWN_TYPES = {
    "n8n-nodes-base.stickyNote": {1},
    "n8n-nodes-base.scheduleTrigger": {1.2},
    "n8n-nodes-base.code": {2},
    "n8n-nodes-base.httpRequest": {4.2},
    "n8n-nodes-base.if": {2.2},
    "n8n-nodes-base.gmail": {2.1},
    "n8n-nodes-base.telegram": {1.2},
    "n8n-nodes-base.slack": {2.3},
    "n8n-nodes-base.noOp": {1},
}
TRIGGERS = {"n8n-nodes-base.scheduleTrigger"}
DEFAULT_NAMES = {"Code", "HTTP Request", "If", "Set", "Edit Fields", "Gmail", "Slack", "Telegram",
                 "Schedule Trigger", "No Operation, do nothing"}
HYPE = ("ultimate", "powerful", "revolutionary", "10x", "magic", "supercharge")
# Token prefixes are split so this file never looks like it holds a token itself.
SECRET_RE = re.compile("|".join([
    "sk" + r"-[A-Za-z0-9]{12,}",
    "gh" + r"p_[A-Za-z0-9]{20,}",
    "AK" + r"IA[0-9A-Z]{16}",
    "xo" + r"x[bp]-",
    r"\d{8,10}:[A-Za-z0-9_-]{30,}",
    r"Bearer [A-Za-z0-9._-]{20,}",
]))
NODE_REF_RE = re.compile(r"\$\('([^']+)'\)")
EXPR_RE = re.compile(r"\{\{(.*?)\}\}", re.S)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def brackets_balanced(code):
    """Bracket balance outside strings and comments. The templates use no regex literals."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack, i, n = [], 0, len(code)
    while i < n:
        c = code[i]
        if code.startswith("//", i):
            j = code.find("\n", i)
            i = n if j < 0 else j
            continue
        if code.startswith("/*", i):
            j = code.find("*/", i + 2)
            if j < 0:
                return False
            i = j + 2
            continue
        if c in "'\"`":
            j = i + 1
            while j < n and code[j] != c:
                if code[j] == "\\":
                    j += 1
                elif code[j] == "\n" and c != "`":
                    return False
                j += 1
            if j >= n:
                return False
            i = j + 1
            continue
        if c in "([{":
            stack.append(c)
        elif c in ")]}":
            if not stack or stack.pop() != pairs[c]:
                return False
        i += 1
    return not stack


def expressions_ok(text):
    """An n8n expression is '=' followed by text with {{ ... }} parts, e.g. '={{ $json.url }}/api'.
    Every {{ needs its }}, and the code inside each pair must be balanced."""
    body = text[1:]
    parts = EXPR_RE.findall(body)
    if not parts or body.count("{{") != len(parts) or body.count("}}") != len(parts):
        return False
    return all(brackets_balanced(p) for p in parts)


def all_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from all_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_strings(v)


class WorkflowTests(unittest.TestCase):
    def test_there_are_workflows(self):
        self.assertGreaterEqual(len(WORKFLOWS), 3)

    def test_each_workflow(self):
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                self.check(path)

    def check(self, path):
        wf = load(path)
        title = wf["name"]
        nodes = wf["nodes"]
        conns = wf["connections"]
        names = [n["name"] for n in nodes]
        by_name = {n["name"]: n for n in nodes}

        # Title: n8n's review wants a plain "verb + object + where" title.
        self.assertTrue(title[0].isupper() and len(title) <= 110, title)
        self.assertFalse(any(h in title.lower() for h in HYPE), title)
        self.assertTrue(all(ord(ch) < 0x2000 for ch in title), "no emoji in the title")

        self.assertEqual(len(names), len(set(names)), "node names must be unique")
        self.assertEqual(len(nodes), len({n["id"] for n in nodes}), "node ids must be unique")

        for n in nodes:
            self.assertIn(n["type"], KNOWN_TYPES, n["name"])
            self.assertIn(n["typeVersion"], KNOWN_TYPES[n["type"]], n["name"])
            self.assertEqual(len(n["position"]), 2)
            self.assertNotIn("credentials", n, f"{n['name']}: templates must not carry credentials")
            if n["type"] != "n8n-nodes-base.stickyNote":
                self.assertNotIn(n["name"], DEFAULT_NAMES, "rename default node names")

        # Connections point at real nodes, and every working node is reachable from the trigger.
        edges = {}
        for src, outputs in conns.items():
            self.assertIn(src, by_name, f"connection from unknown node {src}")
            for branch in outputs["main"]:
                for link in branch:
                    self.assertIn(link["node"], by_name, f"connection to unknown node {link['node']}")
                    edges.setdefault(src, []).append(link["node"])
        triggers = [n["name"] for n in nodes if n["type"] in TRIGGERS]
        self.assertEqual(len(triggers), 1)
        seen, todo = set(), list(triggers)
        while todo:
            cur = todo.pop()
            if cur in seen:
                continue
            seen.add(cur)
            todo.extend(edges.get(cur, []))
        working = {n["name"] for n in nodes if n["type"] != "n8n-nodes-base.stickyNote"}
        self.assertEqual(working - seen, set(), "nodes not connected to the trigger")

        # If nodes need both branches wired.
        for n in nodes:
            if n["type"] == "n8n-nodes-base.if":
                self.assertEqual(len(conns[n["name"]]["main"]), 2, n["name"])

        # One yellow sticky note carries the full description, starting with the title.
        stickies = [n for n in nodes if n["type"] == "n8n-nodes-base.stickyNote"]
        main = [s for s in stickies if "color" not in s["parameters"]]
        self.assertEqual(len(main), 1, "exactly one default-colour (yellow) description note")
        content = main[0]["parameters"]["content"]
        self.assertTrue(content.startswith("## " + title), "description note starts with the title")
        for section in ("Who it's for", "How it works", "Setup", "Requirements"):
            self.assertIn(section, content)
        self.assertGreaterEqual(len(content.split()), 150)
        self.assertIn("with AI agents", content, "disclose that the template was made with AI")

        # Code: balanced brackets, and every $('Node') reference exists.
        for n in nodes:
            if n["type"] == "n8n-nodes-base.code":
                code = n["parameters"]["jsCode"]
                self.assertTrue(brackets_balanced(code), f"{n['name']}: unbalanced brackets")
                self.assertIn("return", code)
        for text in all_strings(wf):
            for ref in NODE_REF_RE.findall(text):
                self.assertIn(ref, by_name, f"$('{ref}') refers to a missing node")
            self.assertIsNone(SECRET_RE.search(text), "secret-shaped string")

        # Expressions are balanced too. Text may follow the }}, as in '={{ $json.url }}/api/version'.
        for text in all_strings(wf):
            if text.startswith("={{"):
                self.assertTrue(expressions_ok(text), text)

        # Placeholders the user must replace are clearly named.
        sends = [n for n in nodes if n["type"] in {"n8n-nodes-base.gmail", "n8n-nodes-base.telegram",
                                                    "n8n-nodes-base.slack"}]
        self.assertEqual(len(sends), 1)
        self.assertIn("REPLACE_WITH_", json.dumps(sends[0]["parameters"]))


class HelperTests(unittest.TestCase):
    def test_brackets(self):
        self.assertTrue(brackets_balanced("const a = { b: [1, 2], c: 'x)' }; // ) \n f(a);"))
        self.assertFalse(brackets_balanced("f(a;"))
        self.assertFalse(brackets_balanced("const s = 'open;"))

    def test_expressions(self):
        self.assertTrue(expressions_ok("={{ $json.url }}"))
        self.assertTrue(expressions_ok("={{ $json.url }}/api/version"))
        self.assertTrue(expressions_ok("={{ $('Settings').first().json.url }}/api/tags"))
        self.assertTrue(expressions_ok("={{ JSON.stringify({ a: { b: 1 }, c: f('x') }) }}"))
        self.assertTrue(expressions_ok("=Hello {{ $json.name }}, you have {{ $json.count }} items"))
        self.assertFalse(expressions_ok("={{ $json.url /api"))
        self.assertFalse(expressions_ok("={{ f($json.url }}"))
        self.assertFalse(expressions_ok("={{ $json.a }} and {{ $json.b"))


if __name__ == "__main__":
    unittest.main()
