#!/usr/bin/env python3
"""Resolve a clean-state strategy for persisted-state browser UAT.

The td-loop validation run shipped a "reload persistence" UAT that accumulated
duplicate "Coffee beans" rows across runs because the in-app browser surface
could not reset localStorage. Reload-persistence assertions (add rows, reload,
expect the rows to survive) are AMBIGUOUS when storage carries over from a
previous run: both a correct implementation and a no-op leave rows visible, so
the UAT stops proving anything about the new code.

This helper resolves the strongest isolation strategy the available browser
surface supports and emits the exact commands / JS to execute it, plus an
evidence manifest that records what was chosen so a reviewer can confirm the
UAT ran from a clean state. It NEVER mutates browser state itself (it cannot,
from a CLI) — it hands the loop a deterministic plan and records it, mirroring
review_close_path.py (resolve a path) + record_human_uat.py (write evidence).

Strategies, strongest first:
  clean-context : spawn a throwaway browser context / user-data-dir so storage
                  starts empty. Preferred when the runtime supports it (Playwright
                  newContext, Puppeteer/Chrome `--user-data-dir <tmp>`).
  reset         : the browser surface can evaluate JS, so before the workflow
                  inject the emitted storage-reset snippet (localStorage/sessionStorage
                  clear + optional IndexedDB delete) and reload. Requires knowing
                  the storage keys, or clearing everything.
  unique-data   : neither isolation nor reset is available; tag the test data
                  with a per-run token so assertions key on the unique value
                  and never collide with prior runs' rows. Weakest acceptable
                  strategy; does not actually clean state, only disambiguates.
  escalate      : none of the above can be applied and the persisted state is
                  required to verify the workflow; the loop must block for human
                  UAT instead of asserting against polluted storage.

Run this BEFORE driving a persisted-state browser workflow. Pipe the JSON into
the loop's UAT note / td handoff, or read the human-readable plan from stderr.
Gate the UAT on `--strict`, which exits non-zero when the resolved strategy is
`escalate` (persisted-state UAT cannot run safely from this surface).

No third-party dependencies. Python 3.8+. macOS and Linux.

Usage:
  # Strongest available: the runtime can open a clean context/profile.
  browser_uat_isolation.py --issue td-cb6ec8 \\
      --workflow "reload persistence" \\
      --surface clean-context --json

  # Can evaluate JS but cannot open a fresh profile: emit the reset snippet.
  browser_uat_isolation.py --issue td-cb6ec8 \\
      --workflow "reload persistence" \\
      --surface reset --reset-keys cart,beans \\
      --indexed-db beansDB --origin http://localhost:3000 --json

  # No isolation / no JS eval: fall back to unique test data.
  browser_uat_isolation.py --issue td-cb6ec8 \\
      --workflow "reload persistence" \\
      --surface unique-data --data-label "Coffee beans" --json

  # Nothing available and state is required: escalate (strict exits non-zero).
  browser_uat_isolation.py --issue td-cb6ec8 \\
      --workflow "reload persistence" \\
      --surface none --json --strict

  # Override strategy (testing/offline) without declaring a surface:
  browser_uat_isolation.py --issue td-cb6ec8 --strategy reset --json

  -w / --work-dir is unused (no td calls); accepted for CLI symmetry with the
  other helpers and ignored.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Optional

# Surface capability -> strongest strategy that surface can deliver. The order
# encodes "strongest acceptable isolation first" so resolve_strategy() can pick
# the best strategy a declared surface permits, and surface_for_strategy() can
# name the minimum surface a chosen strategy requires.
STRATEGY_ORDER = ("clean-context", "reset", "unique-data", "escalate")
SURFACE_TO_STRATEGY = {
    "clean-context": "clean-context",
    "reset": "reset",
    "unique-data": "unique-data",
    "none": "escalate",
}
# Minimum surface capability each strategy needs.
STRATEGY_TO_MIN_SURFACE = {
    "clean-context": "clean-context",
    "reset": "reset",
    "unique-data": "unique-data",
    "escalate": "none",
}


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _validate_issue_id(issue_id: str) -> None:
    """Reject issue ids that could escape the per-issue artifact directory."""
    if not issue_id or any(c in issue_id for c in "/\\") or issue_id in (".", ".."):
        raise SystemExit(
            f"--issue must be a plain id without path separators (got {issue_id!r}); "
            "it is used directly as the per-issue artifact directory name."
        )


def _parse_csv(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _run_token() -> str:
    """Stable-ish, collision-resistant token for the unique-data strategy.

    UTC seconds + 4 hex chars is more than enough to disambiguate manual UAT
    runs of the same workflow on the same second, and stays human-readable in a
    UI row label (unlike a full uuid4).
    """
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(2)}"


def resolve_strategy(surface: str, strategy: Optional[str]) -> tuple[str, str, list[str]]:
    """Pick the strategy and report how it was chosen + any warnings.

    Returns (strategy, source, warnings). `--strategy` overrides the surface
    only when the declared surface can actually deliver it — asking for
    `clean-context` on a `none` surface is a contradiction and is downgraded to
    what the surface supports, with a warning, so the manifest never claims an
    isolation the browser cannot provide.
    """
    warnings: list[str] = []
    if surface not in SURFACE_TO_STRATEGY:
        warnings.append(
            f"unknown --surface {surface!r}; treating as 'none'. "
            f"Allowed: {', '.join(SURFACE_TO_STRATEGY)}."
        )
        surface_resolved = "none"
    else:
        surface_resolved = surface

    surface_strategy = SURFACE_TO_STRATEGY[surface_resolved]

    if strategy:
        if strategy not in STRATEGY_TO_MIN_SURFACE:
            raise SystemExit(
                f"--strategy must be one of {', '.join(STRATEGY_ORDER)}, got: {strategy!r}"
            )
        min_surface = STRATEGY_TO_MIN_SURFACE[strategy]
        if _strategy_rank(strategy) < _strategy_rank(surface_strategy):
            # Surface cannot deliver the requested strategy; downgrade.
            warnings.append(
                f"--strategy {strategy!r} requires surface {min_surface!r} but "
                f"--surface is {surface_resolved!r}; downgrading to "
                f"{surface_strategy!r}. Declare a stronger --surface to use it."
            )
            return surface_strategy, f"surface ({surface_resolved}); strategy downgraded", warnings
        return strategy, f"--strategy ({strategy}); surface={surface_resolved}", warnings

    return surface_strategy, f"surface ({surface_resolved})", warnings


def _strategy_rank(strategy: str) -> int:
    """Lower rank = stronger isolation. escalate ranks last."""
    return STRATEGY_ORDER.index(strategy)


def _clean_context_plan(origin: Optional[str]) -> list[str]:
    plan = [
        "Open a throwaway browser context so storage starts empty, instead of "
        "reusing the in-app/default profile that carries localStorage across runs.",
    ]
    plan.append(
        "Playwright (Node): const ctx = await browser.newContext(); "
        "const page = await ctx.newPage(); await page.goto(<origin>);"
    )
    plan.append(
        "Puppeteer (Node): const browser = await puppeteer.launch({userDataDir: <tmp-dir>});"
    )
    plan.append(
        "Chrome CLI: chrome --user-data-dir=$(mktemp -d) <origin>"
    )
    if origin:
        plan.append(f"Navigate to the app origin {origin} inside the fresh context before driving the workflow.")
    plan.append("Close the throwaway context after the workflow so it cannot leak into the next run.")
    return plan


def _reset_snippet(keys: list[str], dbs: list[str]) -> str:
    """Exact JS to clear persisted storage before the workflow.

    Always clears localStorage/sessionStorage; if specific keys are named, also
    removes them by name (belt-and-suspenders with clear()). IndexedDB databases
    must be named and deleted explicitly because there is no generic clear-all.
    """
    lines = ["// Run this in a DevTools/console or via the browser's JS-eval surface, BEFORE the workflow:"]
    lines.append("localStorage.clear();")
    lines.append("sessionStorage.clear();")
    for k in keys:
        # Single-quote-escape the key for JS; escape backslash/quote.
        safe = k.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"localStorage.removeItem('{safe}');  // named key")
    if dbs:
        lines.append("// IndexedDB has no clear-all; delete each named database and wait for the result.")
        for db in dbs:
            safe = db.replace("\\", "\\\\").replace("'", "\\'")
            lines.append(
                f"indexedDB.deleteDatabase('{safe}');"
            )
        lines.append("// NOTE: deleting IndexedDB is async; reload only after the success event fires.")
    else:
        lines.append("// (No IndexedDB databases named; add --indexed-db <name> if the app persists there.)")
    lines.append("location.reload();  // reload so the app re-reads the now-empty storage")
    return "\n".join(lines)


def _reset_plan(keys: list[str], dbs: list[str], origin: Optional[str]) -> list[str]:
    plan = [
        "The browser surface can evaluate JS but cannot open a fresh profile, so "
        "reset persisted storage in place immediately before the workflow.",
    ]
    if origin:
        plan.append(f"Navigate to {origin} first so the reset runs against the app's origin (storage is per-origin).")
    if not keys and not dbs:
        plan.append(
            "No specific keys/databases named; the snippet clears ALL localStorage and sessionStorage. "
            "Pass --reset-keys k1,k2 and/or --indexed-db db1 to scope it when the app shares the origin."
        )
    else:
        if keys:
            plan.append("Named localStorage/sessionStorage keys to also remove: " + ", ".join(keys) + ".")
        if dbs:
            plan.append("Named IndexedDB databases to delete: " + ", ".join(dbs) + " (async; wait for success).")
    plan.append("Inject the emitted JS snippet (see reset_snippet in the descriptor) and reload before driving the workflow.")
    plan.append(
        "Record the reset as part of the UAT manifest (this helper does it for you) so a reviewer can see "
        "state was cleared, not assumed."
    )
    return plan


def _unique_data_plan(label: str, token: str) -> list[str]:
    plan = [
        "Neither a clean context nor a JS reset is available on this surface, so "
        "disambiguate by tagging the test data with a per-run token. This does NOT "
        "clean prior state; it only makes the assertion key on a value no previous "
        "run could have produced, so accumulated rows do not create false passes.",
    ]
    plan.append(
        f"Use the data label: '{label} UAT-{token}' (token appended). Assert the reloaded "
        f"view contains exactly the row(s) tagged with this token, not any prior 'Coffee beans' rows."
    )
    plan.append(
        "If the app dedupes by label, this strategy is insufficient — escalate to human UAT instead, "
        "because a unique tag cannot disambiguate a dedup-on-label workflow."
    )
    return plan


def _escalate_plan(workflow: str) -> list[str]:
    return [
        f"No clean-state path is available for persisted-state workflow '{workflow}' on this surface.",
        "Do NOT assert against storage that may carry rows from a previous run (the validation run's failure mode).",
        "Block the issue for human UAT (label human-uat-required) and name 'clean browser profile or explicit "
        "storage reset' as the required human capability in the block comment.",
        "Record this strategy with this helper so the escalation is auditable; --strict exits non-zero here.",
    ]


def build_descriptor(
    *,
    issue_id: str,
    workflow: str,
    surface: str,
    strategy: str,
    source: str,
    reset_keys: list[str],
    indexed_dbs: list[str],
    origin: Optional[str],
    data_label: str,
    token: str,
    notes: list[str],
    session: str,
    warnings: list[str],
) -> dict:
    if strategy == "clean-context":
        plan = _clean_context_plan(origin)
        reset_snippet = None
        data_token = None
    elif strategy == "reset":
        plan = _reset_plan(reset_keys, indexed_dbs, origin)
        reset_snippet = _reset_snippet(reset_keys, indexed_dbs)
        data_token = None
    elif strategy == "unique-data":
        token = token or _run_token()
        plan = _unique_data_plan(data_label or "<data label>", token)
        reset_snippet = None
        data_token = token
    else:  # escalate
        plan = _escalate_plan(workflow)
        reset_snippet = None
        data_token = None

    descriptor: dict = {
        "schema": "td-loop.browser-uat-isolation/v1",
        "issue_id": issue_id,
        "workflow": workflow,
        "surface": surface,
        "strategy": strategy,
        "source": source,
        "origin": origin,
        "reset_keys": list(reset_keys),
        "indexed_databases": list(indexed_dbs),
        "data_label": data_label or None,
        "data_token": data_token,
        "plan": plan,
        "reset_snippet": reset_snippet,
        "notes": list(notes),
        "warnings": list(warnings),
        "recorded_at": _utc_now(),
        "recorded_by_session": session,
    }
    # Surface the precedence so a reviewer sees why this strategy was chosen.
    descriptor["strategy_precedence"] = list(STRATEGY_ORDER)
    return descriptor


def write_manifest(manifest: dict, artifacts_dir: Path) -> Path:
    issue_dir = artifacts_dir / manifest["issue_id"]
    issue_dir.mkdir(parents=True, exist_ok=True)
    latest = issue_dir / "browser-uat.json"
    latest.write_text(json.dumps(manifest, indent=2) + "\n")
    # Append to the shared evidence.log so browser-isolation runs sit alongside
    # screenshot / human-UAT runs in one append-only history per issue.
    with (issue_dir / "evidence.log").open("a") as handle:
        handle.write(json.dumps(manifest) + "\n")
    return latest


def _human_lines(desc: dict) -> list[str]:
    lines = [
        f"issue: {desc['issue_id']}",
        f"workflow: {desc['workflow']}",
        f"surface: {desc['surface']}",
        f"strategy: {desc['strategy']} (source: {desc['source']})",
        f"precedence (strongest first): {', '.join(desc['strategy_precedence'])}",
    ]
    if desc.get("origin"):
        lines.append(f"origin: {desc['origin']}")
    if desc["reset_keys"]:
        lines.append(f"reset keys: {', '.join(desc['reset_keys'])}")
    if desc["indexed_databases"]:
        lines.append(f"indexedDB databases: {', '.join(desc['indexed_databases'])}")
    if desc.get("data_token"):
        lines.append(f"unique data token: {desc['data_token']}")
        if desc.get("data_label"):
            lines.append(f"  use data label: '{desc['data_label']} UAT-{desc['data_token']}'")
    lines.append("plan:")
    for step in desc["plan"]:
        lines.append(f"  - {step}")
    if desc.get("reset_snippet"):
        lines.append("reset snippet:")
        for sline in desc["reset_snippet"].splitlines():
            lines.append(f"  {sline}")
    for w in desc["warnings"]:
        lines.append(f"  WARNING: {w}")
    return lines


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve a clean-state strategy for persisted-state browser UAT and "
            "record it as evidence. Emits the exact JS/commands for clean-context, "
            "storage-reset, or unique-data strategies, or escalates when none apply."
        ),
    )
    parser.add_argument("--issue", required=True, help="td issue id, e.g. td-cb6ec8")
    parser.add_argument(
        "--workflow", required=True,
        help="Short name of the persisted-state workflow (e.g. 'reload persistence').",
    )
    parser.add_argument(
        "--surface", default="none",
        choices=list(SURFACE_TO_STRATEGY.keys()),
        help=(
            "Strongest isolation capability the browser surface exposes. "
            "clean-context (fresh profile/context) > reset (JS eval) > unique-data "
            "(vary input only) > none. Default: none."
        ),
    )
    parser.add_argument(
        "--strategy",
        choices=list(STRATEGY_ORDER),
        help="Override strategy (testing/offline). Downgraded if --surface cannot deliver it.",
    )
    parser.add_argument(
        "--reset-keys", default="",
        help="Comma-separated localStorage/sessionStorage keys to remove (reset strategy). Optional; defaults to clear-all.",
    )
    parser.add_argument(
        "--indexed-db", action="append", default=[], metavar="NAME",
        help="IndexedDB database to delete (reset strategy). Repeatable.",
    )
    parser.add_argument(
        "--origin", default="",
        help="App origin the UAT targets (recorded + used in plan text).",
    )
    parser.add_argument(
        "--data-label", default="",
        help="Base label for unique-data strategy (token is appended). E.g. 'Coffee beans'.",
    )
    parser.add_argument(
        "--note", action="append", default=[], metavar="TEXT",
        help="Free-form note. Repeatable.",
    )
    parser.add_argument(
        "--artifacts-dir", default="uat-artifacts",
        help="Canonical artifact directory (default: uat-artifacts). Relative to cwd.",
    )
    parser.add_argument(
        "--no-write", action="store_true",
        help="Do not write the manifest (planning/dry-run). Still prints JSON/summary.",
    )
    parser.add_argument("--session", default=os.environ.get("TD_SESSION", ""), help="td session id")
    parser.add_argument(
        "--print", action="store_true",
        help="Print the manifest JSON to stdout in addition to writing it.",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Print the descriptor as JSON on stdout (human-readable summary still goes to stderr).",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero when the resolved strategy is 'escalate' (persisted-state UAT cannot run safely).",
    )
    parser.add_argument(
        "-w", "--work-dir", default="",
        help="Accepted for CLI symmetry with the other helpers; this script makes no td calls.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    _validate_issue_id(args.issue)
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()

    reset_keys = _parse_csv(args.reset_keys)
    indexed_dbs = [db.strip() for db in args.indexed_db if db.strip()]

    strategy, source, warnings = resolve_strategy(args.surface, args.strategy)

    descriptor = build_descriptor(
        issue_id=args.issue,
        workflow=args.workflow,
        surface=args.surface,
        strategy=strategy,
        source=source,
        reset_keys=reset_keys,
        indexed_dbs=indexed_dbs,
        origin=(args.origin or None),
        data_label=args.data_label,
        token="",  # generated inside build_descriptor for unique-data
        notes=args.note,
        session=args.session,
        warnings=warnings,
    )

    if not args.no_write:
        path = write_manifest(descriptor, artifacts_dir)
        descriptor["manifest_path"] = str(path)
    else:
        descriptor["manifest_path"] = None

    out_json = json.dumps(descriptor, indent=2)
    if args.as_json or args.print:
        print(out_json)

    for line in _human_lines(descriptor):
        print(line, file=sys.stderr)
    if descriptor.get("manifest_path"):
        print(f"manifest: {descriptor['manifest_path']}", file=sys.stderr)
    else:
        print("manifest: (not written, --no-write)", file=sys.stderr)

    if args.strict and strategy == "escalate":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
