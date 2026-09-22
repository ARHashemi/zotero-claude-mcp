"""Tool definitions and dispatch for the Zotero MCP server."""

from pathlib import Path

from . import localdb, render
from .config import CONFIG_PATH, Config
from .webapi import WebApiError, ZoteroWeb

SOURCE_DESC = (
    "Which library to read: 'local' (this computer's Zotero database — fast, works offline, "
    "includes file paths), 'web' (zotero.org, includes group libraries), or 'auto' (default "
    "from config; local first, falling back to web)."
)

_LIBRARY_DESC = (
    "Library to use. Local: omit for My Library, or give a group name/ID. "
    "Web: omit for your user library, or give a group ID (or 'group:12345')."
)


def _schema(props, required=None):
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


_SOURCE_PROP = {"type": "string", "enum": ["local", "web", "auto"], "description": SOURCE_DESC}
_LIBRARY_PROP = {"type": "string", "description": _LIBRARY_DESC}

TOOLS = [
    {
        "name": "zotero_search",
        "description": (
            "Search the user's Zotero library for references by keyword, author, tag, item type, "
            "year or collection. Returns a ranked list with title, authors, year, venue and the "
            "item key needed by the other tools. Use this first for any question about what is in "
            "the user's library or to find a paper they have saved."
        ),
        "inputSchema": _schema(
            {
                "query": {"type": "string", "description": "Free-text query over title, creators, tags and other fields."},
                "mode": {
                    "type": "string",
                    "enum": ["titleCreatorYear", "everything"],
                    "description": "'everything' also searches indexed PDF full text and notes. Default titleCreatorYear.",
                },
                "itemType": {"type": "string", "description": "Restrict to a Zotero item type, e.g. journalArticle, book, conferencePaper, thesis, preprint."},
                "tag": {"type": "string", "description": "Restrict to items carrying this tag."},
                "creator": {"type": "string", "description": "Restrict to items with a creator whose name contains this (local source only)."},
                "year": {"type": "string", "description": "Restrict to items whose date contains this year (local source only)."},
                "collection": {"type": "string", "description": "Collection key, or (local source) collection name."},
                "limit": {"type": "integer", "description": "Max results, default 25."},
                "sort": {"type": "string", "enum": ["relevance", "date", "added"], "description": "Result ordering, default relevance."},
                "source": _SOURCE_PROP,
                "library": _LIBRARY_PROP,
            }
        ),
    },
    {
        "name": "zotero_get_item",
        "description": (
            "Fetch the complete record for one or more Zotero items by item key: every metadata "
            "field, all creators, tags, collections, attached notes (as plain text) and attachment "
            "file paths. Use after zotero_search when you need abstracts, DOIs, notes or the PDF path."
        ),
        "inputSchema": _schema(
            {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "One or more 8-character Zotero item keys.",
                },
                "source": _SOURCE_PROP,
                "library": _LIBRARY_PROP,
            },
            ["keys"],
        ),
    },
    {
        "name": "zotero_fulltext_search",
        "description": (
            "Search the full text of PDFs and notes stored in the library (not just metadata), "
            "returning the parent references that contain the phrase. Use when the user asks which "
            "of their papers discuss or mention something."
        ),
        "inputSchema": _schema(
            {
                "query": {"type": "string", "description": "Words or a quoted phrase to find inside PDFs and notes."},
                "limit": {"type": "integer", "description": "Max results, default 20."},
                "source": _SOURCE_PROP,
                "library": _LIBRARY_PROP,
            },
            ["query"],
        ),
    },
    {
        "name": "zotero_collections",
        "description": "List the library's collections as a tree, with item counts and collection keys.",
        "inputSchema": _schema({"source": _SOURCE_PROP, "library": _LIBRARY_PROP}),
    },
    {
        "name": "zotero_collection_items",
        "description": "List the references in a collection, by collection key or (local) name.",
        "inputSchema": _schema(
            {
                "collection": {"type": "string", "description": "Collection key or name."},
                "recursive": {"type": "boolean", "description": "Include subcollections (local source only). Default false."},
                "limit": {"type": "integer", "description": "Max results, default 100."},
                "source": _SOURCE_PROP,
                "library": _LIBRARY_PROP,
            },
            ["collection"],
        ),
    },
    {
        "name": "zotero_tags",
        "description": "List tags used in the library with how many items carry each one.",
        "inputSchema": _schema(
            {
                "contains": {"type": "string", "description": "Only tags containing this text."},
                "limit": {"type": "integer", "description": "Max tags, default 200."},
                "source": _SOURCE_PROP,
                "library": _LIBRARY_PROP,
            }
        ),
    },
    {
        "name": "zotero_recent",
        "description": "List the most recently added or modified references — useful for 'what have I saved lately'.",
        "inputSchema": _schema(
            {
                "by": {"type": "string", "enum": ["dateAdded", "dateModified"], "description": "Default dateAdded."},
                "limit": {"type": "integer", "description": "Max results, default 20."},
                "source": _SOURCE_PROP,
                "library": _LIBRARY_PROP,
            }
        ),
    },
    {
        "name": "zotero_attachments",
        "description": (
            "Resolve the on-disk paths of files (PDFs, HTML snapshots) attached to the given items, "
            "so the PDF can then be read with the Read tool. Local source only."
        ),
        "inputSchema": _schema(
            {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "Item keys."},
                "contentType": {"type": "string", "description": "Filter, e.g. application/pdf."},
                "library": _LIBRARY_PROP,
            },
            ["keys"],
        ),
    },
    {
        "name": "zotero_bibliography",
        "description": (
            "Format references as a bibliography or in-text citations in any CSL style "
            "(apa, chicago-note-bibliography, ieee, nature, vancouver, …). Uses zotero.org's "
            "citation processor when a web API key is configured; otherwise falls back to a plain "
            "author–date rendering from local metadata."
        ),
        "inputSchema": _schema(
            {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "Item keys to cite."},
                "style": {"type": "string", "description": "CSL style id, default 'apa'."},
                "locale": {"type": "string", "description": "Locale, default 'en-US'."},
                "mode": {"type": "string", "enum": ["bib", "citation"], "description": "'bib' (reference list, default) or 'citation' (in-text)."},
                "library": _LIBRARY_PROP,
            },
            ["keys"],
        ),
    },
    {
        "name": "zotero_libraries",
        "description": "List available libraries: local libraries on this machine and group libraries on zotero.org.",
        "inputSchema": _schema({}),
    },
    {
        "name": "zotero_create_item",
        "description": (
            "Save a new reference to the user's Zotero library via the web API (it then syncs to "
            "the desktop app). Requires a configured API key with write access. Ask the user before "
            "writing unless they clearly asked you to save something."
        ),
        "inputSchema": _schema(
            {
                "itemType": {"type": "string", "description": "e.g. journalArticle, book, conferencePaper, preprint, report, webpage."},
                "title": {"type": "string"},
                "creators": {
                    "type": "array",
                    "description": "Authors etc., as ['Last, First', ...] or [{'lastName','firstName','creatorType'}].",
                    "items": {"type": ["string", "object"]},
                },
                "date": {"type": "string"},
                "publicationTitle": {"type": "string"},
                "DOI": {"type": "string"},
                "url": {"type": "string"},
                "abstractNote": {"type": "string"},
                "extraFields": {"type": "object", "description": "Any other Zotero fields, e.g. {'volume':'12','pages':'1-20'}."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "collection": {"type": "string", "description": "Collection key, name, or 'Parent/Child' path to file it under."},
                "library": _LIBRARY_PROP,
            },
            ["itemType", "title"],
        ),
    },
    {
        "name": "zotero_add_note",
        "description": (
            "Attach a note to an existing Zotero item (or add a standalone note) via the web API. "
            "Requires a configured API key with write access."
        ),
        "inputSchema": _schema(
            {
                "parentKey": {"type": "string", "description": "Item key to attach the note to; omit for a standalone note."},
                "text": {"type": "string", "description": "Note body. Markdown-ish plain text is converted to simple HTML."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "library": _LIBRARY_PROP,
            },
            ["text"],
        ),
    },
    {
        "name": "zotero_create_collection",
        "description": (
            "Create a collection (folder) in the user's Zotero library via the web API, optionally "
            "nested under a parent. If a collection with the same name already exists under that "
            "parent, its key is returned instead of making a duplicate. Returns the collection key."
        ),
        "inputSchema": _schema(
            {
                "name": {"type": "string", "description": "Collection name."},
                "parent": {"type": "string", "description": "Parent collection key, name, or 'Parent/Child' path; omit for top level."},
                "library": _LIBRARY_PROP,
            },
            ["name"],
        ),
    },
    {
        "name": "zotero_update_collection",
        "description": "Rename a collection and/or move it under another parent (or to the top level) via the web API.",
        "inputSchema": _schema(
            {
                "collection": {"type": "string", "description": "Collection key, name, or 'Parent/Child' path."},
                "name": {"type": "string", "description": "New name."},
                "parent": {"type": "string", "description": "New parent key/name/path, or 'root' for top level."},
                "library": _LIBRARY_PROP,
            },
            ["collection"],
        ),
    },
    {
        "name": "zotero_delete_collection",
        "description": (
            "Delete a collection via the web API. The items in it are NOT deleted — they stay in the "
            "library and in any other collections. Refuses if the collection has subcollections. "
            "Always confirm with the user first."
        ),
        "inputSchema": _schema(
            {
                "collection": {"type": "string", "description": "Collection key, name, or 'Parent/Child' path."},
                "library": _LIBRARY_PROP,
            },
            ["collection"],
        ),
    },
    {
        "name": "zotero_add_to_collection",
        "description": (
            "File existing items into a collection via the web API (items can be in several "
            "collections at once; this does not remove them from others). Works in batches, so "
            "pass all the keys in one call. Set create=true to make the collection if it doesn't exist."
        ),
        "inputSchema": _schema(
            {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "Item keys to file."},
                "collection": {"type": "string", "description": "Collection key, name, or 'Parent/Child' path."},
                "create": {"type": "boolean", "description": "Create the collection (top level, or under the path's parent) if missing. Default false."},
                "library": _LIBRARY_PROP,
            },
            ["keys", "collection"],
        ),
    },
    {
        "name": "zotero_remove_from_collection",
        "description": "Take items out of a collection via the web API. The items themselves stay in the library.",
        "inputSchema": _schema(
            {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "Item keys."},
                "collection": {"type": "string", "description": "Collection key, name, or 'Parent/Child' path."},
                "library": _LIBRARY_PROP,
            },
            ["keys", "collection"],
        ),
    },
    {
        "name": "zotero_update_item",
        "description": (
            "Edit an existing item's metadata via the web API: set or clear any fields (title, date, "
            "DOI, abstractNote, volume, pages, extra, …), replace its creators, add or remove tags. "
            "Only the fields you pass are changed."
        ),
        "inputSchema": _schema(
            {
                "key": {"type": "string", "description": "Item key."},
                "fields": {"type": "object", "description": "Field -> new value, e.g. {'DOI':'10.1/x','pages':'1-9'}. Use '' to clear a field."},
                "creators": {
                    "type": "array",
                    "description": "Replaces all creators. ['Last, First', ...] or [{'lastName','firstName','creatorType'}].",
                    "items": {"type": ["string", "object"]},
                },
                "addTags": {"type": "array", "items": {"type": "string"}},
                "removeTags": {"type": "array", "items": {"type": "string"}},
                "library": _LIBRARY_PROP,
            },
            ["key"],
        ),
    },
    {
        "name": "zotero_tag_items",
        "description": "Add and/or remove tags on many items at once via the web API.",
        "inputSchema": _schema(
            {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "Item keys."},
                "add": {"type": "array", "items": {"type": "string"}, "description": "Tags to add."},
                "remove": {"type": "array", "items": {"type": "string"}, "description": "Tags to remove."},
                "library": _LIBRARY_PROP,
            },
            ["keys"],
        ),
    },
    {
        "name": "zotero_rename_tag",
        "description": "Rename a tag on every item that carries it (merging into the new name if it already exists), via the web API.",
        "inputSchema": _schema(
            {
                "tag": {"type": "string", "description": "Current tag name (exact)."},
                "newName": {"type": "string", "description": "New tag name."},
                "library": _LIBRARY_PROP,
            },
            ["tag", "newName"],
        ),
    },
    {
        "name": "zotero_delete_tags",
        "description": "Remove tags from the whole library (from every item) via the web API. Items are untouched otherwise. Confirm with the user first.",
        "inputSchema": _schema(
            {
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Exact tag names."},
                "library": _LIBRARY_PROP,
            },
            ["tags"],
        ),
    },
    {
        "name": "zotero_trash_items",
        "description": (
            "Move items to the Zotero trash (recoverable), or restore them from it with "
            "restore=true, via the web API. Never deletes permanently. Confirm with the user first."
        ),
        "inputSchema": _schema(
            {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "Item keys."},
                "restore": {"type": "boolean", "description": "Restore from trash instead. Default false."},
                "library": _LIBRARY_PROP,
            },
            ["keys"],
        ),
    },
    {
        "name": "zotero_status",
        "description": "Report how this connector is configured: data directory, item counts, whether the web API key works, and what to fix if something is missing.",
        "inputSchema": _schema({}),
    },
]


