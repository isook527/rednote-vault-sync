#!/usr/bin/env python3
"""Find existing RedNote notes inside a local Obsidian Vault."""

import json
import re
import sys
from datetime import date
from pathlib import Path

if sys.flags.isolated:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from import_rednote import ArgumentParser, nonnegative
from rednote_files import InputError, checked_path, read_text, relative_parts, user_error
from rednote_notes import legacy_frontmatter, scan_notes


def query(args):
    if not args.vault.is_dir():
        raise InputError("目标 Vault 路径不存在或不是文件夹。")
    vault = args.vault.resolve()
    directory = checked_path(vault, (*relative_parts(args.folder), "笔记"))
    terms = [term.casefold() for value in args.text for term in value.split()]
    if args.since:
        try:
            since = date.fromisoformat(args.since).isoformat()
        except ValueError as error:
            raise InputError("--since 需为 YYYY-MM-DD。") from error
        if since != args.since:
            raise InputError("--since 需为 YYYY-MM-DD。")
    matches = []
    for paths in scan_notes(directory).values():
        for path in paths:
            fields, body = legacy_frontmatter(read_text(path))
            title = fields.get("title") or path.stem
            status = fields.get("status", "未读")
            saved_at = fields.get("savedAt", "")
            values = {
                "author": fields.get("author", ""),
                "collection": fields.get("collection", ""),
                "status": status,
            }
            if any(getattr(args, field) and getattr(args, field).casefold() != value.casefold()
                   for field, value in values.items()):
                continue
            if args.tag and args.tag.removeprefix("#").casefold() not in {x.casefold() for x in fields["tags"]}:
                continue
            if args.since and saved_at < args.since:
                continue
            text = " ".join(re.sub(r"<!--.*?-->", "", title + "\n" + body, flags=re.S).split())
            if not all(term in text.casefold() for term in terms):
                continue
            anchor = min(terms, key=text.casefold().count) if terms else ""
            match = re.search(re.escape(anchor), text, re.I) if anchor else None
            start, end = match.span() if match else (0, 0)
            relative = path.relative_to(vault)
            matches.append({"path": relative.as_posix(), "link": f"[[{relative.with_suffix('').as_posix()}]]",
                            "title": title, **values, "savedAt": saved_at,
                            "snippet": text[max(0, start - 40):end + 40]})
    matches.sort(key=lambda item: item["path"])
    matches.sort(key=lambda item: item["savedAt"], reverse=True)
    return matches[:args.limit]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--folder", default="小红书收藏")
    parser.add_argument("--text", action="append", default=[])
    parser.add_argument("--tag")
    parser.add_argument("--collection")
    parser.add_argument("--status", choices=["未读", "已读", "已用"])
    parser.add_argument("--author")
    parser.add_argument("--since")
    parser.add_argument("--limit", type=nonnegative, default=20)
    parser.add_argument("--output", choices=["json", "table"], default="json")
    parser.add_argument("--json-errors", action="store_true")
    args = parser.parse_args()
    try:
        matches = query(args)
    except (InputError, OSError, UnicodeError) as error:
        message = user_error(error)
        if args.json_errors or args.output == "json":
            message = json.dumps({"error": message, "code": "input"}, ensure_ascii=False)
        print(message, file=sys.stderr)
        return 2
    if args.output == "json":
        print(json.dumps({"count": len(matches), "matches": matches}, ensure_ascii=False, indent=2))
    else:
        print("笔记\t作者\t收藏夹\t状态\t片段")
        for item in matches:
            print(*(item[key] for key in ("link", "author", "collection", "status", "snippet")), sep="\t")
    return 0


if __name__ == "__main__":
    sys.exit(main())
