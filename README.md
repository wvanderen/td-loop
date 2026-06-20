# td-loop

An [Agent Skills](https://agentskills.io/specification)–standard skill that runs
a stateful [`td`](https://github.com/marcus/td) backlog execution loop: work down
the critical path one issue at a time, with UAT verification gates,
browser/computer-use screenshot evidence, explicit human-escalation pauses when
acceptance workflows cannot be verified, and review-policy-aware close
semantics. Any compliant harness can load it — confirmed in **Codex** and
**pi**; other Agent-Skills loaders (e.g. opencode) should work if they implement
the standard.

## Layout

```
SKILL.md                # skill entry (loaded by Codex)
agents/openai.yaml      # Codex UI metadata
references/config.md    # td-loop JSON config schema + suggested defaults
scripts/
  validate_config.py     # config validator (no third-party deps)
  record_human_uat.py    # structured human-UAT resume evidence (no third-party deps)
  review_close_path.py   # resolve td review mode → exact approve/close commands
  handoff_required.py    # require structured handoff content (done/remaining/decisions/uncertain) before review
  browser_uat_isolation.py # resolve a clean-state strategy (clean-context/reset/unique-data/escalate) for persisted-state browser UAT
```

## What the loop guarantees

- **`td` is ground truth**, not pasted issue headers. Reads live state before
  acting; never re-implements closed issues.
- **Detects the active review policy** (`td feature get review_policy_mode`)
  and uses the close path that actually works for that mode — `--self-review`
  (trusted), `--record-only` → any-session close (delegated), or the tool-enforced
  independent-review wall (strict/balanced). `scripts/review_close_path.py`
  resolves the mode and emits the exact reviewer/close command so the loop never
  asks a reviewer for a flag the database will reject (the validation run hit
  `--record-only requires review_policy_mode=delegated` in a trusted-mode DB).
- **Independent review is the default** for non-minor issues, including a
  concrete handoff → fresh-session → approve recipe for sessions with no
  sub-agent tool. Self-review is reserved for `--minor` work.
- **UAT is mandatory** for workflow-bearing issues, with a canonical screenshot
  artifact strategy that relocates browser-emitted temp screenshots into the
  workspace and records an explicit "emitted-but-not-written" note when it can't.
- **Human-only UAT gates resume with structured evidence**: when a human
  unblocks a `human-uat-required` issue, the loop records the supplied evidence
  fields (sender, subject, timestamp, …) and, when fields are missing, records
  an operator attestation that names each missing field before continuing —
  never a silent pass. See `scripts/record_human_uat.py`.
- **Structured handoff before review**: every submission is gated on a handoff
  whose `done`/`remaining`/`decisions`/`uncertain` fields are populated — not a
  prose note and not the auto-handoff `td review` creates when none exists
  (the validation run shipped handoffs with all four fields `None`).
  `scripts/handoff_required.py` detects whether `td handoff` supports the
  structured flags and verifies them, falling back to Done/Remaining/Decisions/
  Uncertain sections in the review reason on older td.
- **Persisted-state browser UAT starts from a clean state**: reload-persistence
  workflows (localStorage/sessionStorage/IndexedDB) resolve the strongest
  isolation the browser surface supports — clean-context, storage reset, or
  unique test data — and escalate to human UAT when none is available, never
  asserting against storage that may carry rows from a previous run (the
  validation run's duplicate "Coffee beans" rows). See
  `scripts/browser_uat_isolation.py`.
- **td writes are sequenced, never concurrent**: reads fan out in parallel, but every state-changing td command (`handoff`, `review`, `approve`, `block`, `unblock`) runs one at a time — a reviewer's `td approve` never races the orchestrator's `td handoff` — and the loop re-reads the affected issue(s) with `td show <id> --json` (and `td tree <id> --json` after a parent/epic auto-cascade — not `td show <id> --json --children`, which is a silent no-op on td 0.46.0) before deciding the next action, so a stale snapshot never drives the next decision (the validation run hit overlapping child/parent state when these ran concurrently). See **Sequencing td Writes** in `SKILL.md`.

## Install (global, any machine)

`install.sh` copies the skill into a harness skill directory so the agent
discovers it on next start. It targets any Agent-Skills-standard loader —
**Codex**, **pi**, or the harness-neutral `~/.agents/skills/` (scanned by pi
and any compliant loader). Idempotent: it safely replaces a previous install of
this skill and refuses to clobber a directory that belongs to a different skill.

```sh
./install.sh                      # default: all three targets below
./install.sh --target codex       # $CODEX_HOME/skills/        (default ~/.codex/skills)
./install.sh --target pi          # $PI_HOME/agent/skills/     (default ~/.pi/agent/skills)
./install.sh --target agents      # $AGENTS_HOME/skills/       (default ~/.agents/skills, harness-neutral)
./install.sh --target all         # install into all three (default)
```

Override the install root per target with an env var:

```sh
CODEX_HOME=/opt/codex   ./install.sh --target codex
PI_HOME=/custom/pi      ./install.sh --target pi
AGENTS_HOME=/opt/agents ./install.sh --target agents
```

Restart your agent harness to pick up the skill.

### Point pi at an existing Codex install

If you already keep skills in `~/.codex/skills`, pi can load them with no second
copy — add the directory to pi settings:

```json
{ "skills": ["~/.codex/skills"] }
```

## Develop on this machine (live edits)

For active development, symlink the install target at this repo so edits are
immediately live (no copy step). Pick the target(s) for the harness(es) you run
(run from inside the td-loop repo):

```sh
ln -s "$PWD" ~/.codex/skills/td-loop      # Codex
ln -s "$PWD" ~/.pi/agent/skills/td-loop   # pi
ln -s "$PWD" ~/.agents/skills/td-loop     # harness-neutral (pi + compliant loaders)
```

If a target already exists as a copied install, remove it first
(`rm -rf <target>/td-loop`) then run the `ln -s` above.

## Validate a td-loop config

```sh
python3 scripts/validate_config.py path/to/td-loop.config.json
```
