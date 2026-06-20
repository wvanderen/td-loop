#!/usr/bin/env python3
"""Record structured human-UAT evidence when resuming from a human-uat-required block.

When a human tester unblocks a human-only UAT gate, the loop must NOT continue
from a bare "pass" instruction. The validation run hit exactly this case: the
issue acceptance asked for sender, subject, timestamp, and the visible package
name, but the operator only supplied a pass instruction. This helper records
what the human actually supplied, and when required fields are missing it
records an explicit operator attestation that NAMES each missing field, so a
reviewer sees the gap instead of a silent pass.

Manifest schema `td-loop.human-uat/v1`, written to
<artifacts-dir>/<issue>/human-uat.json, with one JSON line appended to the
shared <artifacts-dir>/<issue>/evidence.log (the same append-only JSONL history
used by the screenshot evidence helper). The manifest is overwritten per resume
run; the log preserves every run.

No third-party dependencies. Python 3.8+. macOS and Linux.

Usage:
  record_human_uat.py \\
    --issue td-9164d6 \\
    --required sender --required subject --required timestamp \\
    --required tester --required package_name \\
    --field sender=noreply@example.com \\
    --field subject="Your package shipped" \\
    --field package_name="Coffee beans" \\
    --result pass \\
    --operator alice \\
    --instruction "pass" \\
    --artifacts-dir uat-artifacts

`--required` names a field the issue's acceptance expects. If omitted, required
defaults to the union of supplied fields and any `--missing` names. `--missing`
is a convenience to declare a required field that was not supplied without
listing every required field.

Exit code is non-zero under --strict when the resume is not safe to continue:
result is not `pass`, OR required fields are missing without an operator
attestation (a non-empty --instruction) that names them.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

RESULTS = {"pass", "fail"}


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _validate_issue_id(issue_id: str) -> None:
    """Reject issue ids that could escape the per-issue artifact directory."""
    if not issue_id or any(c in issue_id for c in "/\\") or issue_id in (".", ".."):
        raise SystemExit(
            f"--issue must be a plain id without path separators (got {issue_id!r}); "
            "it is used directly as the per-issue artifact directory name."
        )


def _parse_field(spec: str) -> tuple[str, str]:
    """Parse one '--field name=value' spec into (name, value)."""
    if "=" not in spec:
        raise SystemExit(f"--field must be '<name>=<value>', got: {spec!r}")
    name, value = spec.split("=", 1)
    name, value = name.strip(), value.strip()
    if not name:
        raise SystemExit(f"--field name is required, got: {spec!r}")
    return name, value


def build_manifest(
    *,
    issue_id: str,
    result: str,
    required: list[str],
    supplied: dict[str, str],
    operator: str,
    instruction: str,
    notes: list[str],
    session: str,
) -> dict:
    missing = [r for r in required if r not in supplied]

    # Attestation is "recorded" only when the operator gave an instruction
    # (e.g. "pass", "looks good"). The operator identity defaults to a stable
    # label so the manifest always names who resumed the gate.
    attestation = None
    if instruction:
        attestation = {
            "operator": operator,
            "instruction": instruction,
            "at": _utc_now(),
        }

    out_notes = list(notes)
    if missing and attestation:
        # Clearly name the missing metadata in the manifest itself, per the
        # acceptance criteria, so the gap travels with the evidence.
        out_notes.append(
            "Operator attested the resume; the following required evidence fields "
            "were not supplied by the human tester and rely on operator attestation: "
            + ", ".join(missing)
            + "."
        )
    elif missing and not attestation:
        out_notes.append(
            "Required evidence fields are missing and no operator attestation was "
            "recorded; the resume is not safe to continue: "
            + ", ".join(missing)
            + "."
        )

    return {
        "schema": "td-loop.human-uat/v1",
        "issue_id": issue_id,
        "result": result,
        "required_fields": list(required),
        "supplied": dict(supplied),
        "missing": missing,
        "attestation": attestation,
        "notes": out_notes,
        "recorded_at": _utc_now(),
        "recorded_by_session": session,
    }


def write_manifest(manifest: dict, artifacts_dir: Path) -> Path:
    issue_dir = artifacts_dir / manifest["issue_id"]
    issue_dir.mkdir(parents=True, exist_ok=True)
    latest = issue_dir / "human-uat.json"
    latest.write_text(json.dumps(manifest, indent=2) + "\n")
    # Append to the shared evidence.log so human-UAT runs sit alongside
    # screenshot runs in one append-only history per issue.
    with (issue_dir / "evidence.log").open("a") as handle:
        handle.write(json.dumps(manifest) + "\n")
    return latest


def _resume_is_safe(manifest: dict) -> bool:
    if manifest["result"] != "pass":
        return False
    if manifest["missing"] and manifest["attestation"] is None:
        return False
    return True


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record structured human-UAT resume evidence for a td-loop issue.",
    )
    parser.add_argument("--issue", required=True, help="td issue id, e.g. td-9164d6")
    parser.add_argument("--result", required=True, choices=sorted(RESULTS), help="Human UAT outcome")
    parser.add_argument(
        "--required", action="append", default=[], metavar="NAME",
        help="A required evidence field (from the issue acceptance / block comment). Repeatable.",
    )
    parser.add_argument(
        "--missing", action="append", default=[], metavar="NAME",
        help="Declare a required field that was not supplied, without listing every required field. Repeatable. Ignored when --required is given.",
    )
    parser.add_argument(
        "--field", action="append", default=[], metavar="NAME=VALUE",
        help="A supplied evidence field as '<name>=<value>'. Repeatable.",
    )
    parser.add_argument(
        "--operator", default=os.environ.get("TD_OPERATOR", "operator"),
        help="Operator/human who resumed the gate (default: $TD_OPERATOR or 'operator').",
    )
    parser.add_argument(
        "--instruction", default=os.environ.get("TD_HUMAN_UAT_INSTRUCTION", ""),
        help="Raw pass/fail instruction the operator gave (e.g. 'pass'). Empty means no attestation.",
    )
    parser.add_argument(
        "--note", action="append", default=[], metavar="TEXT",
        help="Free-form note. Repeatable.",
    )
    parser.add_argument(
        "--artifacts-dir", default="uat-artifacts",
        help="Canonical artifact directory (default: uat-artifacts). Relative to cwd.",
    )
    parser.add_argument("--session", default=os.environ.get("TD_SESSION", ""), help="td session id")
    parser.add_argument(
        "--print", action="store_true",
        help="Print the manifest JSON to stdout in addition to writing it.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero if the resume is not safe to continue (result not pass, or missing fields without attestation).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    _validate_issue_id(args.issue)
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()

    supplied: dict[str, str] = {}
    for spec in args.field:
        name, value = _parse_field(spec)
        if name in supplied:
            raise SystemExit(f"--field '{name}' supplied more than once")
        supplied[name] = value

    # Required is authoritative when given; otherwise infer from supplied + missing.
    if args.required:
        required = list(args.required)
    else:
        required = sorted(set(supplied) | set(args.missing))

    manifest = build_manifest(
        issue_id=args.issue,
        result=args.result,
        required=required,
        supplied=supplied,
        operator=args.operator,
        instruction=args.instruction.strip(),
        notes=args.note,
        session=args.session,
    )

    path = write_manifest(manifest, artifacts_dir)

    if args.print:
        print(json.dumps(manifest, indent=2))

    safe = _resume_is_safe(manifest)
    attested = manifest["attestation"] is not None
    summary = (
        f"human-uat: {path} | result={args.result} "
        f"supplied={len(manifest['supplied'])}/{len(manifest['required_fields'])} "
        f"missing={len(manifest['missing'])} attestation={'yes' if attested else 'no'} "
        f"safe={'yes' if safe else 'no'}"
    )
    # Summary and diagnostics go to stderr so --print stdout is pure JSON and
    # safe to pipe into jq / td comments.
    print(summary, file=sys.stderr)
    if manifest["missing"]:
        print("missing required fields (see manifest notes):", file=sys.stderr)
        for name in manifest["missing"]:
            print(f"  - {name}", file=sys.stderr)
        if not attested:
            print(
                "resume NOT safe: missing fields require an operator attestation (--instruction).",
                file=sys.stderr,
            )
    if args.strict and not safe:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
