#!/usr/bin/env python3
"""Require explicit handoff content before td review.

The td-loop validation run shipped handoffs whose four structured fields
(done, remaining, decisions, uncertain) were all None. Implementers had used a
prose `--note`/`-m` message, or let `td review` auto-create a minimal handoff,
so `td show <id> --json` reported empty structured sections and reviewers lost
auditability. This helper prevents that: BEFORE `td review`, it (1) detects
whether `td handoff` supports the structured flags, and (2) verifies the
handoff actually carries all four sections (or, when td cannot, that the review
reason/comment does).

Adapts to the installed td:

  - Structured flags supported (current td): gate review on a handoff whose
    `done`, `remaining`, `decisions`, and `uncertain` JSON fields are populated.
    Emits the exact `td handoff` command form so the loop never falls back to a
    prose note when structured flags exist.
  - Structured flags NOT supported (older td): the acceptance criteria fall back
    to the review reason/comment. Verifies the supplied reason text contains
    Done / Remaining / Decisions / Uncertain sections, and emits a pasteable
    template the loop can put into `td review --reason` or `td comment`.

Run this right before `td review <id>` and gate review on `--strict` (exit 0
means safe to review; non-zero means the handoff/reason is incomplete).

No third-party dependencies. Python 3.8+. macOS and Linux.

Usage:
  # Capability detection only (no issue):
  handoff_required.py --json

  # Verify an issue's handoff before review (structured path):
  handoff_required.py --issue td-abc1 --strict

  # Fallback path: verify the review reason carries the sections:
  handoff_required.py --issue td-abc1 --review-reason "$REASON" --strict
  handoff_required.py --issue td-abc1 --review-reason-file reason.txt --strict
  echo "$REASON" | handoff_required.py --issue td-abc1 --review-reason-stdin --strict

  # Override detection (testing/offline):
  handoff_required.py --issue td-abc1 --structured false --strict
  handoff_required.py --issue td-abc1 --structured true --strict

  # Override the handoff source (testing/offline) without touching td.
  # Accepts a raw handoff object OR a full `td show --json` dump; a raw
  # object is treated as present from its section content (no
  # session/timestamp metadata required):
  handoff_required.py --issue td-abc1 --handoff-json handoff.json --strict

  -w / --work-dir passes through to `td -w` (default: $TD_WORK_DIR or cwd).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# The four sections every handoff/review-reason must carry, in the order the
# td handoff JSON and the validation AC use. The td CLI flag for `decisions` is
# singular (--decision); the JSON field is plural (decisions).
SECTION_TO_FLAG = {
    "done": "--done",
    "remaining": "--remaining",
    "decisions": "--decision",
    "uncertain": "--uncertain",
}
REQUIRED_SECTIONS = tuple(SECTION_TO_FLAG.keys())

# Match a section header at the start of a line, case-insensitive, allowing
# common markdown/list prefixes and an optional trailing colon. Used only on
# the fallback review-reason text (when td lacks structured flags).
_REASON_HEADER_RE = re.compile(
    r"(?im)^[ \t]*[#*>\-]*[ \t]*(done|remaining|decisions?|uncertain)\b[ \t]*:?[ \t]*$"
)


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _section_count(value) -> int:
    """0 for None / empty string / empty list; else item count (or 1 for a string)."""
    if value is None:
        return 0
    if isinstance(value, str):
        return 1 if value.strip() else 0
    if isinstance(value, (list, tuple)):
        return len([v for v in value if isinstance(v, str) and v.strip()])
    # dicts/other: treat as populated if non-empty.
    return 1 if value else 0


def _normalize_issue(issue: str) -> None:
    if not issue:
        raise SystemExit("--issue is required for this operation.")
    if any(c in issue for c in "/\\") or issue in (".", ".."):
        raise SystemExit(
            f"--issue must be a plain id without path separators (got {issue!r})."
        )


def _query_handoff_support(work_dir: Optional[str]) -> tuple[bool, dict, str, list[str]]:
    """Probe `td handoff --help` for the structured flags.

    Returns (supported, flags_found, source, warnings). Degrades gracefully to
    "unsupported" when td is unavailable so the loop falls back to reason text
    rather than crashing.
    """
    warnings: list[str] = []
    cmd = ["td"]
    if work_dir:
        cmd += ["-w", work_dir]
    cmd += ["handoff", "--help"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        warnings.append("`td` not found on PATH; assuming structured flags unsupported.")
        return False, {}, "unsupported (td unavailable)", warnings
    except subprocess.TimeoutExpired:
        warnings.append("`td handoff --help` timed out; assuming structured flags unsupported.")
        return False, {}, "unsupported (td timed out)", warnings

    text = ((proc.stdout or "") + "\n" + (proc.stderr or ""))
    # Long flags appear as tokens like "--done", "--remaining". Collect them so
    # the membership test is exact (not a substring of another flag name).
    found: set[str] = set()
    for token in re.findall(r"--[a-zA-Z][a-zA-Z0-9\-]*", text):
        found.add(token)

    flags_found = {
        section: (flag in found)
        for section, flag in SECTION_TO_FLAG.items()
    }
    supported = all(flags_found.values())
    if not supported:
        missing_flags = [SECTION_TO_FLAG[s] for s, ok in flags_found.items() if not ok]
        warnings.append(
            "td handoff is missing structured flag(s): "
            + ", ".join(missing_flags)
            + "; falling back to review-reason sections."
        )
    source = "probed (`td handoff --help`)"
    return supported, flags_found, source, warnings


def _read_td_json(issue: str, work_dir: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
    """Run `td show <id> --json`. Returns (parsed_dict, error)."""
    cmd = ["td"]
    if work_dir:
        cmd += ["-w", work_dir]
    cmd += ["show", issue, "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return None, "`td` not found on PATH."
    except subprocess.TimeoutExpired:
        return None, "`td show --json` timed out."
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        err = (proc.stderr or "").strip()
        return None, f"`td show {issue} --json` exited {proc.returncode}" + (f": {err}" if err else "")
    try:
        return json.loads(out), None
    except json.JSONDecodeError as exc:
        return None, f"`td show {issue} --json` returned non-JSON: {exc}"


def _last_review_summary(issue_json: dict) -> Optional[str]:
    """Most recent review entry's reason/summary, for post-hoc fallback checks."""
    history = issue_json.get("review_history") or []
    if not history:
        return None
    last = history[-1]
    for key in ("summary", "reason", "comment"):
        val = last.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def reason_section_hits(text: str) -> dict:
    """Which required sections appear as headers in fallback reason text.

    `decisions` matches either 'decision' or 'decisions'.
    """
    hits = {section: False for section in REQUIRED_SECTIONS}
    if not text:
        return hits
    matched_words = {m.group(1).lower() for m in _REASON_HEADER_RE.finditer(text)}
    if "done" in matched_words:
        hits["done"] = True
    if "remaining" in matched_words:
        hits["remaining"] = True
    if "decision" in matched_words or "decisions" in matched_words:
        hits["decisions"] = True
    if "uncertain" in matched_words:
        hits["uncertain"] = True
    return hits


