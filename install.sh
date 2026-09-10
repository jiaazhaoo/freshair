#!/usr/bin/env bash
# Install the what-do-you-think skill into ~/.claude/skills/
set -euo pipefail

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skills/what-do-you-think"
dest="${HOME}/.claude/skills/what-do-you-think"

if [ ! -d "$src" ]; then
  echo "install.sh: cannot find $src" >&2
  exit 1
fi

mkdir -p "${HOME}/.claude/skills"

if [ -e "$dest" ] || [ -L "$dest" ]; then
  echo "Replacing existing install at $dest"
  rm -rf "$dest"
fi

ln -s "$src" "$dest"
echo "Linked $dest -> $src"

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  cat <<'MSG'

One more step: OPENROUTER_API_KEY is not set.

  1. Get a key: https://openrouter.ai/keys
  2. export OPENROUTER_API_KEY="sk-or-v1-..."   (add it to ~/.zshrc or ~/.bashrc)
MSG
else
  echo "OPENROUTER_API_KEY is set. You're ready — try asking Claude Code: \"问问别的模型\""
fi
