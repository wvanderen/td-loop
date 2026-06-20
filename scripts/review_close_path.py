#!/usr/bin/env python3
"""Detect the active td review policy and emit the exact approve/close commands.

The td-loop validation run asked a spawned reviewer to run
`td approve <id> --record-only`, but the td database was in the default
`trusted` mode, where --record-only is rejected with
"--record-only requires review_policy_mode=delegated". The orchestrator then
had to recover with a --self-review. This helper prevents that failure: it
reads the RESOLVED review policy BEFORE review is requested and emits the exact
approve command that mode accepts, so reviewer instructions can never ask for a
flag the database will reject.

Run this right before spawning/requesting review (after `td review <id>`).
Pipe the JSON into the reviewer prompt, or read the human-readable path from
stderr. Compare against `review.policy_mode` in the td-loop config with
--expected to surface a config/resolved mismatch (the loop always proceeds on
the RESOLVED mode, never the configured expectation).

No third-party dependencies. Python 3.8+. macOS and Linux.

Usage:
  review_close_path.py                          # detect + print path to stderr
  review_close_path.py --issue td-abc1          # fill the issue id into commands
  review_close_path.py --json                   # machine-readable JSON on stdout
  review_close_path.py --json --issue td-abc1   # JSON with concrete commands
  review_close_path.py --expected delegated     # warn + exit 1 if resolved differs
  review_close_path.py --mode trusted           # override detection (testing/offline)
  review_close_path.py -w /path/to/repo         # td work-dir (resolves .td-root)

Mode semantics (mirrors `td approve --help`):
  trusted (default): --self-review allowed (audited); --record-only NOT allowed.
                     A fresh independent reviewer approves+close directly.
  delegated:         --record-only allowed; --self-review NOT allowed. Reviewer
                     records; any session closes afterward.
  strict/balanced:   DifferentReviewerGuard enforces independent review; neither
                     --self-review nor --record-only is an escape hatch.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Optional

SUPPORTED_MODES = ("strict", "balanced", "delegated", "trusted")
DEFAULT_MODE = "trusted"


def _parse_feature_line(line: str) -> tuple[Optional[str], str]:
    """Parse 'review_policy_mode=trusted (source=default)' -> ('trusted', 'default')."""
    if "=" not in line:
        return None, ""
    body = line.split("=", 1)[1]
    source = "default"
    if "(" in body and ")" in body:
        paren = body[body.index("(") + 1 : body.index(")")]
        body = body[: body.index("(")].strip()
        if paren.startswith("source="):
            source = paren[len("source=") :].strip()
    mode = body.strip().strip('"').strip("'")
    if mode not in SUPPORTED_MODES:
        return None, source
    return mode, source


def _query_td_mode(work_dir: Optional[str]) -> tuple[str, str, list[str]]:
    """Run `td feature get review_policy_mode`. Returns (mode, source, warnings)."""
    warnings: list[str] = []
    cmd = ["td"]
    if work_dir:
        cmd += ["-w", work_dir]
    cmd += ["feature", "get", "review_policy_mode"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        warnings.append("`td` not found on PATH; assuming default trusted mode.")
        return DEFAULT_MODE, "default (td unavailable)", warnings
    except subprocess.TimeoutExpired:
        warnings.append("`td feature get` timed out; assuming default trusted mode.")
        return DEFAULT_MODE, "default (td timed out)", warnings

    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        err = (proc.stderr or "").strip()
        warnings.append(
            f"`td feature get review_policy_mode` exited {proc.returncode}"
            + (f": {err}" if err else "")
            + "; assuming default trusted mode."
        )
        return DEFAULT_MODE, "default (td error)", warnings

    mode, source = _parse_feature_line(out)
    if mode is None:
        warnings.append(f"could not parse td output {out!r}; assuming default trusted mode.")
        return DEFAULT_MODE, "default (unparsed)", warnings
    return mode, source, warnings


def resolve_path(mode: str, issue: str) -> dict:
    """Build the close-path descriptor for a resolved mode and issue id."""
    iid = issue or "<issue-id>"
    if mode == "trusted":
        return {
            "mode": mode,
            "record_only_supported": False,
            "self_review_supported": True,
            "independent_review_enforced": False,
            "reviewer_command": f'td approve {iid} --reason "<review summary>"',
            "reviewer_role": "approve-and-close",
            "orchestrator_close_command": None,
            "orchestrator_close_note": (
                "A fresh independent reviewer runs reviewer_command and closes the "
                f"issue directly. This session should NOT close unless it is the "
                f"reviewer. For --minor tasks or explicit user opt-in only, this "
                f"session may self-review: td approve {iid} "
                f'--self-review --reason "<reason>".'
            ),
        }
    if mode == "delegated":
        return {
            "mode": mode,
            "record_only_supported": True,
            "self_review_supported": False,
            "independent_review_enforced": False,
            "reviewer_command": f'td approve {iid} --record-only --reason "<review summary>"',
            "reviewer_role": "record-only",
            "orchestrator_close_command": f'td approve {iid} --reason "using recorded approval"',
            "orchestrator_close_note": (
                "The reviewer records an approval without closing; any session "
                "(including this orchestrator) may then close once a record-only "
                "approval exists. Verify with `td reviewable --include-approved`."
            ),
        }
    # strict / balanced
    return {
        "mode": mode,
        "record_only_supported": False,
        "self_review_supported": False,
        "independent_review_enforced": True,
        "reviewer_command": f'td approve {iid} --reason "<review summary>"',
        "reviewer_role": "approve-and-close",
        "orchestrator_close_command": None,
        "orchestrator_close_note": (
            "DifferentReviewerGuard enforces an independent reviewer; both "
            "--self-review and --record-only are rejected. The independent "
            "reviewer approves+close directly; this session cannot close its own work."
        ),
    }


def build_descriptor(
    *,
    mode: str,
    source: str,
    issue: str,
    warnings: list[str],
    expected: Optional[str],
) -> dict:
    path = resolve_path(mode, issue)
    descriptor = {
        "schema": "td-loop.review-close-path/v1",
        "mode": path["mode"],
        "source": source,
        "record_only_supported": path["record_only_supported"],
        "self_review_supported": path["self_review_supported"],
        "independent_review_enforced": path["independent_review_enforced"],
        "reviewer_command": path["reviewer_command"],
        "reviewer_role": path["reviewer_role"],
        "orchestrator_close_command": path["orchestrator_close_command"],
        "orchestrator_close_note": path["orchestrator_close_note"],
        "warnings": list(warnings),
    }
    if expected:
        descriptor["expected_mode"] = expected
        descriptor["matches_expected"] = (expected == mode)
        if expected not in SUPPORTED_MODES:
            descriptor["warnings"].append(
                f"--expected {expected!r} is not a supported mode "
                f"({', '.join(SUPPORTED_MODES)}); comparison still performed."
            )
        if expected != mode:
            descriptor["warnings"].append(
                f"Config/resolved mismatch: config expects {expected!r} but the "
                f"resolved td mode is {mode!r}. Proceed on the RESOLVED mode; do "
                f"not mutate the user's td feature flags."
            )
    return descriptor


def _human_lines(desc: dict, issue: str) -> list[str]:
    mode = desc["mode"]
    lines = [
        f"review mode: {mode} (source: {desc['source']})",
        f"  record-only supported : {desc['record_only_supported']}",
        f"  self-review supported : {desc['self_review_supported']}",
        f"  independent enforced  : {desc['independent_review_enforced']}",
        f"  give the reviewer     : {desc['reviewer_command']}",
        f"    reviewer role       : {desc['reviewer_role']}",
    ]
    close = desc["orchestrator_close_command"]
    if close:
        lines.append(f"  this session closes   : {close}")
    else:
        lines.append("  this session closes   : (none — see note)")
    lines.append(f"  close note            : {desc['orchestrator_close_note']}")
    if issue:
        lines.insert(0, f"issue: {issue}")
    for w in desc["warnings"]:
        lines.append(f"  WARNING: {w}")
    return lines


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Detect the active td review policy and emit the exact approve/close "
            "commands so reviewer instructions match the resolved mode."
        ),
    )
    parser.add_argument("--issue", default="", help="td issue id to interpolate into commands (e.g. td-abc1).")
    parser.add_argument(
        "--mode",
        choices=list(SUPPORTED_MODES),
        help="Override detection (testing/offline). Skips the `td feature get` call.",
    )
    parser.add_argument(
        "-w", "--work-dir",
        default=os.environ.get("TD_WORK_DIR", ""),
        help="td work-dir passed to `td -w` (default: $TD_WORK_DIR or cwd).",
    )
    parser.add_argument(
        "--expected",
        help="review.policy_mode from the td-loop config. Exit 1 if it differs from the resolved mode.",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Print the descriptor as JSON on stdout (human-readable summary still goes to stderr).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.mode:
        mode, source, warnings = args.mode, "override (--mode)", []
    else:
        mode, source, warnings = _query_td_mode(args.work_dir or None)

    desc = build_descriptor(
        mode=mode,
        source=source,
        issue=args.issue,
        warnings=warnings,
        expected=args.expected,
    )

    if args.as_json:
        print(json.dumps(desc, indent=2))

    # Human-readable summary/diagnostics go to stderr so --json stdout stays pure.
    for line in _human_lines(desc, args.issue):
        print(line, file=sys.stderr)

    # Exit non-zero on a config/resolved mismatch so a gating wrapper can surface
    # it, while still printing the usable (resolved) path.
    if args.expected and args.expected != mode:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
