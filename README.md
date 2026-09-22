# zotero-claude-mcp

An [MCP](https://modelcontextprotocol.io) server that gives Claude access to your
[Zotero](https://www.zotero.org) library — both the local database on your own machine
and your zotero.org account, including group libraries.

**Python standard library only.** No pip install, no virtualenv, no dependencies to keep
patched. If you have Python 3.9+, it runs.

## What it does

Ask Claude things like *"what do I have saved on contact-angle hysteresis?"*, *"which of
my PDFs mention level-set methods?"*, *"summarise the abstracts in my Reading collection"*,
or *"cite these three in IEEE style"* — and it reads your actual library instead of
guessing or searching the web.

It can also open the PDFs: `zotero_attachments` returns real file paths, which Claude can
then read directly.

## Install

```bash
git clone https://github.com/ARHashemi/zotero-claude-mcp.git
cd zotero-claude-mcp
./install.sh
```

That is the whole install. `install.sh` checks your Python, finds your Zotero library,
registers the server with Claude Code at **user scope** (so it works in every project and
session), and tells you what it found. It is safe to re-run.

Then restart Claude Code.

**Nothing to configure for local use** — no account, no API key, no paths. Your Zotero data
directory is read from Zotero's own preferences, and your numeric user ID from the local
database.

If your library lives somewhere unusual and is not found automatically, `install.sh` says so
and offers to take the path. You can also set it any time:

```bash
bin/zotero-mcp setup --data-dir=/path/to/your/Zotero
```

Zotero shows that path under **Settings -> Advanced -> Files and Folders**.

### Other MCP clients

Point any stdio-capable client at `bin/zotero-mcp`:

```json
"mcpServers": {
  "zotero": {
    "command": "/absolute/path/to/zotero-claude-mcp/bin/zotero-mcp"
  }
}
```

### Where things are looked for

| Platform | Zotero profile | Default data directory |
| --- | --- | --- |
| Linux | `~/.zotero/zotero/*/` | `~/Zotero` |
| Linux (snap) | `~/snap/zotero-snap/common/.zotero/...` | `~/snap/zotero-snap/common/Zotero` |
| Linux (flatpak) | `~/.var/app/org.zotero.Zotero/data/...` | `~/.var/app/org.zotero.Zotero/data/Zotero` |
| macOS | `~/Library/Application Support/Zotero/Profiles/*/` | `~/Zotero` |
| Windows | `%APPDATA%\Zotero\Zotero\Profiles\*\` | `%APPDATA%\Zotero\Zotero` |

A custom directory set in Zotero is picked up automatically from its preferences, on every
platform. `bin/zotero-mcp status` reports which of these it used.

## The two sources

Every read tool takes a `source` argument.

**`local`** (default) — reads `zotero.sqlite` from your Zotero data directory. Fast, works
offline, works whether or not Zotero is running, and is the only source that can return PDF
file paths.

Zotero holds an exclusive lock on that file while it runs, so the server reads from a
snapshot copy under `~/.cache/zotero-mcp/`, refreshed automatically whenever the live
database changes. **Nothing is ever written to your Zotero data.**

**`web`** — `api.zotero.org` with your API key. Needed for group libraries, for items that
have not synced to this machine, for real CSL citation formatting, and for the two write
tools.

## Configuration

Everything is autodetected except the API key. The Zotero data directory and your numeric
user ID are read from Zotero's own profile, so for local-only use there is **nothing to
configure**.

To add a web API key — create one at https://www.zotero.org/settings/keys/new, then:

```bash
bin/zotero-mcp setup                    # prompts, hiding the key as you type
bin/zotero-mcp setup --key=... --user-id=...   # or non-interactively
```

The prompt hides the key as you type and writes `~/.config/zotero-mcp/config.json` with
mode `600`. Every field is optional:

```json
{
  "api_key": "...",
  "user_id": "YOUR_NUMERIC_USER_ID",
  "data_dir": "~/Zotero",
  "library_type": "user",
  "default_source": "local"
}
```

Environment variables override the file: `ZOTERO_API_KEY`, `ZOTERO_USER_ID`,
`ZOTERO_DATA_DIR`, `ZOTERO_BASE_ATTACHMENT_PATH`, `ZOTERO_LIBRARY_TYPE`,
`ZOTERO_DEFAULT_SOURCE`.

Give the key **read/write** permission if you want `zotero_create_item` and
`zotero_add_note` to work; read-only is fine for everything else.

## Tools

| Tool | Purpose |
| --- | --- |
| `zotero_search` | find references by keyword, author, tag, type, year, collection |
| `zotero_get_item` | full record: every field, notes as plain text, attachment paths |
| `zotero_fulltext_search` | search inside PDFs and notes, not just metadata |
| `zotero_collections` | collection tree with item counts |
| `zotero_collection_items` | items in a collection, optionally recursive |
| `zotero_tags` | tags with item counts |
| `zotero_recent` | recently added or modified |
| `zotero_attachments` | on-disk PDF paths, ready to read |
| `zotero_bibliography` | citations in any CSL style (apa, ieee, nature, vancouver, …) |
| `zotero_libraries` | local libraries and zotero.org groups |
| `zotero_create_item` | save a new reference *(web API, needs write permission)* |
| `zotero_add_note` | attach a note to an item *(web API, needs write permission)* |
| `zotero_status` | configuration diagnostics |

## CLI

Useful for checking things without going through Claude:

```bash
bin/zotero-mcp status                              # diagnostics
bin/zotero-mcp setup                               # store API key / user ID
bin/zotero-mcp tools                               # list tools
bin/zotero-mcp tool zotero_search '{"query":"x"}'  # run one tool
bin/zotero-mcp                                     # serve over stdio (what Claude runs)
```

## Privacy

Your library never leaves your machine unless you use `source: "web"`, which talks to
zotero.org and nowhere else. There is no telemetry and no third-party network code — the
only outbound requests in the codebase are to `api.zotero.org`.

The API key is stored only in `~/.config/zotero-mcp/config.json` (mode 600), never in the
repository, and is never logged.

## How the local backend works

It queries Zotero's SQLite tables directly (`items`, `itemData`, `itemCreators`,
`collections`, `itemTags`, `itemAttachments`, `itemNotes`) and uses the FTS5 index in
`fulltext.sqlite` for full-text search.

That schema is a Zotero implementation detail and can change between releases. The code is
written against **userdata schema v129** (Zotero 7); on a newer schema it prints a note to
stderr and keeps going, rather than failing. Since every local query is read-only against a
snapshot copy, a schema change can at worst produce wrong output — never a damaged library.

Zotero's own HTTP local API (`localhost:23119/api`, off by default) is not used; reading the
database directly works whether or not it is enabled.

## Troubleshooting

**"Zotero database not found"** — run `bin/zotero-mcp setup --data-dir=/your/path`. Find the
real path in Zotero under Settings -> Advanced -> Files and Folders. `bin/zotero-mcp status`
shows where it looked and why.

**Claude does not see the tools** — restart Claude Code, then check `claude mcp list`.

**Attachment shows `FILE MISSING`** — the item's file has not synced to this machine yet;
open it once in Zotero, or use `source: "web"`.

**Full-text search finds nothing** — only attachments Zotero has indexed are searchable.
Check Settings → Search in Zotero.

## License

MIT — see [LICENSE](LICENSE).

Not affiliated with, endorsed by, or supported by Zotero (Corporation for Digital Scholarship)
or Anthropic. "Zotero" and "Claude" are their respective trademarks, used here only to describe
what this tool connects.
