---
name: td-loop
description: Run a stateful td backlog execution loop along td critical-path output, with structured JSON configuration, implementation/review agent selection, UAT verification gates, browser/computer-use screenshot evidence, and explicit human-escalation pauses when acceptance workflows cannot be verified. Use when Codex needs to work through td epics, tasks, or backlogs systematically with minimal oversight across Codex, Opencode, or Pi agents.
---

# TD Loop

## Overview

Use `td` as the source of truth for backlog state, ordering, handoffs, reviews, and escalation history. Work down the critical path one issue at a time; never silently skip UAT or continue past an unverified user workflow.

Read `references/config.md` when a JSON config exists or the user asks to create one. Run `scripts/validate_config.py <config.json>` before starting a configured loop.

## Loop Contract

**Ground truth is `td`, not pasted headers.** Before implementing or mutating td state on any issue, read live state with `td context <id>` (or `td show <id> --json`). A "Status:" line embedded in a pasted issue description is informational and may be stale; if it disagrees with td, td wins. If the issue is already closed, do not re-implement it — offer an independent review/verification or ask the user before reopening.

Manual agent sessions (a human driving Codex/Pi/OpenCode directly) are not spawned by td-loop, so the configured reviewer agents do not auto-spawn. In that case the human is the orchestrator and must provide the independent reviewer context (see **Review Policy**).

1. Establish session state with `td status --json`, `td critical-path --json --limit <n>`, and `td list --json` as needed.
2. Select the next eligible issue from the configured scope:
   - Prefer the first open issue on the critical path whose dependencies are closed.
   - Do not start blocked issues.
   - If an issue is `in_review`, review or close it only when the current session is eligible.
3. Start the issue with `td start <id>` and set focus with `td focus <id>`.
4. Read full context with `td show <id> --json --children`; inspect linked files, acceptance criteria, dependencies, and comments.
5. Implement the smallest complete change that satisfies the issue and preserves the repo's existing style.
6. Verify the change with tests, linters, and task-specific checks.
7. Perform UAT for every user-facing or workflow-bearing issue:
   - Prefer browser automation for web apps.
   - Use computer use when browser automation is impossible but GUI verification is possible.
   - Capture or inspect screenshots when visual state matters, and persist them with the artifact strategy below (never leave screenshot evidence only in a temp location).
   - For workflows that assert state survives a reload (localStorage/sessionStorage/IndexedDB), resolve a clean-state isolation strategy first and record it — see **Persisted-State Browser UAT**.
   - Confirm the exact workflow named by the td issue, not just a nearby smoke test.
