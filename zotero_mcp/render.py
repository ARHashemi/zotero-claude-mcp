"""Compact, readable text rendering of Zotero records for an LLM to consume."""

CORE_FIELDS = [
    "title", "shortTitle", "abstractNote", "date", "publicationTitle", "bookTitle",
    "proceedingsTitle", "journalAbbreviation", "volume", "issue", "pages", "series",
    "publisher", "place", "edition", "institution", "university", "conferenceName",
    "DOI", "ISBN", "ISSN", "url", "language", "extra", "archive", "archiveLocation",
    "callNumber", "rights", "repository", "reportNumber", "patentNumber", "thesisType",
    "presentationType", "websiteType", "accessDate", "libraryCatalog",
]
SKIP_IN_FULL = {"creators", "tags", "collections", "notes", "attachments", "source", "key",
                "itemType", "libraryID", "version"}


def _year(item):
    date = str(item.get("parsedDate") or item.get("date") or "")
    for chunk in date.replace("/", "-").split("-"):
        if len(chunk) == 4 and chunk.isdigit():
            return chunk
    digits = "".join(c for c in date if c.isdigit())
    return digits[:4] if len(digits) >= 4 else (date or "n.d.")


def authors(item, limit=3):
    names = [c["name"] for c in item.get("creators", []) if c.get("name")]
    if not names:
        return item.get("creatorSummary") or "No author"
    short = [n.split(",")[0].strip() for n in names]
    if len(short) > limit:
        return f"{short[0]} et al."
    if len(short) == 1:
        return short[0]
    return ", ".join(short[:-1]) + f" & {short[-1]}"


def one_line(item, index=None):
    prefix = f"{index}. " if index is not None else ""
    venue = (
        item.get("publicationTitle")
        or item.get("bookTitle")
        or item.get("proceedingsTitle")
        or item.get("publisher")
        or item.get("university")
        or item.get("repository")
        or ""
    )
    bits = [f"**{item.get('title', '(untitled)')}**"]
    bits.append(f"{authors(item)} ({_year(item)})")
    if venue:
        bits.append(f"_{venue}_")
    line = f"{prefix}" + "\n   ".join(bits)
    meta = [item.get("itemType", "?"), f"key: {item.get('key')}"]
    if item.get("DOI"):
        meta.append(f"doi: {item['DOI']}")
    if item.get("numChildren"):
        meta.append(f"{item['numChildren']} child item(s)")
    if item.get("tags"):
        shown = item["tags"][:6]
        meta.append("tags: " + ", ".join(shown) + ("…" if len(item["tags"]) > 6 else ""))
    line += "\n   " + " · ".join(str(m) for m in meta)
    return line


def item_list(items, header=None, source=None, empty="No matching items."):
    if not items:
        return empty
    lines = []
    if header:
        tail = f" (from {source} library)" if source else ""
        lines.append(f"{header}{tail}: {len(items)} item(s)\n")
    lines += [one_line(it, n) for n, it in enumerate(items, 1)]
    return "\n".join(lines)


def full_item(item, note_bodies=None):
    lines = [f"# {item.get('title', '(untitled)')}", ""]
    lines.append(f"- itemType: {item.get('itemType')}")
    lines.append(f"- key: {item.get('key')}  ·  source: {item.get('source')}")
    creators = item.get("creators") or []
    if creators:
        grouped = {}
        for c in creators:
            grouped.setdefault(c.get("type") or "author", []).append(c["name"])
        for role, names in grouped.items():
            lines.append(f"- {role}s: {'; '.join(names)}")
    for field in CORE_FIELDS:
        value = item.get(field)
        if value:
            lines.append(f"- {field}: {value}")
    for field, value in sorted(item.items()):
        if field in SKIP_IN_FULL or field in CORE_FIELDS or not value:
            continue
        if field in ("numChildren", "creatorSummary", "parsedDate", "matchedIn"):
            continue
        lines.append(f"- {field}: {value}")
    if item.get("tags"):
        lines.append(f"- tags: {', '.join(str(t) for t in item['tags'])}")
    if item.get("collections"):
        names = [c["name"] if isinstance(c, dict) else str(c) for c in item["collections"]]
        lines.append(f"- collections: {', '.join(names)}")
    attachments = item.get("attachments") or []
    if attachments:
        lines.append("\n## Attachments")
        for att in attachments:
            bits = [att.get("title") or att.get("filename") or att.get("key")]
            if att.get("contentType"):
                bits.append(att["contentType"])
            if att.get("path"):
                bits.append(("" if att.get("exists", True) else "MISSING ") + att["path"])
            lines.append("- " + " · ".join(str(b) for b in bits))
    notes = item.get("notes") or []
    if notes:
        lines.append("\n## Notes")
        for note in notes:
            body = (note_bodies or {}).get(note.get("key")) or note.get("note") or ""
            body = strip_html(body)
            title = note.get("title") or (body.splitlines()[0][:60] if body else note.get("key"))
            lines.append(f"\n### {title}  (key: {note.get('key')})\n{body[:4000]}")
    return "\n".join(lines)


def collection_tree(nodes, depth=0):
    lines = []
    for node in nodes:
        pad = "  " * depth
        lines.append(f"{pad}- {node['name']}  ({node.get('numItems', 0)} items, key: {node['key']})")
        if node.get("children"):
            lines.append(collection_tree(node["children"], depth + 1))
    return "\n".join(l for l in lines if l)


def strip_html(html):
    import html as html_mod
    import re

    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", html or "", flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
