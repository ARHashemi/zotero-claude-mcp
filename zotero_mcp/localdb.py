"""Read-only access to a local Zotero library.

Zotero holds an exclusive lock on zotero.sqlite while it runs, so we read from a
snapshot copy that is refreshed whenever the live database changes. That keeps
reads safe and never touches the user's data.
"""

import shutil
import sqlite3
import sys
from pathlib import Path

from .config import CACHE_DIR

ITEM_TYPE_EXCLUDE = ("attachment", "note", "annotation")

# The local backend reads Zotero's internal tables, which are an implementation
# detail Zotero may change between releases. This is the userdata schema version
# the queries were written and tested against; a newer one gets a warning, not a
# failure, since reads are read-only and most schema bumps are additive.
TESTED_USERDATA_SCHEMA = 129
_schema_warned = False
LINK_MODES = {0: "imported_file", 1: "imported_url", 2: "linked_file", 3: "linked_url"}


class LocalLibraryError(RuntimeError):
    pass


def _snapshot(source: Path, name: str) -> Path:
    """Copy `source` into the cache dir when it is newer than the last copy."""
    if not source.exists():
        raise LocalLibraryError(
            f"Zotero database not found at {source}. Find your data directory in Zotero under "
            "Settings -> Advanced -> Files and Folders, then run: "
            "zotero-mcp setup --data-dir=/that/path"
        )
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    snap = CACHE_DIR / name
    src_stat = source.stat()
    if snap.exists():
        snap_stat = snap.stat()
        if snap_stat.st_mtime >= src_stat.st_mtime and snap_stat.st_size == src_stat.st_size:
            return snap
    tmp = snap.with_suffix(snap.suffix + ".tmp")
    shutil.copy2(source, tmp)
    for suffix in ("-wal", "-shm"):
        side = source.with_name(source.name + suffix)
        if side.exists() and side.stat().st_size:
            shutil.copy2(side, tmp.with_name(tmp.name + suffix))
    tmp.replace(snap)
    return snap


def schema_version(conn):
    try:
        row = conn.execute("SELECT version FROM version WHERE schema='userdata'").fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _warn_on_new_schema(conn):
    global _schema_warned
    if _schema_warned:
        return
    _schema_warned = True
    found = schema_version(conn)
    if found and found > TESTED_USERDATA_SCHEMA:
        print(
            f"[zotero-mcp] note: this Zotero database uses userdata schema v{found}, newer than "
            f"the v{TESTED_USERDATA_SCHEMA} this connector was tested against. Reads are "
            "read-only and cannot harm the library, but if results look wrong please report it.",
            file=sys.stderr,
            flush=True,
        )


def connect(cfg, which="main"):
    source = cfg.sqlite_path if which == "main" else cfg.fulltext_path
    snap = _snapshot(source, f"{which}.sqlite")
    conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    if which == "main":
        _warn_on_new_schema(conn)
    return conn


def account_user_id(cfg):
    with connect(cfg) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE setting='account' AND key='userID'"
        ).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------
# libraries
# --------------------------------------------------------------------------

def libraries(cfg):
    with connect(cfg) as conn:
        rows = conn.execute(
            "SELECT l.libraryID, l.type, g.groupID, g.name "
            "FROM libraries l LEFT JOIN groups g USING (libraryID) ORDER BY l.libraryID"
        ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "libraryID": r["libraryID"],
                "type": r["type"],
                "name": r["name"] or ("My Library" if r["type"] == "user" else f"Library {r['libraryID']}"),
                "groupID": r["groupID"],
            }
        )
    return out


def _resolve_library(conn, library):
    """Accept a libraryID, a group id, or a library name; default to My Library."""
    if library in (None, "", "user", "my", "mine"):
        row = conn.execute("SELECT libraryID FROM libraries WHERE type='user'").fetchone()
        return row["libraryID"] if row else 1
    if isinstance(library, int) or str(library).isdigit():
        n = int(library)
        row = conn.execute("SELECT libraryID FROM libraries WHERE libraryID=?", (n,)).fetchone()
        if row:
            return n
        row = conn.execute("SELECT libraryID FROM groups WHERE groupID=?", (n,)).fetchone()
        if row:
            return row["libraryID"]
        raise LocalLibraryError(f"No local library with id {library}")
    row = conn.execute("SELECT libraryID FROM groups WHERE name=? COLLATE NOCASE", (library,)).fetchone()
    if row:
        return row["libraryID"]
    raise LocalLibraryError(f"No local library named {library!r}")


# --------------------------------------------------------------------------
# item assembly
# --------------------------------------------------------------------------