def build_descriptor(
    *,
    structured_supported: bool,
    source: str,
    issue: str,
    handoff: Optional[dict],
    handoff_supplied: bool = False,
    reason_text: Optional[str],
    reason_source: str,
    warnings: list[str],
) -> dict:
    required = list(REQUIRED_SECTIONS)

    if structured_supported:
        strategy = "structured-handoff"
        handoff_command = _handoff_command(issue)
        fallback_note = None
    else:
        strategy = "reason-sections"
        handoff_command = None
        fallback_note = _fallback_review_note()

    descriptor: dict = {
        "schema": "td-loop.handoff-required/v1",
        "issue": issue or None,
        "structured_flags_supported": structured_supported,
        "source": source,
        "required_sections": required,
        "strategy": strategy,
        "handoff_command": handoff_command,
        "fallback_review_note": fallback_note,
        "warnings": list(warnings),
    }

    if not issue:
        # Capability detection only; nothing to verify.
        descriptor["review_safe"] = None
        return descriptor

    # Compute section counts first; presence depends on them for the
    # --handoff-json (raw-object) path and on td metadata for the
    # production `td show` path.
    section_counts = {
        section: (_section_count(handoff.get(section)) if handoff else 0)
        for section in required
    }
    if handoff_supplied:
        # The handoff came from --handoff-json (testing/offline). A raw
        # handoff object legitimately lacks session/timestamp metadata, so
        # derive presence from section content, not metadata: a non-empty
        # supplied object with at least one populated section counts as
        # recorded. (A complete raw {done,remaining,decisions,uncertain}
        # object is review_safe even without a session/timestamp.)
        handoff_present = bool(handoff) and any(
            section_counts[s] > 0 for s in required
        )
    else:
        # Production `td show` path: unchanged. Presence comes from the
        # metadata td stamps when it records a handoff.
        handoff_present = bool(handoff) and bool(
            handoff.get("timestamp") or handoff.get("session")
        )
    descriptor["handoff_present"] = handoff_present
    descriptor["handoff_supplied"] = handoff_supplied
    descriptor["handoff_section_counts"] = section_counts
    descriptor["handoff_session"] = (handoff or {}).get("session")
    descriptor["review_reason_source"] = reason_source
    descriptor["review_reason_has_sections"] = None

    if structured_supported:
        missing = [s for s in required if section_counts[s] == 0]
        descriptor["missing_sections"] = missing
        descriptor["review_safe"] = (handoff_present and not missing)
        if missing:
            descriptor["warnings"].append(
                "Structured handoff is supported but these sections are empty: "
                + ", ".join(missing)
                + ". Populate them with `td handoff` flags before review."
            )
        elif not handoff_present:
            descriptor["warnings"].append(
                "No handoff recorded yet. Run the structured handoff command "
                "before `td review` so review is not auto-generated."
            )
    else:
        hits = reason_section_hits(reason_text or "")
        descriptor["review_reason_has_sections"] = hits
        missing = [s for s in required if not hits[s]]
        descriptor["missing_sections"] = missing
        if reason_text:
            descriptor["review_safe"] = not missing
            if missing:
                descriptor["warnings"].append(
                    "td lacks structured handoff flags and the review reason is "
                    "missing these sections: " + ", ".join(missing)
                    + ". Add Done/Remaining/Decisions/Uncertain sections."
                )
        else:
            # Cannot confirm the fallback requirement is met.
            descriptor["review_safe"] = False
            descriptor["warnings"].append(
                "td lacks structured handoff flags. Provide the planned review "
                "reason (--review-reason / --review-reason-file / "
                "--review-reason-stdin) so the Done/Remaining/Decisions/Uncertain "
                "sections can be verified before review."
            )
    return descriptor


