# td-loop

A Codex skill that runs a stateful [`td`](https://github.com/marcus/td) backlog
execution loop: work down the critical path one issue at a time, with UAT
verification gates, browser/computer-use screenshot evidence, explicit
human-escalation pauses when acceptance workflows cannot be verified, and
review-policy-aware close semantics.

## Layout

```
SKILL.md                # skill entry (loaded by Codex)
agents/openai.yaml      # Codex UI metadata
references/config.md    # td-loop JSON config schema + suggested defaults
scripts/
  validate_config.py    # config validator (no third-party deps)
```

## What the loop guarantees

- **`td` is ground truth**, not pasted issue headers. Reads live state before
  acting; never re-implements closed issues.
- **Detects the active review policy** (`td feature get review_policy_mode`) and
  uses the close path that actually works for that mode — `--self-review`
  (trusted), `--record-only` → any-session close (delegated), or the tool-enforced
  independent-review wall (strict/balanced).
- **Independent review is the default** for non-minor issues, including a
  concrete handoff → fresh-session → approve recipe for sessions with no
  sub-agent tool. Self-review is reserved for `--minor` work.
- **UAT is mandatory** for workflow-bearing issues, with a canonical screenshot
  artifact strategy that relocates browser-emitted temp screenshots into the
  workspace and records an explicit "emitted-but-not-written" note when it can't.

## Install (global, any machine)

`install.sh` copies the skill into `$CODEX_HOME/skills/td-loop` (default
`~/.codex/skills/td-loop`) so Codex discovers it on next start. Idempotent: it
refuses to clobber a directory that isn't this skill.

```sh
./install.sh
# or into a non-default Codex home:
CODEX_HOME=/opt/codex ./install.sh
```

Restart Codex to pick up the skill.

## Develop on this machine (live edits)

For active development, symlink the install target at this repo so edits are
immediately live in Codex (no copy step):

```sh
ln -s "$PWD/td-loop" ~/.codex/skills/td-loop   # run from ~/dev
```

If `~/.codex/skills/td-loop` already exists as a copied install, remove it
first (`rm -rf ~/.codex/skills/td-loop`) then run the `ln -s` above.

## Validate a td-loop config

```sh
python3 scripts/validate_config.py path/to/td-loop.config.json
```