# --------------------------------------------------------------------------
# dispatch helpers
# --------------------------------------------------------------------------

class ToolError(RuntimeError):
    pass


def _resolve_source(cfg, requested):
    source = requested or cfg.default_source or "local"
    return source if source in ("local", "web") else (cfg.default_source if cfg.default_source in ("local", "web") else "local")


def _use_web(cfg, args, want=None):
    source = want or args.get("source")
    resolved = _resolve_source(cfg, source)
    if resolved == "web":
        return True
    # 'auto'/'local' but the local database is unreachable and a key exists -> fall back.
    if source in (None, "auto") and not cfg.sqlite_path.exists() and cfg.api_key:
        return True
    return False


def _web(cfg, args):
    return ZoteroWeb(cfg, args.get("library"))


SYNC_NOTE = (
    "Saved on zotero.org. The desktop app (and source='local' reads) will show it after "
    "Zotero's next sync; use source='web' to see it right away."
)


def _keys(args, name="keys"):
    keys = args.get(name) or []
    if isinstance(keys, str):
        keys = [keys]
    keys = [k.strip() for k in keys if k and k.strip()]
    if not keys:
        raise ToolError(f"'{name}' must list at least one item key.")
    return list(dict.fromkeys(keys))


def _batch_report(verb, changed, unchanged, missing, failed):
    lines = [f"{verb}: {len(changed)} item(s)."]
    if unchanged:
        lines.append(f"Already in that state: {len(unchanged)} ({', '.join(unchanged)}).")
    if missing:
        lines.append(f"Not found in this library: {', '.join(missing)}.")
    for f in failed:
        lines.append(f"FAILED {f.get('key')}: {f.get('message')}")
    if changed:
        lines.append(SYNC_NOTE)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# tool implementations
