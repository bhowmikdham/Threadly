#!/usr/bin/env python3
"""Validate this planning package using only Python's standard library.

Checks local Markdown targets/anchors, JSON, task dependencies, referenced fixture
schemas and the JSON Schema keywords used by those fixtures. This is a deliberately
bounded planning check, not a general JSON Schema validator, Mermaid renderer,
backend test runner or live AWS deployment validator.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from build_task_cards import render

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        ERRORS.append(message)


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        ERRORS.append(f"{path.relative_to(ROOT)}: {exc}")
        return None


def slug(heading: str) -> str:
    plain = re.sub(r"[`*_]", "", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", plain).replace(" ", "-")


def validate(value, spec: dict, path: str = "$", errors: list[str] | None = None):
    """Validate exactly the schema subset used in this package's examples."""
    errors = [] if errors is None else errors
    if "anyOf" in spec:
        if not any(not validate(value, branch, path) for branch in spec["anyOf"]):
            errors.append(f"{path}: matches no anyOf branch")
        return errors
    if "const" in spec and value != spec["const"]:
        errors.append(f"{path}: wrong const")
    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{path}: not in enum")
    types = spec.get("type", [])
    types = [types] if isinstance(types, str) else types
    matches = {
        "null": value is None,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (float, int)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }
    if types and not any(matches.get(t, False) for t in types):
        errors.append(f"{path}: expected {types}")
        return errors
    if isinstance(value, dict):
        for field in spec.get("required", []):
            if field not in value:
                errors.append(f"{path}: missing {field}")
        properties = spec.get("properties", {})
        if spec.get("additionalProperties") is False:
            for field in value.keys() - properties.keys():
                errors.append(f"{path}: unexpected {field}")
        for field, child in properties.items():
            if field in value:
                validate(value[field], child, f"{path}.{field}", errors)
    if isinstance(value, list):
        if len(value) < spec.get("minItems", 0) or len(value) > spec.get("maxItems", sys.maxsize):
            errors.append(f"{path}: invalid item count")
        for index, item in enumerate(value):
            validate(item, spec.get("items", {}), f"{path}[{index}]", errors)
    if isinstance(value, str):
        if len(value) < spec.get("minLength", 0) or len(value) > spec.get("maxLength", sys.maxsize):
            errors.append(f"{path}: invalid string length")
        if "pattern" in spec and not re.search(spec["pattern"], value):
            errors.append(f"{path}: pattern mismatch")
        if spec.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("missing time zone")
            except ValueError:
                errors.append(f"{path}: invalid zoned date-time")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < spec.get("minimum", float("-inf")) or value > spec.get("maximum", float("inf")):
            errors.append(f"{path}: out of range")
    for branch in spec.get("allOf", []):
        validate(value, branch, path, errors)
    if "if" in spec and not validate(value, spec["if"], path):
        validate(value, spec.get("then", {}), path, errors)
    return errors


def main() -> int:
    markdown = list(ROOT.rglob("*.md"))
    diagrams = 0
    for path in markdown:
        text = path.read_text()
        check(text.count("```") % 2 == 0, f"Unbalanced fences: {path.name}")
        diagrams += text.count("```mermaid")
        without_code = re.sub(r"```.*?```", "", text, flags=re.S)
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", without_code):
            target = target.strip("<>")
            if re.match(r"[a-zA-Z]+:", target):
                continue
            filename, _, anchor = unquote(target).partition("#")
            resolved = (path.parent / filename).resolve() if filename else path
            check(resolved.exists(), f"Broken target in {path.name}: {target}")
            if anchor and resolved.is_file() and resolved.suffix == ".md":
                headings = re.findall(r"^#{1,6}\s+(.+)$", resolved.read_text(), re.M)
                check(anchor in {slug(h) for h in headings}, f"Missing anchor in {path.name}: {target}")
    parsed = {str(p.relative_to(ROOT)): read_json(p) for p in ROOT.rglob("*.json")}
    backlog = parsed.get("backlog.json") or {}
    tasks = backlog.get("tasks", [])
    check((ROOT / "12-work-packages.md").read_text() == render(backlog), "Work package cards are stale; run tools/build_task_cards.py")
    by_id = {t["id"]: t for t in tasks}
    check(len(tasks) == len(by_id), "Duplicate backlog task IDs")
    for task in tasks:
        check(bool(task.get("acceptance")) and bool(task.get("deliverables")), f"Missing task criteria: {task['id']}")
        for dep in task["depends_on"]:
            check(dep in by_id, f"Unknown dependency {dep} for {task['id']}")
    visiting, visited = set(), set()

    def walk(task_id: str):
        if task_id in visiting:
            ERRORS.append(f"Dependency cycle at {task_id}")
            return
        if task_id in visited or task_id not in by_id:
            return
        visiting.add(task_id)
        for dep in by_id[task_id]["depends_on"]:
            walk(dep)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in by_id:
        walk(task_id)
    examples = parsed.get("examples/index.json") or []
    for entry in examples:
        value, spec = parsed.get(entry["file"]), parsed.get(entry["schema"])
        if value is None or spec is None:
            ERRORS.append(f"Missing example/schema: {entry}")
            continue
        ERRORS.extend(f"{entry['file']}: {e}" for e in validate(value, spec))
        if entry["schema"] == "schemas/artifact.schema.json":
            evidence_ids = {e["ref_id"] for e in value["evidence"]}

            def refs(node):
                if isinstance(node, dict):
                    for key, child in node.items():
                        if key in {"evidence_ref_ids", "fact_ref_ids"}:
                            check(set(child) <= evidence_ids, f"Unknown evidence in {entry['file']}")
                        elif key == "check_ref":
                            check(child in evidence_ids, f"Unknown calendar check in {entry['file']}")
                        refs(child)
                elif isinstance(node, list):
                    for child in node:
                        refs(child)

            refs(value["content"])
    cases = (parsed.get("routing-fixtures.json") or {}).get("cases", [])
    check(len({c["id"] for c in cases}) == len(cases), "Duplicate routing fixture IDs")
    check({c["expected"]["intent"] for c in cases} == {"summarise", "plan_schedule", "reply", "compose", "other"}, "Routing seed does not cover all five intents")
    tool_catalog = (parsed.get("tool-catalog.json") or {}).get("tools", [])
    check(all(t["access"] == "write_executor_only" for t in tool_catalog if t["name"].endswith("_approved")), "Write tool boundary drift")
    # Negative probes establish that the planning validator catches meaningful
    # contract violations, rather than merely accepting every supplied example.
    bad_request = copy.deepcopy(parsed["examples/request.json"])
    bad_request["user_id"] = "client-selected-owner"
    check(bool(validate(bad_request, parsed["schemas/assistant-request.schema.json"])), "Validator accepted injected identity field")
    bad_route = copy.deepcopy(parsed["examples/route.json"])
    bad_route["operations"] = ["gmail.send_approved"]
    check(bool(validate(bad_route, parsed["schemas/route-decision.schema.json"])), "Validator accepted a write as a routing operation")
    if ERRORS:
        print("Planning validation failed:")
        for error in ERRORS:
            print(f"- {error}")
        return 1
    print(f"PASS: {len(markdown)} Markdown files, {diagrams} Mermaid diagrams, {len(parsed)} JSON files, {len(tasks)} acyclic work packages, {len(examples)} schema-checked examples, {len(cases)} routing seed cases.")
    print("Local links/anchors, fixture provenance references and contract rejection probes passed.")
    print("Not performed: Mermaid rendering, model evaluation, backend tests or live AWS/Google execution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
