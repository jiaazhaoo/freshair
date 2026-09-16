#!/usr/bin/env bash
# FreshAir installer.
#
#   curl -fsSL https://raw.githubusercontent.com/jiaazhaoo/what-do-you-think/main/install.sh | bash
#
# Also works when run from a clone, in which case the skill is linked rather
# than copied, so `git pull` updates it.
set -euo pipefail

REPO="${FRESHAIR_REPO:-jiaazhaoo/what-do-you-think}"
REF="${FRESHAIR_REF:-main}"
DEST="${HOME}/.claude/skills/freshair"

die() { echo "install: $*" >&2; exit 1; }

here=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

mkdir -p "${HOME}/.claude/skills"

if [ -n "$here" ] && [ -d "$here/skills/freshair" ]; then
  # From a clone: symlink, so the installed skill follows the working tree.
  if [ -e "$DEST" ] || [ -L "$DEST" ]; then rm -rf "$DEST"; fi
  ln -s "$here/skills/freshair" "$DEST"
  echo "✓ Linked $DEST -> $here/skills/freshair"
else
  # Piped from curl: fetch just this repo's tarball, no git required.
  command -v curl >/dev/null 2>&1 || die "curl is required"
  command -v tar  >/dev/null 2>&1 || die "tar is required"

  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  url="https://codeload.github.com/${REPO}/tar.gz/refs/heads/${REF}"

  curl -fsSL "$url" | tar -xzf - -C "$tmp" \
    || die "could not download ${REPO}@${REF} — check the repo name and that the branch exists"

  src=""
  for candidate in "$tmp"/*/skills/freshair; do
    [ -d "$candidate" ] && src="$candidate" && break
  done
  [ -n "$src" ] || die "no skills/freshair in ${REPO}@${REF}"

  if [ -e "$DEST" ] || [ -L "$DEST" ]; then rm -rf "$DEST"; fi
  cp -R "$src" "$DEST"
  echo "✓ Installed $DEST  (${REPO}@${REF})"
fi

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null \
  || echo "! python3 3.9+ not found on PATH — FreshAir needs it to run"

echo
echo "Use it in any Claude Code session:  /freshair"
echo

# Which reviewers can this machine actually reach?
echo "Backends found:"
found=0; outsider=0
for cli in codex gemini claude; do
  command -v "$cli" >/dev/null 2>&1 || continue
  found=1
  case "$cli" in
    codex)  echo "  ✓ codex   — your ChatGPT login";  outsider=1 ;;
    gemini) echo "  ✓ gemini  — your Google login";   outsider=1 ;;
    claude) echo "  ✓ claude  — your Anthropic login (same vendor as Claude Code; weakest option)" ;;
  esac
done
if [ -n "${OPENROUTER_API_KEY:-}" ]; then
  echo "  ✓ openrouter — OPENROUTER_API_KEY is set"; found=1; outsider=1
fi

if [ "$found" -eq 0 ]; then
  cat <<'MSG'
  (none)

Install whichever you already pay for — no API key needed, it reuses the login:
  npm i -g @openai/codex      && codex    # sign in with ChatGPT
  npm i -g @google/gemini-cli && gemini   # sign in with Google
MSG
elif [ "$outsider" -eq 0 ]; then
  cat <<'MSG'

Only a same-vendor backend is available, so reviews will be Claude reviewing
Claude — a fresh context window, but the same blind spots. For a real outsider:
  npm i -g @openai/codex      && codex    # sign in with ChatGPT
  npm i -g @google/gemini-cli && gemini   # sign in with Google
MSG
else
  echo
  echo "Ready. Try /freshair in a session that has been going a while."
fi
