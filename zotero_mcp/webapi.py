"""Zotero Web API v3 client (api.zotero.org) using only the standard library."""

import json
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.zotero.org"
API_VERSION = "3"
USER_AGENT = "zotero-mcp/1.0 (+local Claude Code connector)"
WRITE_BATCH = 50  # the API accepts at most 50 objects per write request


class WebApiError(RuntimeError):
    pass


class ZoteroWeb:
    def __init__(self, cfg, library=None):
        self.cfg = cfg
        self.api_key = cfg.api_key
        if not self.api_key:
            raise WebApiError(
                "No Zotero API key configured. Run `zotero-mcp setup` or set ZOTERO_API_KEY. "
                "Create a key at https://www.zotero.org/settings/keys/new"
            )
        self.prefix = self._library_prefix(library)

    def _library_prefix(self, library):
        """`library` may be None (the configured user library), 'user', or a group id/'group:ID'."""
        if library in (None, "", "user", "my", "mine"):
            lib_id = self.cfg.library_id
            if not lib_id:
                raise WebApiError(
                    "No Zotero user ID configured. Run `zotero-mcp setup` or set ZOTERO_USER_ID "
                    "(find it at https://www.zotero.org/settings/keys)."
                )
            return f"/{self.cfg.library_type}s/{lib_id}"
        text = str(library)
        if text.startswith("group:"):
            return f"/groups/{text.split(':', 1)[1]}"
        if text.startswith("user:"):
            return f"/users/{text.split(':', 1)[1]}"
        if text.isdigit():
            return f"/groups/{text}"
        raise WebApiError(f"Unrecognized library specifier {library!r}; use a group ID or 'user'.")

    # -- transport -------------------------------------------------------
    def request(self, path, params=None, method="GET", body=None, headers=None, absolute=False):
        url = path if absolute else f"{API_BASE}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "", [])}
            if clean:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Zotero-API-Version", API_VERSION)
        req.add_header("Zotero-API-Key", self.api_key)
        req.add_header("User-Agent", USER_AGENT)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read()
                info = {k.lower(): v for k, v in resp.headers.items()}
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            hint = ""
            if exc.code == 403:
                hint = " (check that the API key has access to this library)"
            elif exc.code == 404:
                hint = " (wrong library or item key?)"
            elif exc.code == 412:
                hint = " (the object changed on zotero.org since it was read; retry)"
            raise WebApiError(f"Zotero API {exc.code} {exc.reason}{hint}: {detail}") from None
        except urllib.error.URLError as exc:
            raise WebApiError(f"Could not reach api.zotero.org: {exc.reason}") from None
        if "json" in ctype:
            return json.loads(raw or b"null"), info
        return raw.decode(errors="replace"), info

    def get(self, path, params=None):
        return self.request(f"{self.prefix}{path}", params)[0]

    def get_all(self, path, params=None, cap=10000):
        """GET every page of a multi-object response (the API caps pages at 100)."""
        rows, params = [], dict(params or {})
        while len(rows) < cap:
            params.update(limit=100, start=len(rows))
            page, info = self.request(f"{self.prefix}{path}", params)
            rows += page
            total = int(info.get("total-results") or 0)
            if not page or len(rows) >= total:
                break
        return rows

    def library_version(self):
        _, info = self.request(f"{self.prefix}/items", {"limit": 1, "format": "versions"})
        return int(info.get("last-modified-version") or 0)

    # -- reads -----------------------------------------------------------
    def search(self, query=None, item_type=None, tag=None, collection=None, mode="titleCreatorYear",
               limit=25, sort="relevance", since=None):
        path = f"/collections/{collection}/items/top" if collection else "/items/top"
        params = {
            "q": query,
            "qmode": "everything" if mode == "everything" else "titleCreatorYear",
            "itemType": item_type,
            "tag": tag,
            "limit": min(int(limit), 100),
            "since": since,
        }
        if sort == "date":
            params["sort"] = "date"
            params["direction"] = "desc"
        elif sort == "added":
            params["sort"] = "dateAdded"
            params["direction"] = "desc"
        return [_flatten(r) for r in self.get(path, params)]

    def item(self, key, include_children=True):
        item = _flatten(self.get(f"/items/{key}"))
        if include_children:
            try:
                kids = [_flatten(r) for r in self.get(f"/items/{key}/children", {"limit": 100})]
            except WebApiError:
                kids = []
            item["notes"] = [k for k in kids if k.get("itemType") == "note"]
            item["attachments"] = [k for k in kids if k.get("itemType") == "attachment"]
        return item

    def children(self, key):
        return [_flatten(r) for r in self.get(f"/items/{key}/children", {"limit": 100})]

    def collections_flat(self):
        return [
            {
                "key": r["key"],
                "name": r["data"]["name"],
                "parentKey": r["data"].get("parentCollection") or None,
                "numItems": r["meta"].get("numItems", 0),
                "version": r["version"],
                "children": [],
            }
            for r in self.get_all("/collections")
        ]

    def collections(self):
        flat = self.collections_flat()
        by_key = {c["key"]: c for c in flat}
        roots = []
        for coll in flat:
            parent = by_key.get(coll["parentKey"])
            (parent["children"] if parent else roots).append(coll)
        return roots

    def collection_items(self, collection, limit=100):
        return [_flatten(r) for r in self.get(f"/collections/{collection}/items/top", {"limit": limit})]

    def tags(self, contains=None, limit=200):
        raw = self.get_all("/tags", {"q": contains, "qmode": "contains"}, cap=int(limit))[: int(limit)]
        return [{"tag": r["tag"], "numItems": r.get("meta", {}).get("numItems", 0)} for r in raw]

    def recent(self, limit=20, by="dateAdded"):
        return [
            _flatten(r)
            for r in self.get("/items/top", {"sort": by, "direction": "desc", "limit": min(int(limit), 100)})
        ]

    def bibliography(self, keys, style="apa", locale="en-US", mode="bib"):
        params = {
            "itemKey": ",".join(keys),
            "include": mode,
            "style": style,
            "locale": locale,
            "limit": 100,
        }
        rows = self.get("/items", params)
        return [(r["key"], r.get(mode, "")) for r in rows]

    def groups(self):
        user_id = self.cfg.library_id
        raw, _ = self.request(f"/users/{user_id}/groups", {"limit": 100})
        return [
            {
                "id": g["id"],
                "name": g["data"]["name"],
                "type": g["data"].get("type"),
                "numItems": g["meta"].get("numItems", 0),
            }
            for g in raw
        ]

    def key_info(self):
        raw, _ = self.request("/keys/current")
        return raw

    def fulltext(self, attachment_key):
        return self.get(f"/items/{attachment_key}/fulltext")

    # -- collection lookup -----------------------------------------------
    def resolve_collection(self, ref, flat=None):
        """Return the collection dict for a key, a name, or a 'Parent/Child' path (case-insensitive)."""
        flat = flat if flat is not None else self.collections_flat()
        text = str(ref or "").strip()
        for coll in flat:
            if coll["key"] == text:
                return coll
        by_key = {c["key"]: c for c in flat}

        def path_of(coll):
            parts = [coll["name"]]
            while coll.get("parentKey") in by_key:
                coll = by_key[coll["parentKey"]]
                parts.append(coll["name"])
            return "/".join(reversed(parts))

        want = text.strip("/").lower()
        hits = [c for c in flat if path_of(c).lower() == want] if "/" in want else []
        if not hits:
            hits = [c for c in flat if c["name"].lower() == want]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            options = "; ".join(f"{path_of(c)} (key {c['key']})" for c in hits)
            raise WebApiError(f"Collection name {ref!r} is ambiguous: {options}. Use the key or a Parent/Child path.")
        raise WebApiError(f"No collection named {ref!r} in this library. Use zotero_collections to list them.")

    # -- writes ----------------------------------------------------------
    def write_objects(self, path, objects):
        """POST objects in batches of 50. Returns (written, unchanged_keys, failures)."""
        written, unchanged, failed = [], [], []
        for start in range(0, len(objects), WRITE_BATCH):
            chunk = objects[start:start + WRITE_BATCH]
            result, _ = self.request(f"{self.prefix}{path}", method="POST", body=chunk)
            written += list((result.get("successful") or {}).values())
            unchanged += list((result.get("unchanged") or {}).values())
            for idx, err in (result.get("failed") or {}).items():
                obj = chunk[int(idx)] if str(idx).isdigit() and int(idx) < len(chunk) else {}
                failed.append({"key": err.get("key") or obj.get("key"), "message": err.get("message", str(err))})
        return written, unchanged, failed

    def create_items(self, payload):
        made, _, failed = self.write_objects("/items", payload)
        if failed and not made:
            raise WebApiError(f"Zotero rejected the write: {failed[0]['message']}")
        return [_flatten(v) for v in made]

    def raw_items(self, keys):
        """Raw API rows (with version and full data) for the given item keys."""
        rows = []
        keys = list(dict.fromkeys(keys))
        for start in range(0, len(keys), WRITE_BATCH):
            chunk = keys[start:start + WRITE_BATCH]
            rows += self.get("/items", {"itemKey": ",".join(chunk), "limit": WRITE_BATCH, "includeTrashed": 1})
        return rows

    def modify_items(self, keys, change):
        """Apply `change(data) -> dict of changed fields | None` to each item and save the diffs.

        Returns (changed_keys, unchanged_keys, missing_keys, failures)."""
        rows = self.raw_items(keys)
        found = {r["key"] for r in rows}
        missing = [k for k in keys if k not in found]
        patches, unchanged = [], []
        for row in rows:
            diff = change(row["data"])
            if diff:
                patches.append({"key": row["key"], "version": row["version"], **diff})
            else:
                unchanged.append(row["key"])
        written, same, failed = self.write_objects("/items", patches) if patches else ([], [], [])
        changed = [w.get("key") if isinstance(w, dict) else w for w in written]
        return changed, unchanged + same, missing, failed

    def items_with_tag(self, tag):
        return self.get_all("/items", {"tag": tag, "includeTrashed": 1})

    def delete_tags(self, tags):
        version = self.library_version()
        for start in range(0, len(tags), WRITE_BATCH):
            chunk = tags[start:start + WRITE_BATCH]
            _, info = self.request(
                f"{self.prefix}/tags",
                {"tag": " || ".join(chunk)},
                method="DELETE",
                headers={"If-Unmodified-Since-Version": str(version)},
            )
            version = int(info.get("last-modified-version") or version)

    def create_collection(self, name, parent_key=None):
        payload = {"name": name, "parentCollection": parent_key or False}
        made, _, failed = self.write_objects("/collections", [payload])
        if failed or not made:
            raise WebApiError(f"Zotero rejected the collection: {failed[0]['message'] if failed else 'no result'}")
        return made[0]

    def update_collection(self, key, changes):
        current = self.get(f"/collections/{key}")
        self.request(
            f"{self.prefix}/collections/{key}",
            method="PATCH",
            body=changes,
            headers={"If-Unmodified-Since-Version": str(current["version"])},
        )

    def delete_collection(self, key):
        current = self.get(f"/collections/{key}")
        self.request(
            f"{self.prefix}/collections/{key}",
            method="DELETE",
            headers={"If-Unmodified-Since-Version": str(current["version"])},
        )

    def item_template(self, item_type):
        raw, _ = self.request("/items/new", {"itemType": item_type}, absolute=False)
        return raw

    def update_item(self, key, changes):
        current = self.get(f"/items/{key}")
        version = current["version"]
        self.request(
            f"{self.prefix}/items/{key}",
            method="PATCH",
            body=changes,
            headers={"If-Unmodified-Since-Version": str(version)},
        )
        return self.item(key, include_children=False)