# --------------------------------------------------------------------------

def t_search(cfg, args):
    limit = int(args.get("limit") or 25)
    if _use_web(cfg, args):
        client = _web(cfg, args)
        collection = args.get("collection")
        if collection:
            collection = client.resolve_collection(collection)["key"]
        items = client.search(
            query=args.get("query"),
            item_type=args.get("itemType"),
            tag=args.get("tag"),
            collection=collection,
            mode=args.get("mode") or "titleCreatorYear",
            limit=limit,
            sort=args.get("sort") or "relevance",
        )
        src = "web"
    else:
        items = localdb.search(
            cfg,
            query=args.get("query"),
            item_type=args.get("itemType"),
            tag=args.get("tag"),
            collection=args.get("collection"),
            creator=args.get("creator"),
            year=args.get("year"),
            mode=args.get("mode") or "titleCreatorYear",
            limit=limit,
            library=args.get("library"),
            sort=args.get("sort") or "relevance",
        )
        src = "local"
    header = f"Search: {args.get('query') or '(filters only)'}"
    return render.item_list(items, header=header, source=src)


def t_get_item(cfg, args):
    keys = args["keys"]
    if isinstance(keys, str):
        keys = [keys]
    if _use_web(cfg, args):
        client = _web(cfg, args)
        items = [client.item(k) for k in keys]
        note_bodies = {}
        for item in items:
            for note in item.get("notes", []):
                note_bodies[note.get("key")] = render.strip_html(note.get("note", ""))
        return "\n\n---\n\n".join(render.full_item(i, note_bodies) for i in items)
    items = localdb.get_items_by_key(cfg, keys, library=args.get("library"))
    if not items:
        return f"No local item found for key(s): {', '.join(keys)}. Try source='web'."
    note_bodies = {
        n.get("key"): render.strip_html(n.get("note", ""))
        for item in items
        for n in item.get("notes", [])
    }
    return "\n\n---\n\n".join(render.full_item(i, note_bodies) for i in items)


