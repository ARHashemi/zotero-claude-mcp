"""Zotero Web API v3 client (api.zotero.org) using only the standard library."""

import json
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.zotero.org"
API_VERSION = "3"
USER_AGENT = "zotero-mcp/1.0 (+local Claude Code connector)"


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
                info = dict(resp.headers)
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            hint = ""
            if exc.code == 403:
                hint = " (check that the API key has access to this library)"
            elif exc.code == 404:
                hint = " (wrong library or item key?)"
            raise WebApiError(f"Zotero API {exc.code} {exc.reason}{hint}: {detail}") from None
        except urllib.error.URLError as exc:
            raise WebApiError(f"Could not reach api.zotero.org: {exc.reason}") from None
        if "json" in ctype:
            return json.loads(raw or b"null"), info
        return raw.decode(errors="replace"), info

    def get(self, path, params=None):
        return self.request(f"{self.prefix}{path}", params)[0]

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

    def collections(self):
        raw = self.get("/collections", {"limit": 100})
        flat = [
            {
                "key": r["key"],
                "name": r["data"]["name"],
                "parentKey": r["data"].get("parentCollection") or None,
                "numItems": r["meta"].get("numItems", 0),
                "children": [],
            }
            for r in raw
        ]
        by_key = {c["key"]: c for c in flat}
        roots = []
        for coll in flat:
            parent = by_key.get(coll["parentKey"])
            (parent["children"] if parent else roots).append(coll)
        return roots

    def collection_items(self, collection, limit=100):
        return [_flatten(r) for r in self.get(f"/collections/{collection}/items/top", {"limit": limit})]

    def tags(self, contains=None, limit=200):
        raw = self.get("/tags", {"q": contains, "limit": min(int(limit), 100)})
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

    # -- writes ----------------------------------------------------------
    def create_items(self, payload):
        result, _ = self.request(f"{self.prefix}/items", method="POST", body=payload)
        failed = result.get("failed") or {}
        if failed:
            first = next(iter(failed.values()))
            raise WebApiError(f"Zotero rejected the write: {first.get('message', failed)}")
        made = result.get("successful") or {}
        return [_flatten(v) for v in made.values()]

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
