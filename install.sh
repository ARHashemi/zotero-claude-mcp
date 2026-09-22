#!/usr/bin/env bash
# Install the Zotero connector for Claude Code.
# Safe to re-run: it replaces any previous registration.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$ROOT/bin/zotero-mcp"
NAME="${ZOTERO_MCP_NAME:-zotero}"

say()  { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- 1. Python ---------------------------------------------------------
command -v python3 >/dev/null 2>&1 || fail "python3 not found. Install Python 3.9 or newer."
python3 - <<'PY' || fail "Python 3.9+ required; $(python3 -V) found."
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
say "Python:  $(python3 -V)"

chmod +x "$LAUNCHER"

# --- 2. Zotero library -------------------------------------------------
say ""
say "Looking for your Zotero library..."
if "$LAUNCHER" status | grep -q "NOT FOUND"; then
    say ""
    "$LAUNCHER" status | sed 's/^/  /'
    say ""
    say "Zotero's database was not found in any default location."
    say "Open Zotero -> Settings -> Advanced -> Files and Folders to see your data directory,"
    say "then run:   $LAUNCHER setup --data-dir=/your/path"
    say ""
    if [ -t 0 ]; then
        read -r -p "Enter it now (or press Enter to continue anyway): " answer
        [ -n "$answer" ] && "$LAUNCHER" setup "--data-dir=$answer"
    fi
else
    "$LAUNCHER" status | grep -E "data directory|detected via|local library|schema" | sed 's/^/  /'
fi

# --- 3. Register with Claude ------------------------------------------
say ""
if command -v claude >/dev/null 2>&1; then
    claude mcp remove "$NAME" -s user >/dev/null 2>&1 || true
    claude mcp add --scope user "$NAME" -- "$LAUNCHER"
    say "Registered with Claude Code as \"$NAME\" for every project on this machine."
else
    say "The 'claude' CLI was not found, so nothing was registered automatically."
    say "For Claude Code:   claude mcp add --scope user $NAME -- $LAUNCHER"
    say ""
    say "For any other MCP client, add this to its server configuration:"
    cat <<JSON

  "mcpServers": {
    "$NAME": {
      "command": "$LAUNCHER"
    }
  }
JSON
fi

# --- 4. Optional API key ----------------------------------------------
say ""
say "Local library access works now, with no key and no account."
say ""
say "Optional: a Zotero web API key adds group libraries, proper CSL citation"
say "styles, and saving items/notes back to Zotero. Create one at"
say "    https://www.zotero.org/settings/keys/new"
say "then run:"
say "    $LAUNCHER setup"
say ""
say "Done. Restart Claude Code to load the tools."