def t_fulltext_search(cfg, args):
    query = args["query"]
    limit = int(args.get("limit") or 20)
    if _use_web(cfg, args):
        items = _web(cfg, args).search(query=query, mode="everything", limit=limit)
        return render.item_list(items, header=f"Full-text search: {query}", source="web")
    items = localdb.fulltext_search(cfg, query, limit=limit, library=args.get("library"))
    if not items:
        return (
            f"No indexed PDF or note text matches {query!r}.\n"
            "Note that only attachments Zotero has indexed are searchable; "
            "try zotero_search with mode='everything' or a shorter phrase."
        )
    return render.item_list(items, header=f"Full-text search: {query}", source="local")


def t_collections(cfg, args):
    if _use_web(cfg, args):
        tree = _web(cfg, args).collections()
        label = "web"
    else:
        tree = localdb.collections(cfg, library=args.get("library"))
        label = "local"
    return render.collection_tree(tree) or f"No collections in this {label} library."


def t_collection_items(cfg, args):
    limit = int(args.get("limit") or 100)
    if _use_web(cfg, args):
        client = _web(cfg, args)
        coll = client.resolve_collection(args["collection"])
        items = client.collection_items(coll["key"], limit=limit)
        return render.item_list(items, header=f"Collection: {coll['name']} ({coll['key']})", source="web")
    name, items = localdb.collection_items(
        cfg, args["collection"], limit=limit, library=args.get("library"),
        recursive=bool(args.get("recursive")),
    )
    return render.item_list(items, header=f"Collection: {name}", source="local")


