#!/usr/bin/env bash
# Install the td-loop skill into a harness skill directory so the agent
# discovers it on next start. Targets any Agent-Skills-standard loader —
# Codex, pi, or the harness-neutral ~/.agents/skills/ (scanned by pi and any
# compliant loader). All three ship a SKILL.md and scan directories containing
# one.
#
# Copies this repo into the install destination. Idempotent: safely replaces a
# previous install of this skill and refuses to clobber a directory that belongs
# to a different skill.
#
# Usage:
#   ./install.sh                      # default target: all (codex + pi + agents)
#   ./install.sh --target codex       # $CODEX_HOME/skills        (default ~/.codex/skills)
#   ./install.sh --target pi          # $PI_HOME/agent/skills     (default ~/.pi/agent/skills)
#   ./install.sh --target agents      # $AGENTS_HOME/skills       (default ~/.agents/skills, harness-neutral)
#   ./install.sh --target all         # install into all three
#   ./install.sh codex                # positional shorthand
#
# Override the install root per target with an env var:
#   CODEX_HOME=/opt/codex ./install.sh --target codex
#   PI_HOME=/custom/pi    ./install.sh --target pi
#
# For active development on this machine, prefer a symlink instead (see README).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="td-loop"

usage() {
  cat >&2 <<EOF
Usage: $0 [--target <codex|pi|agents|all>] [<target>]

Targets (default: all):
  codex    \$CODEX_HOME/skills        (default ~/.codex/skills)
  pi       \$PI_HOME/agent/skills     (default ~/.pi/agent/skills)
  agents   \$AGENTS_HOME/skills       (default ~/.agents/skills)   [harness-neutral]
  all      install into all three
EOF
  exit 2
}

TARGET="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target|-t)
      [[ $# -ge 2 ]] || { echo "error: --target requires a value" >&2; usage; }
      TARGET="$2"; shift 2 ;;
    --target=*) TARGET="${1#--target=}"; shift ;;
    -t*) TARGET="${1#-t}"; shift ;;
    -h|--help) usage ;;
    codex|pi|agents|all) TARGET="$1"; shift ;;
    *) echo "error: unknown argument: $1" >&2; usage ;;
  esac
done

# Sanity: SKILL.md must sit next to this script.
if [[ ! -f "$SCRIPT_DIR/SKILL.md" ]]; then
  echo "error: SKILL.md not found next to $0; run from the td-loop repo ($SCRIPT_DIR)" >&2
  exit 1
fi

# Resolve the skill parent dir for a target.
resolve_dest_parent() {
  case "$1" in
    codex)  echo "${CODEX_HOME:-$HOME/.codex}/skills" ;;
    pi)     echo "${PI_HOME:-$HOME/.pi}/agent/skills" ;;
    agents) echo "${AGENTS_HOME:-$HOME/.agents}/skills" ;;
    *) echo "error: unknown target: $1" >&2; exit 2 ;;
  esac
}

# Copy this skill into one target dir, replacing any prior install of it.
install_to() {
  local t="$1" dest_parent dest
  dest_parent="$(resolve_dest_parent "$t")"
  dest="$dest_parent/$SKILL_NAME"

  mkdir -p "$dest_parent"

  # Replace any prior install of this skill. Refuse to overwrite a directory
  # that is not this skill (so we never silently delete unrelated work).
  if [[ -e "$dest" || -L "$dest" ]]; then
    if [[ -d "$dest" && ! -L "$dest" && -f "$dest/SKILL.md" ]] \
       && ! grep -q "^name:[[:space:]]*${SKILL_NAME}\$" "$dest/SKILL.md"; then
      echo "error: $dest exists but is not the '${SKILL_NAME}' skill; refusing to overwrite." >&2
      exit 1
    fi
    rm -rf "$dest"
  fi

  cp -R "$SCRIPT_DIR" "$dest"
  # Strip repo-only / ephemeral artifacts from the installed copy.
  rm -rf "$dest/.git"
  find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  find "$dest" -name '.DS_Store' -delete 2>/dev/null || true

  echo "installed ${SKILL_NAME} -> ${dest} (target: ${t})"
}

case "$TARGET" in
  codex|pi|agents) install_to "$TARGET" ;;
  all) install_to codex; install_to pi; install_to agents ;;
  *) echo "error: unknown target: $TARGET" >&2; usage ;;
esac

echo "Restart your agent harness to pick up the skill."
