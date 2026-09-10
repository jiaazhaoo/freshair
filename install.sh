#!/usr/bin/env bash
# Install the FreshAir skill into ~/.claude/skills/, then report which backends
# this machine is already signed in to.
set -euo pipefail

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skills/freshair"
dest="${HOME}/.claude/skills/freshair"

if [ ! -d "$src" ]; then
  echo "install.sh: cannot find $src" >&2
  exit 1
fi

mkdir -p "${HOME}/.claude/skills"
if [ -e "$dest" ] || [ -L "$dest" ]; then
  rm -rf "$dest"
fi
ln -s "$src" "$dest"
echo "Linked $dest -> $src"
echo "Invoke it in Claude Code with /freshair"
echo

echo "Backends available on this machine:"
found=0
outsider=0
for cli in codex gemini claude; do
  if command -v "$cli" >/dev/null 2>&1; then
    case "$cli" in
      codex)  echo "  ✓ codex   — your ChatGPT login" ; outsider=1 ;;
      gemini) echo "  ✓ gemini  — your Google login"  ; outsider=1 ;;
      claude) echo "  ✓ claude  — your Anthropic login (same vendor as Claude Code; weakest option)" ;;
    esac
    found=1
  fi
done

if [ -n "${OPENROUTER_API_KEY:-}" ]; then
  echo "  ✓ openrouter — OPENROUTER_API_KEY is set"
  found=1
  outsider=1
fi

if [ "$found" -eq 0 ]; then
  cat <<'MSG'
  (none)

No backend found. Pick whichever you already pay for:
  - Codex CLI      https://developers.openai.com/codex/cli   (ChatGPT Plus/Pro)
  - Gemini CLI     https://github.com/google-gemini/gemini-cli
  - OpenRouter     https://openrouter.ai/keys  then: export OPENROUTER_API_KEY=...
MSG
elif [ "$outsider" -eq 0 ]; then
  cat <<'MSG'

Only a same-vendor backend is available, so reviews will come from Claude
reviewing Claude — a fresh context window, but the same blind spots. Install
Codex CLI or Gemini CLI, or set OPENROUTER_API_KEY, to get a real outsider.
MSG
else
  echo
  echo "Ready. Try /freshair in a session that has been going for a while."
fi