def t_tags(cfg, args):
    limit = int(args.get("limit") or 200)
    if _use_web(cfg, args):
        rows = _web(cfg, args).tags(contains=args.get("contains"), limit=limit)
    else:
        rows = localdb.tags(cfg, library=args.get("library"), contains=args.get("contains"), limit=limit)
    if not rows:
        return "No tags found."
    return "\n".join(f"- {r['tag']}  ({r['numItems']})" for r in rows)


def t_recent(cfg, args):
    limit = int(args.get("limit") or 20)
    by = args.get("by") or "dateAdded"
    if _use_web(cfg, args):
        items = _web(cfg, args).recent(limit=limit, by=by)
        return render.item_list(items, header=f"Recent by {by}", source="web")
    items = localdb.recent(cfg, limit=limit, by=by, library=args.get("library"))
    return render.item_list(items, header=f"Recent by {by}", source="local")


def t_attachments(cfg, args):
    keys = args["keys"]
    if isinstance(keys, str):
        keys = [keys]
    pairs = localdb.attachments_for(cfg, keys, library=args.get("library"))
    if not pairs:
        return f"No local items found for key(s): {', '.join(keys)}."
    wanted = args.get("contentType")
    lines = []
    for item, atts in pairs:
        lines.append(f"**{item.get('title', '(untitled)')}**  (key: {item['key']})")
        shown = [a for a in atts if not wanted or a.get("contentType") == wanted]
        if not shown:
            lines.append("  (no matching attachment)")
        for att in shown:
            status = "" if att["exists"] else "  [FILE MISSING — may be sync-pending]"
            lines.append(f"  - {att.get('title') or att['key']} [{att.get('contentType')}]\n    {att['path']}{status}")
    lines.append("\nRead a PDF with the Read tool using the path above.")
    return "\n".join(lines)


def _plain_citation(item):
    names = [c["name"] for c in item.get("creators", []) if c.get("name")]
    if len(names) > 6:
        names = names[:6] + ["et al."]
    author = "; ".join(names) or "Anon."
    year = render._year(item)
    venue = item.get("publicationTitle") or item.get("bookTitle") or item.get("publisher") or ""
    bits = [f"{author} ({year}). {item.get('title', '(untitled)')}."]
    if venue:
        bits.append(f"{venue}.")
    for field, fmt in (("volume", "{}"), ("issue", "({})"), ("pages", "{}")):
        if item.get(field):
            bits.append(fmt.format(item[field]))
    if item.get("DOI"):
        bits.append(f"https://doi.org/{item['DOI']}")
    return " ".join(bits)


def _join_entry_number(text):
    """Numbered CSL styles emit the marker in its own block; put it back on the entry."""
    import re

    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) >= 2 and re.fullmatch(r"\[?\d+[.\]]?", lines[0].strip()):
        return f"{lines[0].strip()} {' '.join(l.strip() for l in lines[1:])}"
    return "\n".join(lines)


def t_bibliography(cfg, args):
    keys = args["keys"]
    if isinstance(keys, str):
        keys = [keys]
    style = args.get("style") or "apa"
    mode = args.get("mode") or "bib"
    if cfg.api_key:
        try:
            rows = _web(cfg, args).bibliography(keys, style=style, locale=args.get("locale") or "en-US", mode=mode)
            out = [_join_entry_number(render.strip_html(text)) for _, text in rows]
            if out:
                return f"Style: {style}\n\n" + "\n\n".join(out)
        except WebApiError as exc:
            fallback_note = f"(zotero.org citation service unavailable: {exc})\n\n"
        else:
            fallback_note = "(no output from zotero.org; using local formatting)\n\n"
    else:
        fallback_note = "(no web API key configured — using a plain author–date format)\n\n"
    items = localdb.get_items_by_key(cfg, keys, library=args.get("library"), with_children=False)
    if not items:
        return fallback_note + f"No items found for key(s): {', '.join(keys)}."
    return fallback_note + "\n\n".join(_plain_citation(i) for i in items)


