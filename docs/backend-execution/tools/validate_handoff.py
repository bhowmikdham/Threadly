"""Validate plan structure/references; no backend/cloud execution or quality claims."""

import json
import re

from build_cards import REPO, ROOT, outputs


def validate():
    data = json.loads((ROOT / "tasks.json").read_text())
    tasks = data["tasks"]
    ids = [task["id"] for task in tasks]
    assert len(ids) == len(set(ids)), "Duplicate B task IDs"
    original = json.loads(
        (REPO / "docs/implementation-playbook/backlog.json").read_text()
    )
    original_ids = {task["id"] for task in original["tasks"]}
    known_design = set(
        re.findall(r"^## (D\d+) ", (ROOT / "DESIGN.md").read_text(), re.MULTILINE)
    )
    covered, seen = set(), set()
    statuses = {"planned", "in_progress", "in_review", "verified", "blocked"}
    for task in tasks:
        ident = task["id"]
        assert re.fullmatch(r"B\d{2}", ident), ident
        assert task["status"] in statuses, ident
        assert set(task["depends_on"]) <= seen, (
            f"{ident}: missing, cyclic or later prerequisite"
        )
        assert len(task["depends_on"]) == len(set(task["depends_on"])), ident
        assert set(task["work_packages"]) <= original_ids, ident
        covered.update(task["work_packages"])
        assert set(task["design_sections"]) <= known_design, ident
        for field in ("objective", "external_gate", "handoff", "checkpoint", "owner"):
            assert task[field].strip(), f"{ident}: empty {field}"
        for field in (
            "implementation",
            "acceptance_tests",
            "read_existing",
            "proposed_paths",
            "reviewers",
        ):
            assert task[field] and all(
                isinstance(v, str) and v.strip() for v in task[field]
            ), ident
        for name in task["read_existing"]:
            assert (REPO / name).exists(), f"{ident}: missing existing path {name}"
        assert task["checkpoint"] == f"checkpoints/{ident}.md", ident
        if task["status"] in {"in_review", "verified"}:
            assert (ROOT / task["checkpoint"]).exists(), (
                f"{ident}: missing completion checkpoint"
            )
        seen.add(ident)
    assert covered == original_ids, f"Unmapped original tasks: {original_ids - covered}"
    for path, expected in outputs(data).items():
        assert path.exists() and path.read_text() == expected, (
            f"Stale generated file {path.name}"
        )
    markdown = list(ROOT.rglob("*.md"))
    links = diagrams = 0
    for path in markdown:
        content = path.read_text()
        assert content.count("```") % 2 == 0, f"Unclosed code fence {path}"
        diagrams += content.count("```mermaid")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
            if re.match(r"https?://|mailto:|#", target):
                continue
            target = target.split("#", 1)[0]
            assert (path.parent / target).exists(), (
                f"Broken local link {path}: {target}"
            )
            links += 1
    print(
        f"PASS: {len(tasks)} ordered/acyclic B tasks cover {len(original_ids)} T packages; "
        f"{len(markdown)} Markdown files, {links} local links, {diagrams} Mermaid sources; "
        "cards match manifest and required fields exist."
    )
    print(
        "Not performed: diagram rendering, backend tests, live AWS/Google calls or model evaluation."
    )


if __name__ == "__main__":
    validate()