def _handoff_command(issue: str) -> str:
    iid = issue or "<issue-id>"
    return (
        f"td handoff {iid} \\\n"
        f'  --done "<completed item>" \\\n'
        f'  --remaining "<remaining item>" \\\n'
        f'  --decision "<decision made>" \\\n'
        f'  --uncertain "<question/uncertainty>"'
    )


def _fallback_review_note() -> str:
    return (
        "Done:\n"
        "- <completed item>\n"
        "\n"
        "Remaining:\n"
        "- <remaining item>\n"
        "\n"
        "Decisions:\n"
        "- <decision made>\n"
        "\n"
        "Uncertain:\n"
        "- <question/uncertainty>"
    )


def _human_lines(desc: dict) -> list[str]:
    lines: list[str] = []
    if desc.get("issue"):
        lines.append(f"issue: {desc['issue']}")
    lines.append(
        f"structured handoff flags: {'supported' if desc['structured_flags_supported'] else 'NOT supported'}"
        f" (source: {desc['source']})"
    )
    lines.append(f"strategy: {desc['strategy']}")
    lines.append(f"required sections: {', '.join(desc['required_sections'])}")
    if desc["handoff_command"]:
        lines.append("handoff command:")
        for cline in desc["handoff_command"].splitlines():
            lines.append(f"  {cline}")
    if desc["fallback_review_note"]:
        lines.append("fallback review reason/comment template:")
        for nline in desc["fallback_review_note"].splitlines():
            lines.append(f"  {nline}")
    if desc.get("issue"):
        lines.append(f"handoff present: {desc['handoff_present']}")
        if desc["handoff_section_counts"] is not None:
            counts = desc["handoff_section_counts"]
            rendered = ", ".join(f"{k}={v}" for k, v in counts.items())
            lines.append(f"handoff section counts: {rendered}")
        if desc["review_reason_has_sections"] is not None:
            hits = desc["review_reason_has_sections"]
            rendered = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in hits.items())
            lines.append(f"reason section hits: {rendered}")
        lines.append(f"missing sections: {', '.join(desc['missing_sections']) or '(none)'}")
        safe = desc["review_safe"]
        lines.append(f"review_safe: {'yes' if safe else 'no' if safe is False else 'unknown'}")
    for w in desc["warnings"]:
        lines.append(f"  WARNING: {w}")
    return lines


def _resolve_structured(arg: str) -> tuple[Optional[bool], str]:
    if arg in ("auto", ""):
        return None, "probed"
    if arg in ("true", "yes", "1"):
        return True, "override (--structured true)"
    if arg in ("false", "no", "0"):
        return False, "override (--structured false)"
    raise SystemExit(f"--structured must be auto|true|false, got: {arg!r}")