def t_libraries(cfg, args):
    lines = ["## Local libraries (this computer)"]
    try:
        for lib in localdb.libraries(cfg):
            extra = f", groupID {lib['groupID']}" if lib["groupID"] else ""
            lines.append(f"- {lib['name']}  (type {lib['type']}, libraryID {lib['libraryID']}{extra})")
    except Exception as exc:
        lines.append(f"- unavailable: {exc}")
    lines.append("\n## Group libraries on zotero.org")
    if not cfg.api_key:
        lines.append("- no API key configured (run: zotero-mcp setup)")
    else:
        try:
            groups = ZoteroWeb(cfg).groups()
            lines += [f"- {g['name']}  (group:{g['id']}, {g['numItems']} items, {g['type']})" for g in groups] or ["- none"]
        except WebApiError as exc:
            lines.append(f"- unavailable: {exc}")
    return "\n".join(lines)


def _normalize_creators(raw):
    out = []
    for entry in raw or []:
        if isinstance(entry, str):
            if "," in entry:
                last, first = entry.split(",", 1)
                out.append({"creatorType": "author", "lastName": last.strip(), "firstName": first.strip()})
            else:
                parts = entry.strip().rsplit(" ", 1)
                if len(parts) == 2:
                    out.append({"creatorType": "author", "firstName": parts[0], "lastName": parts[1]})
                else:
                    out.append({"creatorType": "author", "name": entry.strip()})
        elif isinstance(entry, dict):
            entry.setdefault("creatorType", "author")
            out.append(entry)
    return out


def t_create_item(cfg, args):
    client = _web(cfg, args)
    payload = {
        "itemType": args["itemType"],
        "title": args["title"],
        "creators": _normalize_creators(args.get("creators")),
        "tags": [{"tag": t} for t in args.get("tags") or []],
    }
    for field in ("date", "publicationTitle", "DOI", "url", "abstractNote"):
        if args.get(field):
            payload[field] = args[field]
    payload.update(args.get("extraFields") or {})
    if args.get("collection"):
        payload["collections"] = [client.resolve_collection(args["collection"])["key"]]
    made = client.create_items([payload])
    if not made:
        return "Zotero accepted the request but returned no item."
    item = made[0]
    return f"Saved to Zotero (key {item.get('key')}):\n\n" + render.one_line(item) + f"\n\n{SYNC_NOTE}"


def _to_html(text):
    import html as html_mod

    paragraphs = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    return "".join(f"<p>{html_mod.escape(p).replace(chr(10), '<br/>')}</p>" for p in paragraphs) or "<p></p>"


def t_add_note(cfg, args):
    client = _web(cfg, args)
    payload = {
        "itemType": "note",
        "note": _to_html(args["text"]),
        "tags": [{"tag": t} for t in args.get("tags") or []],
    }
    if args.get("parentKey"):
        payload["parentItem"] = args["parentKey"]
    made = client.create_items([payload])
    key = made[0].get("key") if made else "?"
    target = f" on item {args['parentKey']}" if args.get("parentKey") else ""
    return f"Note saved{target} (key {key}). It will appear in the desktop app after the next sync."


def _parent_key(client, ref, flat):
    if ref in (None, ""):
        return None
    if str(ref).strip().lower() in ("root", "top", "/", "none"):
        return False
    return client.resolve_collection(ref, flat)["key"]


def t_create_collection(cfg, args):
    client = _web(cfg, args)
    name = (args.get("name") or "").strip()
    if not name:
        raise ToolError("'name' is required.")
    flat = client.collections_flat()
    parent = _parent_key(client, args.get("parent"), flat) or None
    for coll in flat:
        if coll["name"].lower() == name.lower() and coll["parentKey"] == parent:
            return f"Collection {coll['name']!r} already exists (key {coll['key']}, {coll['numItems']} items); not creating a duplicate."
    made = client.create_collection(name, parent)
    where = f" under {parent}" if parent else " at the top level"
    return f"Created collection {name!r}{where} (key {made['key']}).\n{SYNC_NOTE}"


def t_update_collection(cfg, args):
    client = _web(cfg, args)
    flat = client.collections_flat()
    coll = client.resolve_collection(args["collection"], flat)
    changes = {}
    if args.get("name"):
        changes["name"] = args["name"].strip()
    parent = _parent_key(client, args.get("parent"), flat)
    if parent is not None:
        if parent == coll["key"]:
            raise ToolError("A collection cannot be its own parent.")
        changes["parentCollection"] = parent
    if not changes:
        return "Nothing to change: pass 'name' and/or 'parent'."
    client.update_collection(coll["key"], changes)
    return f"Updated collection {coll['name']!r} (key {coll['key']}): {changes}.\n{SYNC_NOTE}"


