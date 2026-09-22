"""Entry point: `zotero-mcp` (stdio MCP server) plus a few CLI helpers."""

import json
import sys
from pathlib import Path

from .config import CONFIG_PATH, Config


def cmd_setup(argv):
    """Store the API key / user ID without ever echoing the key to the terminal."""
    import getpass

    cfg = Config()
    args = dict(a.split("=", 1) for a in argv if "=" in a)
    api_key = args.get("--key")
    user_id = args.get("--user-id")
    data_dir = args.get("--data-dir")
    interactive = not args and sys.stdin.isatty()

    if data_dir:
        cfg.save(data_dir=str(Path(data_dir).expanduser()))
        cfg = Config()
    elif interactive and not cfg.sqlite_path.exists():
        print(f"No Zotero database at {cfg.data_dir}.")
        print("Zotero shows the real path under Settings -> Advanced -> Files and Folders.")
        answer = input("Zotero data directory (blank to skip): ").strip()
        if answer:
            cfg.save(data_dir=str(Path(answer).expanduser()))
            cfg = Config()

    if not api_key and interactive:
        note = " (blank keeps the current key)" if cfg.api_key else " (blank to skip)"
        print("\nAn API key is optional. It adds group libraries, CSL citation styles and writes.")
        print("Create one at https://www.zotero.org/settings/keys/new")
        api_key = getpass.getpass(f"Zotero API key{note}: ").strip() or None

    if not user_id:
        detected = cfg.local_user_id()
        if interactive:
            prompt = f"Zotero user ID [{detected}]: " if detected else "Zotero user ID (blank to skip): "
            user_id = input(prompt).strip() or (str(detected) if detected else None)
        else:
            user_id = str(detected) if detected else None

    path = cfg.save(api_key=api_key, user_id=user_id)
    print(f"\nSaved to {path} (mode 600).\n")
    cmd_status([])


def cmd_status(argv):
    from . import tools

    print(tools.call("zotero_status", {}))


def cmd_tool(argv):
    """zotero-mcp tool <name> '<json args>' — handy for debugging outside Claude."""
    from . import tools

    name = argv[0] if argv else "zotero_status"
    try:
        args = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(f"Arguments must be valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        print(tools.call(name, args))
    except Exception as exc:
        print(f"{exc}\n\nRun `zotero-mcp status` to check the configuration.", file=sys.stderr)
        sys.exit(1)


def cmd_tools(argv):
    from . import tools

    for tool in tools.TOOLS:
        print(f"{tool['name']}\n    {tool['description'][:160]}")


def main():
    argv = sys.argv[1:]
    command = argv[0] if argv else "serve"
    if command in ("serve", "--stdio", "stdio"):
        from .server import serve

        serve()
        return
    handlers = {"setup": cmd_setup, "status": cmd_status, "tool": cmd_tool, "tools": cmd_tools}
    handler = handlers.get(command)
    if handler is None:
        print(
            "usage: zotero-mcp [serve|setup|status|tools|tool <name> '<json>']\n"
            "  setup [--key=...] [--user-id=...] [--data-dir=...]\n"
            f"config: {CONFIG_PATH}",
            file=sys.stderr,
        )
        sys.exit(2)
    handler(argv[1:])


if __name__ == "__main__":
    main()
