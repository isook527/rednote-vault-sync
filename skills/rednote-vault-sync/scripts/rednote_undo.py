"""Record the files created by an import and undo only verified additions."""

import hashlib
import json
import os
import re
import uuid
from contextlib import nullcontext

from rednote_files import (
    InputError, ConflictError, RESERVED, checked_path, enrichment_lock, publish_file,
    read_text, relative_parts, report_text, sha256_file, text_field, user_error, valid_source_id,
)

MEDIA_NAME = re.compile(r"[0-9a-f]{64}\.[a-z0-9]+")


def save_receipt(report, vault, folder):
    receipt_id = uuid.uuid4().hex
    data = {
        "id": receipt_id,
        "vault": hashlib.sha256(os.fsencode(vault)).hexdigest(),
        "folder": "/".join(folder),
        "index": report["index"],
        "created_files": report["created_files"],
    }
    receipt = checked_path(vault, (*folder, ".rednote-receipts", f"{receipt_id}.json"))
    if not publish_file(receipt, content=report_text(data)):
        raise ConflictError("撤销记录路径已被占用。")
    report["receipt"] = data
    report["receipt_path"] = receipt.relative_to(vault).as_posix()


def receipt_paths(data, vault):
    if not isinstance(data, dict) or not re.fullmatch(r"[0-9a-f]{32}", str(data.get("id", ""))):
        raise InputError("报告没有有效的撤销记录。")
    if data.get("vault") != hashlib.sha256(os.fsencode(vault)).hexdigest():
        raise InputError("这份报告不属于当前 Vault。")
    folder = relative_parts(text_field(data, "folder"))
    receipt = checked_path(vault, (*folder, ".rednote-receipts", f"{data['id']}.json"))
    if not receipt.is_file() or json.loads(read_text(receipt)) != data:
        raise InputError("报告与 Vault 内的撤销记录不一致。")
    if not isinstance(data.get("created_files"), list):
        raise InputError("报告缺少新增文件清单。")

    entries = []
    for entry in data["created_files"]:
        if not isinstance(entry, dict):
            raise InputError("撤销清单条目格式不正确。")
        name, digest = text_field(entry, "path"), text_field(entry, "sha256")
        parts = relative_parts(name)
        tail = parts[len(folder):]
        if parts[:len(folder)] != folder or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise InputError("撤销清单包含越界路径或无效校验值。")
        if tail == ("索引.md",):
            kind = "index"
        elif len(tail) == 2 and tail[0] == "笔记" and tail[1].endswith(".md"):
            kind = "note"
        elif len(tail) == 3 and tail[0] == "媒体" and valid_source_id(tail[1]) and MEDIA_NAME.fullmatch(tail[2]):
            kind = "media"
        else:
            raise InputError("撤销清单包含不支持的文件位置。")
        entries.append((kind, parts, digest))
    return folder, entries


def referenced_media(vault, candidates, excluded):
    remaining = {path.name.casefold() for path in candidates}
    for note in vault.rglob("*.md"):
        if not remaining:
            break
        if note in excluded or set(note.relative_to(vault).parts) & RESERVED:
            continue
        if note.is_symlink():
            return True, set()
        try:
            text = read_text(note).casefold()
        except (OSError, UnicodeError):
            return True, set()
        remaining.difference_update(MEDIA_NAME.findall(text))
    return False, remaining


def undo_import(args, vault, report, refresh_index):
    raw = json.loads(read_text(args.undo))
    if not isinstance(raw, dict):
        raise InputError("撤销需要导入时生成的 JSON 报告。")
    data = raw.get("receipt")
    folder, entries = receipt_paths(data, vault)
    report["mode"] = "undo_preview" if args.dry_run else "undo"
    report.update({"removed": 0, "would_remove": 0, "kept": 0, "already_missing": 0})
    notes_dir = checked_path(vault, (*folder, "笔记"))
    guard = nullcontext() if args.dry_run or not notes_dir.is_dir() else enrichment_lock(notes_dir / "pending.md")
    with guard:
        return remove_receipt_files(args, vault, report, data, folder, entries, refresh_index)


def remove_receipt_files(args, vault, report, data, folder, entries, refresh_index):
    warning_count = len(report["warnings"])
    excluded = set()
    media = []
    index = []

    def remove(path, digest):
        name = path.relative_to(vault).as_posix()
        if path.is_symlink() or not path.is_file() or sha256_file(path) != digest:
            report["kept"] += 1
            report["records"].append({"path": name, "status": "kept", "reason": "文件已修改或路径发生变化"})
            return False
        status = "would_remove" if args.dry_run else "removed"
        if not args.dry_run:
            path.unlink()
        report[status] += 1
        report["records"].append({"path": name, "status": status})
        return True

    for kind, parts, digest in entries:
        name = "/".join(parts)
        try:
            path = checked_path(vault, parts)
            if not path.exists():
                report["already_missing"] += 1
                report["records"].append({"path": name, "status": "already_missing"})
            elif kind == "media":
                media.append((path, digest))
            elif kind == "index":
                index.append((path, digest))
            elif remove(path, digest):
                excluded.add(path)
        except (OSError, InputError) as error:
            report["kept"] += 1
            report["records"].append({"path": name, "status": "kept", "reason": user_error(error)})

    uncertain, unused = referenced_media(vault, [path for path, _ in media], excluded)
    for path, digest in media:
        if uncertain or path.name.casefold() not in unused:
            report["kept"] += 1
            report["records"].append({
                "path": path.relative_to(vault).as_posix(), "status": "kept",
                "reason": "仍有笔记引用该附件，或无法核验引用",
            })
        else:
            try:
                remove(path, digest)
            except (OSError, InputError) as error:
                report["kept"] += 1
                report["records"].append({"path": path.relative_to(vault).as_posix(),
                                          "status": "kept", "reason": user_error(error)})
    refresh = bool(excluded and data.get("index"))
    for path, digest in index:
        try:
            if not report["kept"] and path.is_file() and sha256_file(path) == digest:
                remove(path, digest)
                refresh = False
            elif not refresh:
                report["kept"] += 1
                report["records"].append({"path": path.relative_to(vault).as_posix(),
                                          "status": "kept", "reason": "索引已有改动"})
        except (OSError, InputError) as error:
            report["warnings"].append(user_error(error))
    if refresh:
        try:
            index_path = checked_path(vault, (*folder, "索引.md"))
            if index_path.is_file() and refresh_index(vault, folder, dry_run=args.dry_run, excluded=excluded):
                report["records"].append({
                    "path": index_path.relative_to(vault).as_posix(),
                    "status": "would_refresh_index" if args.dry_run else "refreshed_index",
                    "reason": "更新索引中的现有笔记链接",
                })
        except (OSError, InputError, UnicodeError) as error:
            report["warnings"].append(user_error(error))
    return 1 if report["kept"] or len(report["warnings"]) > warning_count else 0