8. If UAT cannot be performed, pause the loop:
   - Add a `human-uat-required` label while preserving existing labels.
   - Add a comment that names the blocked workflow, attempted automation path, missing capability, exact human instructions, **and the required evidence fields** (copied from the issue's acceptance criteria) so the resume step can record them.
   - Run `td block <id> --reason "human-uat-required: <short reason>"`.
   - Stop; do not continue to downstream critical-path work until a human unblocks or approves the issue. When a human unblocks, follow the structured resume in **Human Escalation Protocol** before continuing.
9. Capture a handoff with `td handoff <id>` that populates **all four** structured sections — done, remaining, decisions, uncertain — not a prose `--note`/`-m`. `td review` auto-creates a minimal handoff when none exists; that auto-handoff is not acceptable here because it leaves the structured fields empty and weakens review context (the validation run shipped handoffs whose `done`/`remaining`/`decisions`/`uncertain` were all `None`).
10. Gate the submission with `scripts/handoff_required.py` before `td review`. It detects whether `td handoff` supports the structured flags and verifies the four sections are populated (or, on older td, that the review reason carries them). Do not submit until it exits 0:
    ```bash
    python3 scripts/handoff_required.py --issue <id> --strict                 # structured path
    python3 scripts/handoff_required.py --issue <id> --review-reason "$SUMMARY" --strict  # only if td lacks the flags
    # exit 0 → safe to review; non-zero → handoff/reason is incomplete
    ```
    Then submit with `td review <id> --reason "<summary>"` unless the issue should remain blocked. See **Handoff Before Review** for the fallback (reason-section) path and the JSON descriptor.
11. Spawn or request independent review when risk warrants it, then close only through `td approve` according to the active td review mode.
12. Refresh `td status --json` and `td critical-path --json`; repeat until the configured stop condition is met.

## Agent Selection

Use the JSON config's `agents` section to decide which agent should act:

- **Codex**: default implementer/orchestrator for repository edits, tests, browser automation, screenshots, and local tool use.
- **Opencode**: use when the user configures it for implementation or review in repos where Opencode has the needed workspace and command access.
- **Pi**: use for planning, product judgment, requirements critique, conversational review, and human-readable UAT scripts; do not assume Pi can verify local UI state unless the config explicitly provides that capability.

When spawning a review agent, give it only the issue id, diff/context commands, acceptance criteria, and the review task. **Do not hard-code `--record-only` into the reviewer's instructions** — `--record-only` is rejected in the default `trusted` mode (`--record-only requires review_policy_mode=delegated`), which is exactly the recovery the validation run had to do. Instead, resolve the close path first (see **Review Policy → Detect the review mode first**) and hand the reviewer the exact approve command that mode accepts. `scripts/review_close_path.py --issue <id> --json` emits it deterministically (reviewer_command + how this session closes), so the reviewer is never asked to run a flag the database will reject; fall back to a concrete `td reject <id>` or the mode-appropriate `td approve` from its output. Keep the orchestrator responsible for final loop progression.

In Codex, discover the available multi-agent tool with `tool_search` before spawning. Only delegate when the user request or JSON config authorizes review/parallel agent work. Use Opencode or Pi through `agents.commands`; if an agent is named but unavailable, stop and report the missing capability instead of silently substituting a different reviewer. Prefer command arrays over shell strings, and respect `prompt_mode`:

- `stdin`: pipe or provide the task prompt on standard input.
- `arg`: append the task prompt as a final command argument.
- `manual`: print the exact prompt and pause for a human/operator to run it.

## UAT Gate

Treat UAT as mandatory for:

- UI flows, browser flows, CLIs with observable user workflows, auth/onboarding/payment/settings flows, data import/export, notifications, and anything with acceptance criteria phrased as user behavior.
- Visual layout changes where screenshots can reveal regressions.
- Cross-agent work where implementation happened outside the current session.

UAT evidence must include:

- Workflow steps executed.
- Tool used: browser automation, computer use, CLI, or other.
- Result and artifacts, including screenshot paths when applicable.
- Known gaps.

Escalate to human review when:

- Login, credentials, external services, local hardware, paid accounts, or permissions block automation.
- The app cannot be launched or observed from available tools.
- Screenshot analysis cannot confirm the named workflow.
- The issue depends on subjective product acceptance that the config marks as human-only.

Record this evidence as a manifest under the canonical artifact directory (see **Screenshot Artifact Strategy** below), not only as prose in the handoff. Reference the manifest path in the `td review --reason` and `td handoff` so a reviewer can find it.

## Screenshot Artifact Strategy

Screenshots must live inside the workspace next to the work they prove. Use one canonical directory, scoped per issue: `<work_dir>/<uat.artifacts_dir>/<issue_id>/`. Default `uat.artifacts_dir` is `uat-artifacts`.

For each UAT workflow, write an `evidence.json` manifest (schema `td-loop.uat-evidence/v1`) capturing: `issue_id`, `workflow`, `tool`, `result`, ordered `steps`, per-screenshot `artifacts`, `notes`, `recorded_at`, and `recorded_by_session`. Each artifact entry records its `label`, `source_path`, whether the tool `tool_emitted` it, whether it was `saved` into the workspace, the saved `path` when true, and a `note`.

Handle the common failure mode where a browser runtime **emits** a screenshot to a temp location (`/tmp`, `/private/tmp`, `$TMPDIR`) but **cannot write it into the workspace**:

1. Copy the screenshot from its temp source into `<uat.artifacts_dir>/<issue_id>/`.
2. Record `saved: true` with the new workspace `path` and an explicit note that the browser could not write directly and the image was relocated.
3. If relocation also fails, record `saved: false`, `tool_emitted: true`, and an explicit **emitted-but-not-written** note naming the source path and the failure. Treat the UAT as `unverifiable` and do not submit for review when `uat.block_on_unverifiable` is true.
4. If an expected screenshot is missing entirely, record `saved: false`, `tool_emitted: false`, with a note that UAT is unverifiable until a screenshot is captured.

Prefer a `scripts/record_uat_evidence.py`-style helper (copy, never move, so failed runs are retryable) and gate review submission with its `--strict` flag. The fixture at `td-loop-skill-validation` ships a reference implementation and a full spec in `docs/ARTIFACT_STRATEGY.md`.

## Persisted-State Browser UAT

When a workflow asserts that state **survives a reload** (localStorage / sessionStorage / IndexedDB, cookies, a cart or list that should persist across navigation), the UAT is only meaningful if it starts from a **known clean state**. The validation run hit this directly: its reload-persistence UAT accumulated duplicate "Coffee beans" rows across runs because the in-app browser surface could not reset localStorage, so both a correct implementation and a no-op left rows visible and the assertion stopped proving anything about the new code. Never assert against persisted storage that may carry data over from a previous run.

Before driving the workflow, resolve the strongest isolation strategy the browser surface actually supports and record it as evidence. `scripts/browser_uat_isolation.py` does both: it never mutates browser state from the CLI, but it picks the best strategy the declared `--surface` permits, emits the exact JS / commands to execute it, and writes a `browser-uat.json` manifest (schema `td-loop.browser-uat-isolation/v1`, appended to the issue's `evidence.log`) so a reviewer can confirm the UAT ran clean.

Strategies, strongest first — pick the first the surface can deliver:

1. **`clean-context`** — open a throwaway browser context / user-data-dir so storage starts empty (Playwright `newContext()`, Puppeteer `--user-data-dir`, Chrome `--user-data-dir $(mktemp -d)`). Preferred when the runtime supports it. Close the throwaway context afterward so it cannot leak into the next run.
2. **`reset`** — the surface can evaluate JS but cannot open a fresh profile, so inject the helper's emitted reset snippet (`localStorage.clear()`, `sessionStorage.clear()`, named `removeItem` keys, and `indexedDB.deleteDatabase(name)` for any `--indexed-db` databases) and reload **before** the workflow. Pass `--reset-keys k1,k2` and `--indexed-db name` to scope it when the app shares the origin; otherwise the snippet clears all local/session storage. IndexedDB deletion is async — reload only after the success event fires.
3. **`unique-data`** — neither isolation nor reset is available, so tag the test data with the helper's per-run token (e.g. `Coffee beans UAT-20260620T015030Z-e0ba`) and assert on that exact token after reload, so accumulated rows cannot create a false pass. This is the **weakest acceptable** strategy: it does not clean state, it only disambiguates. If the app dedupes by label, it is insufficient — escalate instead.
4. **`escalate`** — no clean-state path is available and the persisted state is required to verify the workflow. Do **not** assert against polluted storage. Block for human UAT (label `human-uat-required`) and name "clean browser profile or explicit storage reset" as the required human capability in the block comment, then resume through the **Human Escalation Protocol**.

Resolve and record before the workflow:

```bash
# The runtime can open a fresh profile/context:
python3 scripts/browser_uat_isolation.py --issue <id> \
  --workflow "reload persistence" --surface clean-context --json

# Can evaluate JS but not open a fresh profile:
python3 scripts/browser_uat_isolation.py --issue <id> \
  --workflow "reload persistence" --surface reset \
  --reset-keys cart,beans --indexed-db beansDB --origin http://localhost:3000 --json

# Only able to vary input data:
python3 scripts/browser_uat_isolation.py --issue <id> \
  --workflow "reload persistence" --surface unique-data \
  --data-label "Coffee beans" --json

# Nothing available and state is required — gate the UAT (strict exits non-zero):
python3 scripts/browser_uat_isolation.py --issue <id> \
  --workflow "reload persistence" --surface none --json --strict
```

Reference the manifest path in the `td review --reason` and `td handoff` so a reviewer can find the chosen strategy. Treat `escalate` under `--strict` as "do not submit until a clean-state path exists or a human unblocks" — the same gate as `block_on_unverifiable` for screenshots.

## Human Escalation Protocol

Because td statuses do not include `human_review_required`, encode it as blocked state plus metadata.

### Blocking for human UAT

When UAT cannot be automated, escalate **and name the required evidence** so the human tester knows exactly what to capture. Derive the field list from the issue's acceptance criteria, not a generic template — an email-delivery gate may require `sender`, `subject`, `timestamp`, `tester`, and the visible `package_name`:

```bash
td update <id> --labels existing,label,human-uat-required --comment "UAT escalation: <workflow>. Attempted: <tools>. Human steps: <steps>. Required evidence: sender, subject, timestamp, tester, package_name. Resume condition: human supplies the named evidence (preferred) or gives a pass/fail instruction recorded as operator attestation naming any missing field."
td block <id> --reason "human-uat-required: <workflow or blocker>"
```

### Resuming after human UAT (structured evidence)

Before resuming a loop, check for blocked `human-uat-required` issues in scope. If any are still blocked, stop and report them instead of working around them. If a human has unblocked, **do not** continue from a bare pass instruction — record structured human evidence first:

1. Read the block comment to recover the named required-evidence field list.
2. Capture what the human actually supplied into a manifest at `<uat.artifacts_dir>/<issue_id>/human-uat.json` (schema `td-loop.human-uat/v1`): the supplied `fields`, the list of `missing` required fields, the `result` (`pass`/`fail`), and an `attestation` (`operator`, `instruction`, `at`).
3. If required fields are missing, the resume is still allowed only when the operator explicitly attests; the manifest **must name each missing field** so the gap is visible to a reviewer rather than buried in a silent pass.
4. Write the manifest with `scripts/record_human_uat.py` (it appends one JSON line to the shared `evidence.log` alongside it) and gate the resume with `--strict`, which fails when the result is not `pass` or required fields are missing without an attestation.
5. Then transition td state:
   ```bash
   td update <id> --labels <existing-without-human-uat-required> \
                 --comment "Human UAT resume: result=<pass|fail>. Supplied: <keys>. Missing: <keys or none>. Attestation: <operator> @ <at>: <instruction>. Manifest: <path>"
   td unblock <id>
   ```
6. Reference the manifest path in the `td review --reason` and `td handoff`, then continue down the critical path from the same issue.

The canonical fields for an inbox/email-style gate are `sender`, `subject`, `timestamp`, `tester`, and the visible `package_name`. For other gates, copy the field names straight from the issue's acceptance criteria into both the block comment and the `--required` flags of the resume command.

## Handoff Before Review

Every handoff must carry all four structured sections — `done`, `remaining`, `decisions`, `uncertain` — populated as td handoff fields, not folded into a prose note. The validation run shipped handoffs whose four fields were all `None` (`td show <id> --json` reported empty structured sections) because implementers used `--note`/`-m` or let `td review` auto-create a minimal handoff. That loss of structure weakens review context and auditability, so gate every submission on it.

`scripts/handoff_required.py` detects whether the installed `td handoff` supports the structured flags (`--done`, `--remaining`, `--decision`, `--uncertain`) and adapts:

- **Structured flags supported (current td):** record the handoff with the four flags and verify before review.
  ```bash
  td handoff <id> \
    --done "<completed item>" \
    --remaining "<remaining item>" \
    --decision "<decision made>" \
    --uncertain "<question/uncertainty>"
  python3 scripts/handoff_required.py --issue <id> --strict   # exit 0 required
  ```
- **Structured flags NOT supported (older td):** the acceptance criteria fall back to the review reason/comment. Bake the four sections into `td review --reason` (or a `td comment`) and verify the planned text before submitting. Provide the reason via `--review-reason`, `--review-reason-file`, or `--review-reason-stdin`:
  ```bash
  python3 scripts/handoff_required.py --issue <id> \
    --review-reason "$SUMMARY" --strict
  ```
  The helper prints a pasteable `Done:/Remaining:/Decisions:/Uncertain:` template; the header check accepts both `Decision:` and `Decisions:`.

Machine-readable descriptor on stdout with `--json` (`schema: td-loop.handoff-required/v1`): `structured_flags_supported`, `strategy` (`structured-handoff` | `reason-sections`), `handoff_section_counts`, `missing_sections`, `review_safe`, plus the exact `handoff_command` or `fallback_review_note` to use. Degrades gracefully when `td` is unavailable (assumes unsupported, warns, and routes to the reason-section path) and never mutates td state. `--structured {auto,true,false}` overrides detection for offline/testing; `--handoff-json` reads a handoff object or full `td show --json` dump without touching td.

Run it immediately before `td review <id>` and treat any non-zero exit as "do not submit". Reference the descriptor's `missing_sections` in the handoff fix, not in the review reason of the structured path.

## Review Policy

Spawn an independent review agent when any of these are true:

- The issue touches auth, billing, security, migrations, data loss, permissions, public APIs, or shared infrastructure.
- The diff is large, cross-cutting, or hard to reason about.
- The loop config requires review for the issue type, priority, label, or epic.
- UAT passed but implementation correctness still needs code review.

### Detect the review mode first

Before requesting or spawning review, read the active td review policy and adapt to it. The mode dictates which close path is available, so never assume `--record-only` works — the validation run learned this the hard way when it asked a reviewer for `--record-only` in a `trusted`-mode database and got `--record-only requires review_policy_mode=delegated`.

Resolve the path deterministically with the helper, which reads the resolved mode and emits the exact approve/close commands for it:

```bash
# Human-readable path on stderr:
python3 scripts/review_close_path.py --issue <id>
# Machine-readable JSON on stdout (pipe into the reviewer prompt / td comment):
python3 scripts/review_close_path.py --issue <id> --json
# Warn + exit non-zero if the config's review.policy_mode disagrees with reality:
python3 scripts/review_close_path.py --issue <id> --expected <config.review.policy_mode> --json
```

The equivalent manual read (what the helper runs under the hood):

```bash
td feature get review_policy_mode     # e.g. review_policy_mode=trusted (source=default)
```

The mode dictates which close path is available, so do not assume:

- `trusted` (default): `--self-review` is allowed (audited) and `--record-only` is **not**. A fresh independent context approves and closes directly with `td approve <id> --reason "..."`. This is the path the loop must hand a reviewer in trusted mode — **not** `--record-only`.
- `delegated`: `--record-only` is allowed and `--self-review` is **not**. A reviewer records `td approve <id> --record-only --reason "..."`; any session then closes with `td approve <id> --reason "using recorded approval"`.
- `strict` / `balanced`: independent review is enforced by `DifferentReviewerGuard`; never attempt self-review, and `--record-only` is not an escape hatch either.

If the resolved mode differs from `review.policy_mode` in the config, warn the user (the helper exits non-zero with a warning) and proceed using the **resolved** (actual) mode. Do not mutate the user's td feature flags to force a match.

### Independent review is the default, not a last resort

For any non-minor issue, prefer an independent reviewer. When this session has no sub-agent tool (the common case for manual Codex/Pi sessions), a fresh context **is** the independent reviewer — use this recipe instead of falling back to self-review:

1. Capture a structured handoff (see **Handoff Before Review**): `td handoff <id> --done "..." --remaining "..." --decision "..." --uncertain "..."`, then gate it with `python3 scripts/handoff_required.py --issue <id> --strict`.
2. Stop and ask the user to start a fresh session or `/clear` for a new context. Do **not** call `td session --new` mid-work to manufacture one.
3. In the fresh session: `td reviewable`, read the diff and acceptance criteria, then run the command the resolved mode accepts. Resolve it with `python3 scripts/review_close_path.py --issue <id> --json` rather than guessing — in the **default `trusted` mode** the reviewer approves and closes directly:
   - `td approve <id> --reason "..."` (trusted, the default — approve+close directly), or
   - `td approve <id> --record-only --reason "..."` (delegated only — record, leaving close to any session).
4. Resume in the original context only to finish loop bookkeeping.

Reserve `td approve <id> --self-review --reason "..."` for `--minor` tasks or when the user explicitly opts in. "No sub-agent tool available" by itself is **not** sufficient justification for self-review on non-minor work; request a fresh reviewer context instead.

## Stop Conditions

Stop and summarize when:

- No eligible critical-path issue remains.
- A human UAT escalation is created or still pending.
- A configured budget is reached.
- Tests or UAT fail and the issue needs product or architectural input.
- Required agent/tool capability is unavailable.

The summary must list issue ids touched, state transitions, verification evidence, UAT result, review status, and next human action if any.