def t_delete_collection(cfg, args):
    client = _web(cfg, args)
    flat = client.collections_flat()
    coll = client.resolve_collection(args["collection"], flat)
    subs = [c["name"] for c in flat if c["parentKey"] == coll["key"]]
    if subs:
        raise ToolError(f"{coll['name']!r} has subcollections ({', '.join(subs)}); delete or move those first.")
    client.delete_collection(coll["key"])
    return f"Deleted collection {coll['name']!r} (key {coll['key']}). Its {coll['numItems']} item(s) remain in the library.\n{SYNC_NOTE}"


def _collection_for_write(client, ref, create=False):
    flat = client.collections_flat()
    try:
        return client.resolve_collection(ref, flat), False
    except WebApiError:
        if not create:
            raise
    parts = [p.strip() for p in str(ref).strip("/").split("/") if p.strip()]
    parent = client.resolve_collection("/".join(parts[:-1]), flat)["key"] if len(parts) > 1 else None
    made = client.create_collection(parts[-1], parent)
    return {"key": made["key"], "name": parts[-1]}, True


def t_add_to_collection(cfg, args):
    client = _web(cfg, args)
    keys = _keys(args)
    coll, created = _collection_for_write(client, args["collection"], bool(args.get("create")))

    def change(data):
        current = data.get("collections") or []
        return None if coll["key"] in current else {"collections": current + [coll["key"]]}

    report = _batch_report(f"Filed into {coll['name']!r} ({coll['key']})", *client.modify_items(keys, change))
    return (f"Created collection {coll['name']!r} (key {coll['key']}).\n" if created else "") + report


def t_remove_from_collection(cfg, args):
    client = _web(cfg, args)
    keys = _keys(args)
    coll = client.resolve_collection(args["collection"])

    def change(data):
        current = data.get("collections") or []
        return {"collections": [c for c in current if c != coll["key"]]} if coll["key"] in current else None

    return _batch_report(f"Removed from {coll['name']!r} ({coll['key']})", *client.modify_items(keys, change))


def _retag(tags, add=(), remove=()):
    """Return the new tag list, or None if nothing changes. Keeps each tag's type."""
    drop = {t.lower() for t in remove}
    kept = [t for t in tags if t.get("tag", "").lower() not in drop]
    have = {t.get("tag") for t in kept}
    kept += [{"tag": t} for t in add if t and t not in have]
    return None if kept == tags else kept


def t_update_item(cfg, args):
    client = _web(cfg, args)
    key = args["key"].strip()
    rows = client.raw_items([key])
    if not rows:
        raise ToolError(f"No item {key} in this library.")
    data = rows[0]["data"]
    changes = dict(args.get("fields") or {})
    for locked in ("key", "version", "itemType", "collections", "tags", "relations", "parentItem"):
        if locked in changes:
            raise ToolError(f"'{locked}' can't be set via fields; use the dedicated argument or tool.")
    if args.get("creators") is not None:
        changes["creators"] = _normalize_creators(args["creators"])
    tags = _retag(data.get("tags") or [], args.get("addTags") or [], args.get("removeTags") or [])
    if tags is not None:
        changes["tags"] = tags
    if not changes:
        return "Nothing to change."
    item = client.update_item(key, changes)
    return f"Updated {key} ({', '.join(changes)}):\n\n" + render.one_line(item) + f"\n\n{SYNC_NOTE}"


def t_tag_items(cfg, args):
    client = _web(cfg, args)
    keys = _keys(args)
    add, remove = args.get("add") or [], args.get("remove") or []
    if not add and not remove:
        raise ToolError("Pass tags to 'add' and/or 'remove'.")

    def change(data):
        tags = _retag(data.get("tags") or [], add, remove)
        return None if tags is None else {"tags": tags}

    return _batch_report("Retagged", *client.modify_items(keys, change))


def t_rename_tag(cfg, args):
    client = _web(cfg, args)
    old, new = args["tag"], args["newName"].strip()
    if not new or new == old:
        raise ToolError("'newName' must be a different, non-empty tag.")
    rows = client.items_with_tag(old)
    if not rows:
        return f"No items carry the tag {old!r}."

    def change(data):
        tags = data.get("tags") or []
        out, seen = [], set()
        for t in tags:
            name = new if t.get("tag") == old else t.get("tag")
            if name not in seen:
                seen.add(name)
                out.append({**t, "tag": name})
        return None if out == tags else {"tags": out}

    return _batch_report(f"Renamed tag {old!r} -> {new!r} on", *client.modify_items([r["key"] for r in rows], change))


