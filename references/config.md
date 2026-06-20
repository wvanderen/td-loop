# TD Loop JSON Config

Use a JSON config when the user wants repeatable backlog execution across epics, agents, review policy, and UAT gates. Validate it with `scripts/validate_config.py`.

## Minimal Example

```json
{
  "version": 1,
  "td": {
    "work_dir": ".",
    "scope": {
      "epics": ["td-epic1"],
      "labels": [],
      "priorities": ["P0", "P1", "P2"]
    },
    "critical_path_limit": 10
  },
  "agents": {
    "orchestrator": "codex",
    "implementers": ["codex"],
    "reviewers": ["codex", "opencode"],
    "advisors": ["pi"],
    "commands": {
      "codex": {
        "command": ["codex"],
        "prompt_mode": "stdin",
        "roles": ["orchestrator", "implementer", "reviewer"]
      },
      "opencode": {
        "command": ["opencode"],
        "prompt_mode": "stdin",
        "roles": ["implementer", "reviewer"]
      },
      "pi": {
        "command": ["pi"],
        "prompt_mode": "stdin",
        "roles": ["advisor", "reviewer"]
      }
    }
  },
  "review": {
    "spawn_review_agent": "risk_based",
    "policy_mode": "trusted",
    "prefer_independent_review": true,
    "require_independent_for": {
      "priorities": ["P0", "P1"],
      "labels": ["security", "billing", "migration"],
      "types": ["feature", "bug"]
    },
    "allow_self_review_for_minor": true
  },
  "uat": {
    "required": true,
    "methods": ["browser", "computer_use", "cli"],
    "screenshot_required": true,
    "human_escalation_label": "human-uat-required",
    "block_on_unverifiable": true
  },
  "budgets": {
    "max_issues_per_loop": 3,
    "max_minutes": 120
  }
}
```

## Fields

- `version`: config version. Use `1`.
- `td.work_dir`: project directory to pass through `td -w` when needed.
- `td.scope.epics`: epic ids to work inside. Empty means project-wide critical path.
- `td.scope.labels`: optional label filter.
- `td.scope.priorities`: allowed priorities.
- `td.critical_path_limit`: number of critical-path issues to inspect each refresh.
- `agents.orchestrator`: one of `codex`, `opencode`, `pi`.
- `agents.implementers`: ordered agent preference for implementation.
- `agents.reviewers`: agents eligible for independent review.
- `agents.advisors`: agents used for planning or product critique.
- `agents.commands`: optional concrete invocation specs keyed by `codex`, `opencode`, and/or `pi`.
- `agents.commands.<agent>.command`: executable and fixed args as a string array. Prefer arrays over shell strings.
- `agents.commands.<agent>.prompt_mode`: `stdin`, `arg`, or `manual`.
- `agents.commands.<agent>.roles`: allowed roles for this command: `orchestrator`, `implementer`, `reviewer`, and/or `advisor`.
- `review.spawn_review_agent`: `always`, `risk_based`, or `never`.
- `review.policy_mode`: expected td review policy mode for this project — `strict`, `balanced`, `delegated`, or `trusted` (default `trusted`). The loop reads the actual mode with `td feature get review_policy_mode` (or `scripts/review_close_path.py --expected <this value> --json`, which warns and exits non-zero on a mismatch) and adapts the close path to the **resolved** value; this field documents the expectation and lets the helper warn on a mismatch. The loop does not mutate the user's td feature flags.
- `review.prefer_independent_review`: default `true`. For non-minor issues, request an independent reviewer context (fresh session) rather than self-reviewing, even when no sub-agent tool is available.
- `review.require_independent_for`: priorities, labels, and types that require independent review.
- `review.allow_self_review_for_minor`: allow td self-review escape hatch for minor work.
- **Structured handoff before review** (loop guarantee, not a config knob): before every `td review`, the loop records `done`/`remaining`/`decisions`/`uncertain` as `td handoff` fields and gates submission with `scripts/handoff_required.py --issue <id> --strict`. When the installed `td` lacks the structured flags, the same four sections go into the `td review --reason` (or a `td comment`) and the helper verifies them via `--review-reason`. This prevents the empty/auto-generated handoffs the validation run produced.
- **Persisted-state browser UAT runs from a clean state** (loop guarantee, not a config knob): before any workflow that asserts state survives a reload (localStorage/sessionStorage/IndexedDB), the loop resolves the strongest isolation the browser surface supports with `scripts/browser_uat_isolation.py --issue <id> --workflow <name> --surface <clean-context|reset|unique-data|none> --json` and records a `browser-uat.json` manifest. It escalates to human UAT (and `--strict` exits non-zero) when no clean-state path is available, instead of asserting against storage that may carry rows from a previous run (the validation run's duplicate "Coffee beans" rows).
- **td writes are sequenced, then state is refreshed** (loop guarantee, not a config knob): state-changing td commands (`handoff`, `review`, `approve`, `block`, `unblock`, `reject`) run one at a time — never fanned out across sub-agents or background shells — and after each one the loop re-reads the affected issue with `td show <id> --json` (and `td tree <id> --json` for a parent/epic, since `td review` and the close flow auto-cascade to descendants) before choosing the next action. Do **not** use `td show <id> --json --children` for this: on td 0.46.0 `--children` is a silent no-op under `--json`/`--format json` (parent-only output, exit 0, no warning), even though it works in the human format. Reads (`show`, `list`, `critical-path`, `status`, `tree`) may stay parallel. This avoids the overlapping child/parent state the validation run produced when handoff/review/approve ran concurrently with auto-cascade. Documented in **Sequencing td Writes** in `SKILL.md`.
- `uat.required`: require UAT before review submission for workflow-bearing issues.
- `uat.methods`: allowed verification methods. Use `browser`, `computer_use`, `cli`, and/or `manual`.
- `uat.screenshot_required`: require screenshot evidence for UI/visual flows.
- `uat.artifacts_dir`: canonical directory for screenshot evidence and manifests (default `uat-artifacts`). Relative to `td.work_dir`. One subdirectory per issue id holds `evidence.json`, an append-only `evidence.log`, and the relocated screenshots.
- `uat.human_escalation_label`: label used when blocking for human UAT.
- `uat.block_on_unverifiable`: if true, block instead of continuing when UAT cannot be automated.
- `budgets.max_issues_per_loop`: stop after this many issues.
- `budgets.max_minutes`: stop near this elapsed-time budget.

## Suggested Defaults

Default to Codex as orchestrator and implementer, Codex or Opencode as reviewer, and Pi as advisor. Keep `human-uat-required` as the canonical human escalation label and keep `uat.block_on_unverifiable` true unless the user explicitly accepts speculative progress without UAT.

Use command arrays to name wrappers, not to bypass the current environment's approval or sandbox rules. If `prompt_mode` is `manual`, print the exact prompt and stop for the human/operator to run it.
