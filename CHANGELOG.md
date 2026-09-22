# Changelog

## 1.1.0 — 2026-09-22

Library organisation. Ten new write tools, all via the zotero.org web API:

- Collections: `zotero_create_collection`, `zotero_update_collection` (rename/move),
  `zotero_delete_collection` (keeps items), `zotero_add_to_collection` (batch, optional
  `create`), `zotero_remove_from_collection`.
- Items: `zotero_update_item` (fields, creators, tags), `zotero_tag_items` (batch),
  `zotero_trash_items` (trash or restore; never permanent).
- Tags: `zotero_rename_tag` (rename or merge library-wide), `zotero_delete_tags`.

Also:

- Collections can be given by key, name, or `Parent/Child` path everywhere on the web
  source, including `zotero_search`, `zotero_collection_items` and `zotero_create_item`.
- Web collection and tag listings now page through the full result instead of stopping at
  100.
- Writes report skipped, missing and failed items, and note that the desktop app shows
  them after its next sync.
- HTTP 412 (edited elsewhere in the meantime) gets an explanatory message.

## 1.0.0 — 2026-09-22

First release: local-database and web-API read tools, CSL bibliography, `zotero_create_item`,
`zotero_add_note`, installer.
