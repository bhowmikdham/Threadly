#!/usr/bin/env python3
"""Regenerate readable work packages from the canonical planning backlog."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render(backlog: dict) -> str:
    result = [
        "# 12 · Work package cards\n\n",
        "Generated from [backlog.json](backlog.json). Edit the JSON and regenerate "
        "with `python3 docs/implementation-playbook/tools/build_task_cards.py` "
        "so human and coding-agent instructions stay aligned. Status is copied from "
        "the backlog; implementation completion requires the recorded evidence.\n\n",
    ]
    for task in backlog["tasks"]:
        phase = "Initial release" if task["release"] == "initial" else "Optional follow-on"
        dependencies = ", ".join(task["depends_on"]) or "No implementation dependency; review the playbook."
        paths = ", ".join(f"`{path}`" for path in task["implementation_targets"])
        result.extend([
            f"## {task['id']} · {task['title']}\n\n",
            f"**Owner:** {task['owner']} · **Status:** {task['status']} · **Release:** {phase}\n\n",
            f"**Depends on:** {dependencies}\n\n",
            f"**Implementation targets (may not exist yet):** {paths}\n\n",
        ])
        for title, key in [
            ("Deliverables", "deliverables"),
            ("Acceptance criteria", "acceptance"),
            ("Evidence and handoff", "evidence_required"),
        ]:
            result.append(f"### {title}\n\n")
            result.append("\n".join(f"- {item}" for item in task[key]) + "\n\n")
        result.append(task["handoff"] + "\n\n")
    return "".join(result).rstrip() + "\n"


if __name__ == "__main__":
    backlog = json.loads((ROOT / "backlog.json").read_text())
    (ROOT / "12-work-packages.md").write_text(render(backlog))
    print(f"Generated {len(backlog['tasks'])} task cards.")
