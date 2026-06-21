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
UAT_REQUIREMENT_MODES = {"default", "always", "workflow-bearing", "never"}
PERSISTED_STATE_SURFACES = {"auto", "clean-context", "reset", "unique-data", "none"}
VALIDATION_RUN_STAGES = {"before_loop", "after_implementation", "before_review", "after_review"}
VALIDATION_ARTIFACTS = {
    "uat_manifest",
    "screenshot",
    "browser_isolation_manifest",
    "human_uat_manifest",
    "handoff_descriptor",
}


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def require_string_list_fields(prefix: str, value: object, keys: tuple[str, ...], errors: list[str]) -> None:
    require(isinstance(value, dict), f"{prefix} must be an object", errors)
    if not isinstance(value, dict):
        return
    for key in keys:
        require(is_string_list(value.get(key, [])), f"{prefix}.{key} must be a string array", errors)


def validate_preference_rules(prefix: str, value: object, errors: list[str]) -> None:
    require_string_list_fields(prefix, value, ("priorities", "labels", "types", "workflows"), errors)


def validate_validation_command(prefix: str, value: object, errors: list[str]) -> None:
    require(isinstance(value, dict), f"{prefix} must be an object", errors)
    if not isinstance(value, dict):
        return
    require(isinstance(value.get("name"), str) and bool(value.get("name", "").strip()), f"{prefix}.name must be a non-empty string", errors)
    require(is_string_list(value.get("command")) and bool(value.get("command")), f"{prefix}.command must be a non-empty string array", errors)
    run = value.get("run", "before_review")
    require(run in VALIDATION_RUN_STAGES, f"{prefix}.run must be one of: {', '.join(sorted(VALIDATION_RUN_STAGES))}", errors)
    require(isinstance(value.get("required", True), bool), f"{prefix}.required must be boolean", errors)


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

    preferences = data.get("preferences", {})
    require(isinstance(preferences, dict), "preferences must be an object", errors)
    if isinstance(preferences, dict):
        pref_uat = preferences.get("uat", {})
        require(isinstance(pref_uat, dict), "preferences.uat must be an object", errors)
        if isinstance(pref_uat, dict):
            mode = pref_uat.get("requirement_mode", "default")
            require(mode in UAT_REQUIREMENT_MODES, "preferences.uat.requirement_mode must be default, always, workflow-bearing, or never", errors)
            for key in ("required_for", "skip_for", "human_only_for", "screenshot_required_for"):
                validate_preference_rules(f"preferences.uat.{key}", pref_uat.get(key, {}), errors)
            require(is_string_list(pref_uat.get("evidence_required_fields", [])), "preferences.uat.evidence_required_fields must be a string array", errors)
            persisted = pref_uat.get("persisted_state", {})
            require(isinstance(persisted, dict), "preferences.uat.persisted_state must be an object", errors)
            if isinstance(persisted, dict):
                surface = persisted.get("default_surface", "auto")
                require(surface in PERSISTED_STATE_SURFACES, "preferences.uat.persisted_state.default_surface must be auto, clean-context, reset, unique-data, or none", errors)
                require(isinstance(persisted.get("allow_unique_data_fallback", True), bool), "preferences.uat.persisted_state.allow_unique_data_fallback must be boolean", errors)
                require(isinstance(persisted.get("block_on_no_clean_state", True), bool), "preferences.uat.persisted_state.block_on_no_clean_state must be boolean", errors)

        validation = preferences.get("validation", {})
        require(isinstance(validation, dict), "preferences.validation must be an object", errors)
        if isinstance(validation, dict):
            for key in ("require_config_validation", "require_handoff_gate", "require_uat_evidence_manifest", "block_on_failure", "allow_warnings"):
                require(isinstance(validation.get(key, True), bool), f"preferences.validation.{key} must be boolean", errors)
            artifacts = validation.get("required_artifacts", [])
            require(is_string_list(artifacts), "preferences.validation.required_artifacts must be a string array", errors)
            if is_string_list(artifacts):
                unknown = sorted(set(artifacts) - VALIDATION_ARTIFACTS)
                require(not unknown, f"preferences.validation.required_artifacts has unsupported artifacts: {', '.join(unknown)}", errors)
            commands = validation.get("commands", [])
            require(isinstance(commands, list), "preferences.validation.commands must be an array", errors)
            if isinstance(commands, list):
                for index, command in enumerate(commands):
                    validate_validation_command(f"preferences.validation.commands[{index}]", command, errors)

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