def _clean_date(value):
    """Zotero stores dates as '<SQL date> <original string>'; show the human part once."""
    parts = str(value).split(" ", 1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip() if parts[1].strip() != parts[0] else parts[0]
    return value


def _fields_for(conn, item_ids):
    if not item_ids:
        return {}
    marks = ",".join("?" * len(item_ids))
    rows = conn.execute(
        f"SELECT d.itemID, f.fieldName, v.value FROM itemData d "
        f"JOIN fieldsCombined f USING (fieldID) JOIN itemDataValues v USING (valueID) "
        f"WHERE d.itemID IN ({marks})",
        item_ids,
    ).fetchall()
    out = {}
    for r in rows:
        value = _clean_date(r["value"]) if r["fieldName"] == "date" else r["value"]
        out.setdefault(r["itemID"], {})[r["fieldName"]] = value
    return out


def _creators_for(conn, item_ids):
    if not item_ids:
        return {}
    marks = ",".join("?" * len(item_ids))
    rows = conn.execute(
        f"SELECT ic.itemID, c.firstName, c.lastName, c.fieldMode, ct.creatorType "
        f"FROM itemCreators ic JOIN creators c USING (creatorID) "
        f"JOIN creatorTypes ct USING (creatorTypeID) "
        f"WHERE ic.itemID IN ({marks}) ORDER BY ic.itemID, ic.orderIndex",
        item_ids,
    ).fetchall()
    out = {}
    for r in rows:
        name = r["lastName"] if r["fieldMode"] == 1 else ", ".join(p for p in (r["lastName"], r["firstName"]) if p)
        out.setdefault(r["itemID"], []).append({"name": name, "type": r["creatorType"]})
    return out


def _tags_for(conn, item_ids):
    if not item_ids:
        return {}
    marks = ",".join("?" * len(item_ids))
    rows = conn.execute(
        f"SELECT it.itemID, t.name FROM itemTags it JOIN tags t USING (tagID) "
        f"WHERE it.itemID IN ({marks}) ORDER BY t.name",
        item_ids,
    ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["itemID"], []).append(r["name"])
    return out


def _collections_for(conn, item_ids):
    if not item_ids:
        return {}
    marks = ",".join("?" * len(item_ids))
    rows = conn.execute(
        f"SELECT ci.itemID, c.collectionName, c.key FROM collectionItems ci "
        f"JOIN collections c USING (collectionID) WHERE ci.itemID IN ({marks})",
        item_ids,
    ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["itemID"], []).append({"name": r["collectionName"], "key": r["key"]})
    return out


def _attachment_path(cfg, key, link_mode, path):
    if not path:
        return None
    if path.startswith("storage:"):
        return str(cfg.storage_dir / key / path[len("storage:") :])
    if path.startswith("attachments:"):
        base = cfg.base_attachment_path
        rel = path[len("attachments:") :]
        return str(base / rel) if base else f"<base-dir>/{rel}"
    return path


def _children_for(cfg, conn, item_ids):
    """Notes, attachments and annotations hanging off the given items."""
    if not item_ids:
        return {}
    marks = ",".join("?" * len(item_ids))
    out = {}
    rows = conn.execute(
        f"SELECT n.parentItemID, i.key, n.title, n.note FROM itemNotes n JOIN items i USING (itemID) "
        f"WHERE n.parentItemID IN ({marks}) "
        f"AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
        item_ids,
    ).fetchall()
    for r in rows:
        out.setdefault(r["parentItemID"], {}).setdefault("notes", []).append(
            {"key": r["key"], "title": r["title"], "note": r["note"]}
        )
    rows = conn.execute(
        f"SELECT a.parentItemID, i.key, a.linkMode, a.contentType, a.path, "
        f"       (SELECT v.value FROM itemData d JOIN fieldsCombined f USING (fieldID) "
        f"        JOIN itemDataValues v USING (valueID) "
        f"        WHERE d.itemID=i.itemID AND f.fieldName='title') AS title "
        f"FROM itemAttachments a JOIN items i USING (itemID) "
        f"WHERE a.parentItemID IN ({marks}) "
        f"AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
        item_ids,
    ).fetchall()
    for r in rows:
        file_path = _attachment_path(cfg, r["key"], r["linkMode"], r["path"])
        out.setdefault(r["parentItemID"], {}).setdefault("attachments", []).append(
            {
                "key": r["key"],
                "title": r["title"],
                "contentType": r["contentType"],
                "linkMode": LINK_MODES.get(r["linkMode"], r["linkMode"]),
                "path": file_path,
                "exists": bool(file_path and Path(file_path).exists()),
            }
        )
    return out


def _load_items(cfg, conn, item_ids, with_children=False):
    if not item_ids:
        return []
    marks = ",".join("?" * len(item_ids))
    rows = conn.execute(
        f"SELECT i.itemID, i.key, i.libraryID, i.dateAdded, i.dateModified, t.typeName "
        f"FROM items i JOIN itemTypesCombined t USING (itemTypeID) WHERE i.itemID IN ({marks})",
        item_ids,
    ).fetchall()
    fields = _fields_for(conn, item_ids)
    creators = _creators_for(conn, item_ids)
    tags = _tags_for(conn, item_ids)
    colls = _collections_for(conn, item_ids)
    children = _children_for(cfg, conn, item_ids) if with_children else {}
    order = {iid: n for n, iid in enumerate(item_ids)}
    items = []
    for r in rows:
        iid = r["itemID"]
        item = {
            "key": r["key"],
            "itemType": r["typeName"],
            "libraryID": r["libraryID"],
            "dateAdded": r["dateAdded"],
            "dateModified": r["dateModified"],
            "creators": creators.get(iid, []),
            "tags": tags.get(iid, []),
            "collections": colls.get(iid, []),
            "source": "local",
        }
        item.update(fields.get(iid, {}))
        if with_children:
            kids = children.get(iid, {})
            item["notes"] = kids.get("notes", [])
            item["attachments"] = kids.get("attachments", [])
        items.append((order[iid], item))
    items.sort(key=lambda pair: pair[0])
    return [item for _, item in items]


def get_items_by_key(cfg, keys, library=None, with_children=True):
    with connect(cfg) as conn:
        lib = _resolve_library(conn, library)
        marks = ",".join("?" * len(keys))
        rows = conn.execute(
            f"SELECT itemID FROM items WHERE libraryID=? AND key IN ({marks})", [lib, *keys]
        ).fetchall()
        return _load_items(cfg, conn, [r["itemID"] for r in rows], with_children=with_children)


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

_TITLE_FIELDS = ("title", "shortTitle", "caseName", "subject", "nameOfAct")


def search(cfg, query=None, item_type=None, tag=None, collection=None, creator=None,
           year=None, mode="titleCreatorYear", limit=25, library=None, sort="relevance"):
    with connect(cfg) as conn:
        lib = _resolve_library(conn, library)
        where = [
            "i.libraryID = ?",
            "i.itemID NOT IN (SELECT itemID FROM deletedItems)",
            f"t.typeName NOT IN ({','.join('?' * len(ITEM_TYPE_EXCLUDE))})",
        ]
        params = [lib, *ITEM_TYPE_EXCLUDE]

        if item_type:
            types = [item_type] if isinstance(item_type, str) else list(item_type)
            where.append(f"t.typeName IN ({','.join('?' * len(types))})")
            params += types
        if tag:
            tags = [tag] if isinstance(tag, str) else list(tag)
            for one in tags:
                where.append(
                    "i.itemID IN (SELECT it.itemID FROM itemTags it JOIN tags tg USING (tagID) "
                    "WHERE tg.name = ? COLLATE NOCASE)"
                )
                params.append(one)
        if collection:
            where.append(
                "i.itemID IN (SELECT ci.itemID FROM collectionItems ci JOIN collections c "
                "USING (collectionID) WHERE c.key = ? OR c.collectionName = ? COLLATE NOCASE)"
            )
            params += [collection, collection]
        if creator:
            where.append(
                "i.itemID IN (SELECT ic.itemID FROM itemCreators ic JOIN creators cr USING (creatorID) "
                "WHERE cr.lastName LIKE ? COLLATE NOCASE OR cr.firstName LIKE ? COLLATE NOCASE)"
            )
            params += [f"%{creator}%", f"%{creator}%"]
        if year:
            where.append(
                "i.itemID IN (SELECT d.itemID FROM itemData d JOIN fieldsCombined f USING (fieldID) "
                "JOIN itemDataValues v USING (valueID) WHERE f.fieldName='date' AND v.value LIKE ?)"
            )
            params.append(f"%{year}%")

        fts_ids = set()
        if query:
            like = f"%{query}%"
            clauses = [
                "i.itemID IN (SELECT d.itemID FROM itemData d JOIN itemDataValues v USING (valueID) "
                "WHERE v.value LIKE ? COLLATE NOCASE)",
                "i.itemID IN (SELECT ic.itemID FROM itemCreators ic JOIN creators cr USING (creatorID) "
                "WHERE cr.lastName LIKE ? COLLATE NOCASE OR cr.firstName LIKE ? COLLATE NOCASE)",
                "i.itemID IN (SELECT it.itemID FROM itemTags it JOIN tags tg USING (tagID) "
                "WHERE tg.name LIKE ? COLLATE NOCASE)",
            ]
            query_params = [like, like, like, like]
            if mode == "everything":
                fts_ids = set(fulltext_item_ids(cfg, query))
                if fts_ids:
                    clauses.append(
                        "i.itemID IN (SELECT parentItemID FROM itemAttachments "
                        f"WHERE itemID IN ({','.join('?' * len(fts_ids))}))"
                    )
                    query_params += list(fts_ids)
                    clauses.append(
                        "i.itemID IN (SELECT parentItemID FROM itemNotes "
                        f"WHERE itemID IN ({','.join('?' * len(fts_ids))}))"
                    )
                    query_params += list(fts_ids)
            where.append("(" + " OR ".join(clauses) + ")")
            params += query_params

        sql = (
            "SELECT i.itemID FROM items i JOIN itemTypesCombined t USING (itemTypeID) "
            f"WHERE {' AND '.join(where)} ORDER BY i.dateModified DESC LIMIT ?"
        )
        params.append(max(limit * 8, 200) if query and sort == "relevance" else limit)
        ids = [r["itemID"] for r in conn.execute(sql, params).fetchall()]
        items = _load_items(cfg, conn, ids)

    if query and sort == "relevance":
        items = _rank(items, query)
    elif sort == "date":
        items.sort(key=lambda it: it.get("date", ""), reverse=True)
    return items[:limit]


def _rank(items, query):
    q = query.lower()
    terms = [t for t in q.split() if t]

    def score(item):
        title = str(item.get("title", "")).lower()
        creators = " ".join(c["name"] for c in item.get("creators", [])).lower()
        s = 0
        if q in title:
            s += 100
        s += 12 * sum(1 for t in terms if t in title)
        if q in creators:
            s += 40
        s += 6 * sum(1 for t in terms if t in creators)
        s += 3 * sum(1 for t in terms if any(t in str(tag).lower() for tag in item.get("tags", [])))
        abstract = str(item.get("abstractNote", "")).lower()
        s += sum(1 for t in terms if t in abstract)
        return (-s, item.get("dateModified", ""))

    return sorted(items, key=score)


def fulltext_item_ids(cfg, query, limit=200):
    """Attachment/note itemIDs whose indexed text matches `query` (FTS5)."""
    try:
        conn = connect(cfg, "fulltext")
    except LocalLibraryError:
        return []
    match = " ".join(f'"{t}"' for t in query.split()) if '"' not in query else query
    ids = []
    with conn:
        for table in ("fulltextContent", "fulltextContentCJK", "fulltextNotes"):
            try:
                rows = conn.execute(
                    f"SELECT rowid FROM {table} WHERE {table} MATCH ? LIMIT ?", (match, limit)
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            ids += [r[0] for r in rows]
    return ids


def fulltext_search(cfg, query, limit=20, library=None):
    """Parent items whose PDF/note full text matches, with the matching attachment."""
    ids = fulltext_item_ids(cfg, query)
    if not ids:
        return []
    with connect(cfg) as conn:
        lib = _resolve_library(conn, library)
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT a.itemID AS childID, COALESCE(a.parentItemID, a.itemID) AS topID, 'attachment' AS kind "
            f"FROM itemAttachments a WHERE a.itemID IN ({marks}) "
            f"UNION ALL "
            f"SELECT n.itemID, COALESCE(n.parentItemID, n.itemID), 'note' "
            f"FROM itemNotes n WHERE n.itemID IN ({marks})",
            [*ids, *ids],
        ).fetchall()
        hits, seen = [], set()
        for r in rows:
            if r["topID"] in seen:
                continue
            seen.add(r["topID"])
            hits.append((r["topID"], r["kind"]))
        top_ids = [h[0] for h in hits][:limit]
        top_ids = [
            r["itemID"]
            for r in conn.execute(
                f"SELECT itemID FROM items WHERE libraryID=? AND itemID IN ({','.join('?' * len(top_ids))})",
                [lib, *top_ids],
            ).fetchall()
        ] if top_ids else []
        items = _load_items(cfg, conn, top_ids, with_children=True)
    kind_by_id = dict(hits)
    for item in items:
        item["matchedIn"] = "full text"
    return items


def note_text(cfg, item_ids):
    """Plain text of notes, from the maintained fulltext index."""
    try:
        conn = connect(cfg, "fulltext")
    except LocalLibraryError:
        return {}
    marks = ",".join("?" * len(item_ids))
    with conn:
        rows = conn.execute(
            f"SELECT itemID, text FROM noteText WHERE itemID IN ({marks})", item_ids
        ).fetchall()
    return {r["itemID"]: r["text"] for r in rows}


# --------------------------------------------------------------------------
# collections / tags / recency
# --------------------------------------------------------------------------

def collections(cfg, library=None):
    with connect(cfg) as conn:
        lib = _resolve_library(conn, library)
        rows = conn.execute(
            "SELECT c.collectionID, c.key, c.collectionName, c.parentCollectionID, "
            "(SELECT COUNT(*) FROM collectionItems ci WHERE ci.collectionID = c.collectionID) AS n "
            "FROM collections c WHERE c.libraryID = ? ORDER BY c.collectionName COLLATE NOCASE",
            (lib,),
        ).fetchall()
    by_id = {
        r["collectionID"]: {
            "key": r["key"],
            "name": r["collectionName"],
            "parentID": r["parentCollectionID"],
            "id": r["collectionID"],
            "numItems": r["n"],
            "children": [],
        }
        for r in rows
    }
    roots = []
    for coll in by_id.values():
        parent = by_id.get(coll["parentID"])
        (parent["children"] if parent else roots).append(coll)
    return roots


def collection_items(cfg, collection, limit=100, library=None, recursive=False):
    with connect(cfg) as conn:
        lib = _resolve_library(conn, library)
        row = conn.execute(
            "SELECT collectionID, collectionName FROM collections "
            "WHERE libraryID=? AND (key=? OR collectionName=? COLLATE NOCASE)",
            (lib, collection, collection),
        ).fetchone()
        if not row:
            raise LocalLibraryError(f"No collection matching {collection!r} in this library")
        ids = [row["collectionID"]]
        if recursive:
            frontier = list(ids)
            while frontier:
                kids = conn.execute(
                    f"SELECT collectionID FROM collections WHERE parentCollectionID IN "
                    f"({','.join('?' * len(frontier))})",
                    frontier,
                ).fetchall()
                frontier = [k["collectionID"] for k in kids]
                ids += frontier
        marks = ",".join("?" * len(ids))
        item_ids = [
            r["itemID"]
            for r in conn.execute(
                f"SELECT ci.itemID FROM collectionItems ci JOIN items i USING (itemID) "
                f"JOIN itemTypesCombined t USING (itemTypeID) "
                f"WHERE ci.collectionID IN ({marks}) "
                f"AND t.typeName NOT IN ({','.join('?' * len(ITEM_TYPE_EXCLUDE))}) "
                f"AND i.itemID NOT IN (SELECT itemID FROM deletedItems) "
                f"ORDER BY i.dateModified DESC LIMIT ?",
                [*ids, *ITEM_TYPE_EXCLUDE, limit],
            ).fetchall()
        ]
        return row["collectionName"], _load_items(cfg, conn, item_ids)


def tags(cfg, library=None, contains=None, limit=200):
    with connect(cfg) as conn:
        lib = _resolve_library(conn, library)
        sql = (
            "SELECT t.name, COUNT(*) AS n FROM itemTags it JOIN tags t USING (tagID) "
            "JOIN items i USING (itemID) WHERE i.libraryID = ? "
        )
        params = [lib]
        if contains:
            sql += "AND t.name LIKE ? COLLATE NOCASE "
            params.append(f"%{contains}%")
        sql += "GROUP BY t.name ORDER BY n DESC, t.name LIMIT ?"
        params.append(limit)
        return [{"tag": r["name"], "numItems": r["n"]} for r in conn.execute(sql, params).fetchall()]


def recent(cfg, limit=20, by="dateAdded", library=None):
    column = "dateAdded" if by == "dateAdded" else "dateModified"
    with connect(cfg) as conn:
        lib = _resolve_library(conn, library)
        ids = [
            r["itemID"]
            for r in conn.execute(
                f"SELECT i.itemID FROM items i JOIN itemTypesCombined t USING (itemTypeID) "
                f"WHERE i.libraryID=? AND t.typeName NOT IN ({','.join('?' * len(ITEM_TYPE_EXCLUDE))}) "
                f"AND i.itemID NOT IN (SELECT itemID FROM deletedItems) "
                f"ORDER BY i.{column} DESC LIMIT ?",
                [lib, *ITEM_TYPE_EXCLUDE, limit],
            ).fetchall()
        ]
        return _load_items(cfg, conn, ids)


def attachments_for(cfg, keys, library=None):
    items = get_items_by_key(cfg, keys, library=library, with_children=True)
    return [(it, it.get("attachments", [])) for it in items]
