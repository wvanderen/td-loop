#!/usr/bin/env python3
"""Validate td-loop JSON config shape without third-party dependencies."""

from __future__ import annotations

import json
import sys
from pathlib import Path


AGENTS = {"codex", "opencode", "pi"}
REVIEW_MODES = {"always", "risk_based", "never"}
REVIEW_POLICY_MODES = {"strict", "balanced", "delegated", "trusted"}
UAT_METHODS = {"browser", "computer_use", "cli", "manual"}
PROMPT_MODES = {"stdin", "arg", "manual"}
AGENT_ROLES = {"orchestrator", "implementer", "reviewer", "advisor"}


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_config.py <td-loop-config.json>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    try:
        data = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001 - report parse/read failures uniformly
        print(f"invalid json: {exc}", file=sys.stderr)
        return 1

    errors: list[str] = []
    require(isinstance(data, dict), "config must be a JSON object", errors)
    if not isinstance(data, dict):
        print("\n".join(errors), file=sys.stderr)
        return 1

    require(data.get("version") == 1, "version must be 1", errors)

    td = data.get("td")
    require(isinstance(td, dict), "td must be an object", errors)
    if isinstance(td, dict):
        require(isinstance(td.get("work_dir", "."), str), "td.work_dir must be a string", errors)
        require(isinstance(td.get("critical_path_limit", 10), int), "td.critical_path_limit must be an integer", errors)
        scope = td.get("scope", {})
        require(isinstance(scope, dict), "td.scope must be an object", errors)
        if isinstance(scope, dict):
            for key in ("epics", "labels", "priorities"):
                require(is_string_list(scope.get(key, [])), f"td.scope.{key} must be a string array", errors)

    agents = data.get("agents")
    require(isinstance(agents, dict), "agents must be an object", errors)
    if isinstance(agents, dict):
        orchestrator = agents.get("orchestrator")
        require(orchestrator in AGENTS, "agents.orchestrator must be codex, opencode, or pi", errors)
        for key in ("implementers", "reviewers", "advisors"):
            values = agents.get(key, [])
            require(is_string_list(values), f"agents.{key} must be a string array", errors)
            if is_string_list(values):
                unknown = sorted(set(values) - AGENTS)
                require(not unknown, f"agents.{key} has unsupported agents: {', '.join(unknown)}", errors)
        commands = agents.get("commands", {})
        require(isinstance(commands, dict), "agents.commands must be an object", errors)
        if isinstance(commands, dict):
            unknown = sorted(set(commands) - AGENTS)
            require(not unknown, f"agents.commands has unsupported agents: {', '.join(unknown)}", errors)
            for agent, spec in commands.items():
                if agent not in AGENTS:
                    continue
                require(isinstance(spec, dict), f"agents.commands.{agent} must be an object", errors)
                if not isinstance(spec, dict):
                    continue
                command = spec.get("command")
                require(is_string_list(command) and bool(command), f"agents.commands.{agent}.command must be a non-empty string array", errors)
                prompt_mode = spec.get("prompt_mode", "stdin")
                require(prompt_mode in PROMPT_MODES, f"agents.commands.{agent}.prompt_mode must be stdin, arg, or manual", errors)
                roles = spec.get("roles", [])
                require(is_string_list(roles), f"agents.commands.{agent}.roles must be a string array", errors)
                if is_string_list(roles):
                    unknown_roles = sorted(set(roles) - AGENT_ROLES)
                    require(not unknown_roles, f"agents.commands.{agent}.roles has unsupported roles: {', '.join(unknown_roles)}", errors)

    review = data.get("review", {})
    require(isinstance(review, dict), "review must be an object", errors)
    if isinstance(review, dict):
        mode = review.get("spawn_review_agent", "risk_based")
        require(mode in REVIEW_MODES, "review.spawn_review_agent must be always, risk_based, or never", errors)
        require(isinstance(review.get("allow_self_review_for_minor", False), bool), "review.allow_self_review_for_minor must be boolean", errors)
        policy_mode = review.get("policy_mode", "trusted")
        require(policy_mode in REVIEW_POLICY_MODES, "review.policy_mode must be strict, balanced, delegated, or trusted", errors)
        require(isinstance(review.get("prefer_independent_review", True), bool), "review.prefer_independent_review must be boolean", errors)
        rules = review.get("require_independent_for", {})
        require(isinstance(rules, dict), "review.require_independent_for must be an object", errors)
        if isinstance(rules, dict):
            for key in ("priorities", "labels", "types"):
                require(is_string_list(rules.get(key, [])), f"review.require_independent_for.{key} must be a string array", errors)

    uat = data.get("uat")
    require(isinstance(uat, dict), "uat must be an object", errors)
    if isinstance(uat, dict):
        require(isinstance(uat.get("required", True), bool), "uat.required must be boolean", errors)
        methods = uat.get("methods", [])
        require(is_string_list(methods), "uat.methods must be a string array", errors)
        if is_string_list(methods):
            unknown = sorted(set(methods) - UAT_METHODS)
            require(not unknown, f"uat.methods has unsupported methods: {', '.join(unknown)}", errors)
        require(isinstance(uat.get("screenshot_required", False), bool), "uat.screenshot_required must be boolean", errors)
        require(isinstance(uat.get("artifacts_dir", "uat-artifacts"), str), "uat.artifacts_dir must be a string", errors)
        require(isinstance(uat.get("human_escalation_label", "human-uat-required"), str), "uat.human_escalation_label must be a string", errors)
        require(isinstance(uat.get("block_on_unverifiable", True), bool), "uat.block_on_unverifiable must be boolean", errors)

    budgets = data.get("budgets", {})
    require(isinstance(budgets, dict), "budgets must be an object", errors)
    if isinstance(budgets, dict):
        for key in ("max_issues_per_loop", "max_minutes"):
            value = budgets.get(key)
            if value is not None:
                require(isinstance(value, int) and value > 0, f"budgets.{key} must be a positive integer", errors)

    if errors:
        print("td-loop config is invalid:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("td-loop config is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
