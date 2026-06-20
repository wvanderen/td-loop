#!/usr/bin/env python3
"""Copy the open td issues from the td-loop-skill-validation fixture into this
repo's td database. Content recovered from the fixture's command_usage.jsonl
(the fixture's issues.db is corrupt); titles/statuses cross-checked against
authoritative `td context` reads captured earlier.

td generates fresh IDs per project, so original IDs are NOT preserved; instead
each issue gets a provenance comment naming its original ID, and parent links
are re-pointed to the new epic id. A mapping is printed at the end.
"""
import re, subprocess, sys

TD = ["td"]

def run(args):
    r = subprocess.run(TD + args, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"td failed: {' '.join(args)}\nstdout:{r.stdout}\nstderr:{r.stderr}")
    return r.stdout

def create(issue):
    title = issue["title"]
    args = ["create", title,
            "--type", issue["type"],
            "--priority", issue["priority"],
            "--description", issue["description"],
            "--acceptance", issue["acceptance"]]
    for lab in issue["labels"]:
        args += ["-l", lab]
    if issue.get("parent"):
        args += ["--parent", issue["parent"]]
    out = run(args)
    m = re.search(r"(td-[0-9a-f]{6})", out)
    if not m:
        sys.exit(f"could not parse new id from: {out!r}")
    new_id = m.group(1)
    # Provenance comment so the audit trail survives the ID change.
    run(["comment", new_id,
         f"Copied from td-loop-skill-validation fixture (orig {issue['orig']}) "
         f"on 2026-06-19. Fixture .todos/issues.db was corrupt (SQLITE_CORRUPT); "
         f"content recovered from command logs. History/handoffs not ported."])
    return new_id

# Order matters: epic first so children can reference it.
ISSUES = [
    {
        "orig": "td-0a3c32",
        "title": "Epic: Harden td-loop after validation run",
        "type": "epic", "priority": "P1",
        "labels": ["td-loop-followup"],
        "description": "Follow-up improvements discovered during the td-loop skill validation run.",
        "acceptance": "Child issues capture actionable improvements that should be worked in separate sessions.",
    },
    {
        "orig": "td-9f2a21",
        "title": "Detect td review mode before delegated review",
        "type": "task", "priority": "P1",
        "labels": ["td-loop-followup", "review-policy"],
        "description": "Update td-loop guidance or tooling to inspect td review capabilities before asking reviewers for record-only approvals. The validation run attempted --record-only in a trusted-mode database and had to recover.",
        "acceptance": "Before spawning/requesting review, the loop determines whether delegated record-only approval is supported, adapts reviewer instructions accordingly, and documents the close path to use for trusted-mode td databases.",
    },
    {
        "orig": "td-a6a966",
        "title": "Make human UAT unblock evidence structured",
        "type": "task", "priority": "P1",
        "labels": ["td-loop-followup", "human-uat"],
        "description": "Improve the resume path for human-only UAT gates. During validation, the user supplied a pass instruction, but the issue acceptance requested sender, subject, timestamp, and visible package name.",
        "acceptance": "When resuming from human-uat-required, the loop records supplied human evidence fields; if fields are missing, it records operator attestation and clearly names the missing metadata before continuing.",
    },
    {
        "orig": "td-96a80e",
        "title": "Avoid concurrent td state mutations in td-loop",
        "type": "task", "priority": "P2",
        "labels": ["td-loop-followup", "state-management"],
        "description": "The validation run showed confusing child/parent state when td handoff/review/approve commands overlapped with auto-cascade behavior. Reads can remain parallel, but writes should be sequenced.",
        "acceptance": "The loop contract explicitly sequences td write commands and refreshes issue state after handoff, review, approve, block, unblock, and parent auto-cascade events before deciding the next action.",
    },
    {
        "orig": "td-cdd775",
        "title": "Improve browser UAT isolation for persisted-state workflows",
        "type": "task", "priority": "P2",
        "labels": ["td-loop-followup", "browser-uat"],
        "description": "Reload persistence UAT accumulated duplicate Coffee beans rows because browser localStorage reset was unavailable in the in-app browser surface.",
        "acceptance": "The validation fixture or loop guidance provides a safe way to run persisted-state UAT from a clean state, such as unique test data, an explicit fixture reset path, or a clean browser context when available.",
    },
    {
        "orig": "td-804dbf",
        "title": "Require explicit handoff content before review",
        "type": "task", "priority": "P2",
        "labels": ["td-loop-followup", "handoff"],
        "description": "Several validation handoffs were empty or auto-generated, which weakens review context and auditability.",
        "acceptance": "Before td review, the loop records done, remaining, decisions, and uncertain items in the handoff when td supports structured content; if td does not support structured flags, the review reason or comment contains those sections.",
    },
]

mapping = {}
epic_new = None
for issue in ISSUES:
    if issue["type"] == "epic":
        epic_new = create(issue)
        mapping[issue["orig"]] = epic_new
        print(f"{issue['orig']} -> {epic_new}  (epic)")
    else:
        issue["parent"] = epic_new
        nid = create(issue)
        mapping[issue["orig"]] = nid
        print(f"{issue['orig']} -> {nid}  (parent epic {epic_new})")

print("\nOLD -> NEW ID MAPPING:")
for o, n in mapping.items():
    print(f"  {o}  ->  {n}")