def t_delete_tags(cfg, args):
    client = _web(cfg, args)
    tags = [t for t in (args.get("tags") or []) if t]
    if not tags:
        raise ToolError("'tags' must list at least one tag.")
    client.delete_tags(tags)
    return f"Removed {len(tags)} tag(s) from the library: {', '.join(tags)}.\n{SYNC_NOTE}"


def t_trash_items(cfg, args):
    client = _web(cfg, args)
    keys = _keys(args)
    restore = bool(args.get("restore"))
    flag = 0 if restore else 1

    def change(data):
        return None if int(data.get("deleted") or 0) == flag else {"deleted": flag}

    verb = "Restored from trash" if restore else "Moved to trash"
    return _batch_report(verb, *client.modify_items(keys, change))


def t_status(cfg, args):
    lines = ["# Zotero connector status", ""]
    lines.append(f"- config file: {CONFIG_PATH} ({'present' if CONFIG_PATH.exists() else 'not created yet'})")
    found = cfg.sqlite_path.exists()
    lines.append(f"- data directory: {cfg.data_dir} ({'found' if found else 'NOT FOUND'})")
    lines.append(f"- detected via: {cfg.describe_discovery()}")
    if not found:
        lines.append(
            "  -> Zotero's database was not found. In Zotero, open Settings -> Advanced -> "
            "Files and Folders to see your data directory, then run: "
            "zotero-mcp setup --data-dir=/that/path"
        )
    lines.append(f"- default source: {cfg.default_source}")
    try:
        with localdb.connect(cfg) as conn:
            counts = conn.execute(
                "SELECT (SELECT COUNT(*) FROM items i JOIN itemTypesCombined t USING (itemTypeID) "
                "  WHERE t.typeName NOT IN ('attachment','note','annotation') "
                "  AND i.itemID NOT IN (SELECT itemID FROM deletedItems)) AS refs, "
                " (SELECT COUNT(*) FROM itemAttachments) AS atts, "
                " (SELECT COUNT(*) FROM itemNotes) AS notes, "
                " (SELECT COUNT(*) FROM collections) AS colls"
            ).fetchone()
            schema = localdb.schema_version(conn)
        lines.append(
            f"- local library: {counts['refs']} references, {counts['colls']} collections, "
            f"{counts['notes']} notes, {counts['atts']} attachments"
        )
        tested = localdb.TESTED_USERDATA_SCHEMA
        flag = "" if not schema or schema <= tested else f"  (newer than tested v{tested})"
        lines.append(f"- Zotero schema: userdata v{schema}{flag}")
    except Exception as exc:
        lines.append(f"- local library: UNAVAILABLE ({exc})")
    lines.append(f"- user ID: {cfg.library_id or 'unknown'}")
    if not cfg.api_key:
        lines.append("- web API: no key configured. Run `zotero-mcp setup` to add one "
                     "(create it at https://www.zotero.org/settings/keys/new).")
    else:
        try:
            info = ZoteroWeb(cfg).key_info()
            access = info.get("access", {}).get("user", {})
            perms = [k for k, v in access.items() if v]
            lines.append(f"- web API: key OK for user {info.get('userID')} ({', '.join(perms) or 'no user permissions'})")
        except WebApiError as exc:
            lines.append(f"- web API: key FAILED — {exc}")
    return "\n".join(lines)


HANDLERS = {
    "zotero_search": t_search,
    "zotero_get_item": t_get_item,
    "zotero_fulltext_search": t_fulltext_search,
    "zotero_collections": t_collections,
    "zotero_collection_items": t_collection_items,
    "zotero_tags": t_tags,
    "zotero_recent": t_recent,
    "zotero_attachments": t_attachments,
    "zotero_bibliography": t_bibliography,
    "zotero_libraries": t_libraries,
    "zotero_create_item": t_create_item,
    "zotero_add_note": t_add_note,
    "zotero_create_collection": t_create_collection,
    "zotero_update_collection": t_update_collection,
    "zotero_delete_collection": t_delete_collection,
    "zotero_add_to_collection": t_add_to_collection,
    "zotero_remove_from_collection": t_remove_from_collection,
    "zotero_update_item": t_update_item,
    "zotero_tag_items": t_tag_items,
    "zotero_rename_tag": t_rename_tag,
    "zotero_delete_tags": t_delete_tags,
    "zotero_trash_items": t_trash_items,
    "zotero_status": t_status,
}


def call(name, args):
    handler = HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"Unknown tool: {name}")
    return handler(Config(), args or {})