def _flatten(row):
    """Turn an API row ({key, version, data, meta}) into a flat item dict."""
    if not isinstance(row, dict):
        return row
    data = dict(row.get("data") or {})
    meta = row.get("meta") or {}
    data.setdefault("key", row.get("key"))
    data["version"] = row.get("version", data.get("version"))
    if "numChildren" in meta:
        data["numChildren"] = meta["numChildren"]
    if "creatorSummary" in meta:
        data["creatorSummary"] = meta["creatorSummary"]
    if "parsedDate" in meta:
        data["parsedDate"] = meta["parsedDate"]
    for extra in ("bib", "citation"):
        if extra in row:
            data[extra] = row[extra]
    # The API returns tags as [{"tag": "x", "type": 0}]; the local backend returns plain
    # strings. Normalize here so rendering never has to care which source it came from.
    tags = data.get("tags")
    if isinstance(tags, list):
        data["tags"] = [t.get("tag", "") if isinstance(t, dict) else str(t) for t in tags]
    creators = data.get("creators")
    if isinstance(creators, list):
        data["creators"] = [
            {
                "name": c.get("name") or ", ".join(p for p in (c.get("lastName"), c.get("firstName")) if p),
                "type": c.get("creatorType"),
            }
            for c in creators
        ]
    data["source"] = "web"
    return data