def _read_reason_file(path: str) -> str:
    try:
        return Path(path).expanduser().read_text()
    except OSError as exc:
        raise SystemExit(f"could not read --review-reason-file {path!r}: {exc}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Require explicit handoff content before td review. Detects whether "
            "td handoff supports structured flags and verifies done/remaining/"
            "decisions/uncertain are populated (or, when td cannot, that the "
            "review reason carries those sections)."
        ),
    )
    parser.add_argument("--issue", default="", help="td issue id to verify (omit for capability detection only).")
    parser.add_argument(
        "--structured", default="auto",
        choices=["auto", "true", "false"],
        help="Override capability detection (testing/offline). default: auto (probe td).",
    )
    parser.add_argument(
        "--handoff-json", default="",
        help=(
            "Read the handoff from a JSON file instead of `td show` "
            "(testing/offline). Accepts a raw handoff object "
            "{done,remaining,decisions,uncertain} (treated as present from "
            "section content, no session/timestamp required) or a full "
            "`td show --json` dump."
        ),
    )
    parser.add_argument(
        "--review-reason", default="",
        help="Planned review reason text (fallback path verification). Inline string.",
    )
    parser.add_argument(
        "--review-reason-file", default="",
        help="Read planned review reason from a file. Use '-' for stdin.",
    )
    parser.add_argument(
        "--review-reason-stdin", action="store_true",
        help="Read planned review reason from stdin.",
    )
    parser.add_argument(
        "-w", "--work-dir",
        default=os.environ.get("TD_WORK_DIR", ""),
        help="td work-dir passed to `td -w` (default: $TD_WORK_DIR or cwd).",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Print the descriptor as JSON on stdout (human-readable summary still goes to stderr).",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero when review is not safe to submit (incomplete handoff/reason).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is None else sys.argv[1:])
    issue = (args.issue or "").strip()

    override, _ = _resolve_structured(args.structured)

    reason_text: Optional[str] = None
    reason_source = "none"
    warnings: list[str] = []

    # Capability detection (or override).
    if override is None:
        structured_supported, flags_found, source, warnings = _query_handoff_support(
            args.work_dir or None
        )
    else:
        structured_supported = override
        source = _
        flags_found = {
            section: override for section in SECTION_TO_FLAG
        }

    handoff: Optional[dict] = None
    if issue:
        _normalize_issue(issue)
        if args.handoff_json:
            try:
                payload = json.loads(Path(args.handoff_json).expanduser().read_text())
            except OSError as exc:
                raise SystemExit(f"could not read --handoff-json {args.handoff_json!r}: {exc}")
            except json.JSONDecodeError as exc:
                raise SystemExit(f"--handoff-json is not valid JSON: {exc}")
            # Accept either a raw handoff object or a full `td show --json` dump.
            handoff = payload.get("handoff", payload) if isinstance(payload, dict) else payload
        else:
            issue_json, err = _read_td_json(issue, args.work_dir or None)
            if err:
                warnings.append(err + " (cannot verify handoff).")
                handoff = None
            else:
                handoff = (issue_json or {}).get("handoff")

        # Resolve fallback review-reason text in priority order.
        if args.review_reason_stdin:
            reason_text = sys.stdin.read()
            reason_source = "stdin"
        elif args.review_reason_file == "-":
            reason_text = sys.stdin.read()
            reason_source = "stdin (-)"
        elif args.review_reason_file:
            reason_text = _read_reason_file(args.review_reason_file)
            reason_source = f"file ({args.review_reason_file})"
        elif args.review_reason:
            reason_text = args.review_reason
            reason_source = "inline (--review-reason)"
        else:
            # Post-hoc fallback: read the most recent review summary, if any.
            issue_json, _err = _read_td_json(issue, args.work_dir or None)
            summary = _last_review_summary(issue_json or {}) if issue_json else None
            if summary:
                reason_text = summary
                reason_source = "last review summary (post-hoc)"

    desc = build_descriptor(
        structured_supported=structured_supported,
        source=source,
        issue=issue,
        handoff=handoff,
        handoff_supplied=bool(args.handoff_json),
        reason_text=reason_text,
        reason_source=reason_source,
        warnings=warnings,
    )
    # Surface the per-flag probe detail only when it's informative.
    if override is None and flags_found:
        desc["structured_flags"] = flags_found

    if args.as_json:
        print(json.dumps(desc, indent=2))

    for line in _human_lines(desc):
        print(line, file=sys.stderr)

    if args.strict:
        safe = desc["review_safe"]
        if safe is False:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
