#!/usr/bin/env bash
# Install the td-loop Codex skill into $CODEX_HOME/skills/td-loop.
#
# Copies this repo into the install destination so Codex discovers it on next
# start. Idempotent: safely replaces a previous install of this skill and
# refuses to clobber a directory that belongs to a different skill.
#
# For active development on this machine, prefer a symlink instead (see README):
#     ln -s "$PWD" ~/.codex/skills/td-loop
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="td-loop"
DEST_PARENT="${CODEX_HOME:-$HOME/.codex}/skills"
DEST="$DEST_PARENT/$SKILL_NAME"

if [[ ! -f "$SCRIPT_DIR/SKILL.md" ]]; then
  echo "error: SKILL.md not found next to $0; run from the td-loop repo ($SCRIPT_DIR)" >&2
  exit 1
fi

mkdir -p "$DEST_PARENT"

# Replace any prior install of this skill. Refuse to overwrite a directory that
# is not this skill (so we never silently delete unrelated work).
if [[ -e "$DEST" || -L "$DEST" ]]; then
  if [[ -d "$DEST" && ! -L "$DEST" && -f "$DEST/SKILL.md" ]] \
     && ! grep -q "^name:[[:space:]]*${SKILL_NAME}\$" "$DEST/SKILL.md"; then
    echo "error: $DEST exists but is not the '${SKILL_NAME}' skill; refusing to overwrite." >&2
    exit 1
  fi
  rm -rf "$DEST"
fi

cp -R "$SCRIPT_DIR" "$DEST"
# Strip repo-only / ephemeral artifacts from the installed copy.
rm -rf "$DEST/.git"
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$DEST" -name '.DS_Store' -delete 2>/dev/null || true

echo "installed ${SKILL_NAME} -> ${DEST}"
echo "Restart Codex to pick up the skill."
