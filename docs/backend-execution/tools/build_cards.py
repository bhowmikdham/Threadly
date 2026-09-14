"""Generate focused task cards and index from the handoff manifest (standard library)."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def outputs(data):
    result = {}
    index = [
        "# Backend execution task index",
        "",
        "Generated from [tasks.json](tasks.json). All entries initially describe planned work.",
        "Read START-HERE.md, the selected card and its dependencies; one bounded PR at a time.",
        "",
        "| Task | Work | Requires | Original packages |",
        "|---|---|---|---|",
    ]
    for task in data["tasks"]:
        ident = task["id"]
        index.append(
            f"| [{ident}](tasks/{ident}.md) | {task['title']} | "
            f"{', '.join(task['depends_on']) or 'Verified checkout'} | "
            f"{', '.join(task['work_packages'])} |"
        )
        lines = [
            f"# {ident} — {task['title']}",
            "",
            "**Proposed implementation task. Generated from tasks.json; edit the manifest to change scope.**",
            "",
            f"Status: `{task['status']}`. Original packages: {', '.join(task['work_packages'])}.",
            "",
            f"Prerequisites: {', '.join(task['depends_on']) or 'None beyond verified baseline access'}.",
            "",
            f"Owner: {task['owner']}. Reviewers: {', '.join(task['reviewers'])}.",
            "",
            "## Objective",
            "",
            task["objective"],
            "",
            "## Read first",
            "",
            "- [Agent launch contract](../START-HERE.md)",
            "- [Baseline and known traps](../BASELINE.md)",
            "- [Quality gates](../QUALITY-GATES.md)",
            f"- [Shared design](../DESIGN.md): {', '.join(task['design_sections'])}",
            "",
            "Existing repository files (paths are relative to repository root):",
            "",
        ]
        lines += [f"- [{p}](../../../{p})" for p in task["read_existing"]]
        lines += [
            "",
            "Proposed implementation locations (may not exist yet; follow current repository conventions):",
            "",
        ]
        lines += [f"- `{p}`" for p in task["proposed_paths"]]
        lines += ["", "## Implementation sequence", ""]
        lines += [f"{n}. {s}" for n, s in enumerate(task["implementation"], 1)]
        lines += [
            "",
            "## Acceptance assertions",
            "",
            "Implement independent tests for these behaviors. Record each assertion in the checkpoint;",
            "do not mark a live assertion passed using only a mock. Apply all relevant QUALITY-GATES checks.",
            "",
        ]
        lines += [
            f"- **{ident}-A{n}:** {s}"
            for n, s in enumerate(task["acceptance_tests"], 1)
        ]
        lines += [
            "",
            "## External integration gate",
            "",
            task["external_gate"],
            "",
            "## Finish and hand off",
            "",
            task["handoff"],
            "",
            "Run focused tests and settled full regression when changing runtime code. Run schema/migration",
            "checks when changing storage; pin/evaluate prompt or Flow changes. Use the existing error",
            "envelope and owner checks. Do not deploy or enable real writes from this planning document.",
            "",
            f"Save evidence to `docs/backend-execution/{task['checkpoint']}` using",
            "[the checkpoint template](../CHECKPOINT.md). Update this manifest status and the original",
            "T-task evidence only as justified. Commit a reviewable PR; no auto-merge. Report next eligible",
            "task and any remaining live gate. An entire mapped T package may still be incomplete.",
            "",
        ]
        result[ROOT / "tasks" / f"{ident}.md"] = "\n".join(lines)
    index += [
        "",
        "## Completion boundary",
        "",
        "B00–B20 cover baseline through the initial release; B21–B25 are follow-on capabilities.",
        "Dependencies govern implementation ordering, not automatic live activation.",
        "B05 has an explicit hard enabling gate on B06 plus test-account validation.",
        "B19 operational work may be prepared early, but its final gate must use the completed action/Flow runtime.",
        "",
    ]
    result[ROOT / "INDEX.md"] = "\n".join(index)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = json.loads((ROOT / "tasks.json").read_text())
    generated = outputs(data)
    mismatched = []
    for path, content in generated.items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                mismatched.append(str(path.relative_to(REPO)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if mismatched:
        raise SystemExit("Stale generated cards: " + ", ".join(mismatched))
    print(
        f"{'Checked' if args.check else 'Generated'} {len(data['tasks'])} cards and index."
    )


if __name__ == "__main__":
    main()
