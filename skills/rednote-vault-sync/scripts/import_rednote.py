#!/usr/bin/env python3
"""Import user-supplied RedNote collections into an existing Obsidian vault."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

VERSION = "1.0.1"
NOTE_ID = re.compile(r"[0-9a-f]{24}", re.I)
URL_IN_TEXT = re.compile(r'https?://[^\s<>"\]\)【】]+', re.I)
WIKI_MEDIA = re.compile(r"!\[\[([^\]\n]+)\]\]")
REMOTE_MEDIA = re.compile(r"!\[[^\]\n]*\]\(https?://[^\n)]*\)", re.I)
QUERY_SECRET = re.compile(r"(?i)(?:xsec_token|access_token|refresh_token|api_key|session_id)=[^\s&#)\]<>]+")
MEDIA_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".mkv", ".mov", ".mp4", ".ogv", ".webm"}
RESERVED = {".obsidian", ".git", ".trash"}


class InputError(ValueError):
    """An actionable error without input contents or credentials."""


@dataclass
class Record:
    identity: str
    title: str
    url: str
    author: str = ""
    collection: str = ""
    body: str = ""
    tags: list = field(default_factory=list)
    media: list = field(default_factory=list)
    remote_media: int = 0
    unsupported_media: int = 0


def canonical_url(value):
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").lower()
        valid = parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host
    except ValueError:
        valid = False
    if not valid:
        raise InputError("请提供小红书原帖链接或 xhslink.com 分享短链。")
    path = parsed.path.rstrip("/")
    if host in {"www.xiaohongshu.com", "xiaohongshu.com", "m.xiaohongshu.com"}:
        match = re.fullmatch(r"/(?:explore|discovery/item|user/profile/[0-9a-f]{24})/([0-9a-f]{24})", path, re.I)
        if match:
            identity = match.group(1).lower()
            return identity, f"https://www.xiaohongshu.com/explore/{identity}"
    if host in {"xhslink.com", "www.xhslink.com"} and re.fullmatch(r"/(?:[A-Za-z0-9_-]{1,12}/)?[A-Za-z0-9_-]{3,64}", path):
        url = f"https://xhslink.com{path}"
        return "share-" + hashlib.sha256(url.encode()).hexdigest()[:24], url
    raise InputError("链接格式不受支持。请使用原帖直链或分享短链。")


def relative_parts(value):
    if value.startswith('/') or any(c in value for c in '\\:<>"|?*[]') or any(ord(c) < 32 for c in value):
        raise InputError("路径必须使用安全的相对路径和 / 分隔符。")
    parts = PurePosixPath(value).parts
    if not parts or any(p in {"/", ".."} or p.lower() in RESERVED for p in parts):
        raise InputError("路径越界或进入保留目录。")
    return parts


def checked_path(root, parts):
    path = root
    for index, part in enumerate(parts):
        path = path / part
        if path.is_symlink():
            raise InputError("输入与输出均不支持符号链接。")
        if index < len(parts) - 1 and path.exists() and not path.is_dir():
            raise InputError("路径中的文件夹位置被文件占用。")
    return path


def read_text(path):
    return path.read_text(encoding="utf-8-sig")


def text_field(data, key):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise InputError(f"{key} 必须是字符串。")
    return value.strip()


def media_file(root, name):
    path = checked_path(root, relative_parts(name))
    if path.suffix.lower() not in MEDIA_TYPES:
        raise InputError("附件格式不受支持。")
    if not path.is_file():
        raise InputError("找不到所声明的本地附件，请核对相对路径后重试。")
    if not path.stat().st_size:
        raise InputError("附件为空，请换成完整文件。")
    return path


def json_record(item, root, collection):
    if not isinstance(item, dict):
        raise InputError("每条收藏必须是一个 JSON 对象。")
    link = text_field(item, "url")
    stated = text_field(item, "id").lower()
    if stated and not NOTE_ID.fullmatch(stated):
        raise InputError("id 必须是 24 位十六进制小红书笔记 ID。")
    identity, url = canonical_url(link or f"https://www.xiaohongshu.com/explore/{stated}")
    if stated and not identity.startswith("share-") and stated != identity:
        raise InputError("id 和原帖链接指向不同笔记。")
    if stated:
        identity, url = canonical_url(f"https://www.xiaohongshu.com/explore/{stated}")

    def strings(key):
        values = item.get(key, [])
        if not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values):
            raise InputError(f"{key} 必须是由非空字符串组成的数组。")
        return list(dict.fromkeys(x.strip() for x in values))

    paths = [media_file(root, name) for name in strings("media")]
    return Record(
        identity, text_field(item, "title") or "小红书收藏", url,
        text_field(item, "author"), text_field(item, "collection") or collection,
        text_field(item, "body"), strings("tags"), paths,
    )


def share_items(path, collection):
    for number, line in enumerate(read_text(path).splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("# "):
            continue
        links = list(URL_IN_TEXT.finditer(line))
        for match in links:
            raw = match.group().rstrip("，。；、）！？：,.!?;»'")
            try:
                identity, url = canonical_url(raw)
            except InputError as error:
                yield number, error
                continue
            title = line[:match.start()].strip(" \t-·:：|") if len(links) == 1 else ""
            title = re.sub(r"\s*(?:复制本条.*|点击链接.*)$", "", title).strip()
            yield number, Record(identity, title or "小红书收藏", url, collection=collection)


def legacy_frontmatter(text):
    if not text.startswith("---\n"):
        return {}, text
    header, separator, tail = text[4:].partition("\n---\n")
    if not separator:
        return {}, text
    fields = {}
    tags = []
    reading_tags = False
    for line in header.splitlines():
        entry = re.fullmatch(r"\s+-\s+(.+)", line)
        if reading_tags and entry:
            value = legacy_scalar(entry.group(1))
            if value:
                tags.append(value)
            continue
        reading_tags = line.strip() == "tags:"
        pair = re.fullmatch(r"([A-Za-z]+):\s*(.*)", line)
        if pair:
            key, value = pair.groups()
            fields[key] = legacy_scalar(value)
    fields["tags"] = tags
    return fields, tail


def legacy_scalar(value):
    if value.startswith('"'):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ""
    return value if isinstance(value, str) else ""


def legacy_record(path, root, collection):
    fields, body = legacy_frontmatter(read_text(path))
    if fields.get("sourcePlugin") != "rednote-vault-sync" or fields.get("sourcePlatform") != "rednote":
        return None
    if fields.get("sourceType", "").endswith("media-index"):
        return None
    raw_id = fields.get("sourceId", "")
    if not NOTE_ID.fullmatch(raw_id):
        raise InputError("旧笔记缺少有效的 sourceId。")
    identity, url = canonical_url(f"https://www.xiaohongshu.com/explore/{raw_id}")
    paths = []
    unsupported = 0

    def collect_media(match):
        nonlocal unsupported
        name = match.group(1).split("|", 1)[0].strip()
        if Path(name).suffix.lower() not in MEDIA_TYPES:
            unsupported += 1
            return match.group()
        relative = relative_parts(name)
        candidate = checked_path(root, relative)
        if not candidate.exists():
            relative = (*path.parent.relative_to(root).parts, *relative)
        paths.append(media_file(root, "/".join(relative)))
        return ""

    body, count = REMOTE_MEDIA.subn("", body)
    body = WIKI_MEDIA.sub(collect_media, body).strip()
    body = re.sub(r"^# [^\n]+\n", "", body, count=1).strip()
    body = re.sub(r"(?m)^原文：\[在小红书打开\]\([^\n]*\)\s*$", "", body).strip()
    return Record(
        identity, fields.get("title") or path.stem, url, fields.get("author", ""),
        fields.get("collectionName") or fields.get("aestheticAlbumName") or collection,
        body, tags=fields["tags"], media=list(dict.fromkeys(paths)), remote_media=count,
        unsupported_media=unsupported,
    )


def records(args):
    source = args.source
    if args.format == "shares":
        yield from share_items(source, args.collection)
    elif args.format == "json":
        data = json.loads(read_text(source))
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("items"), list):
            raise InputError("JSON 顶层必须有 version: 1 和 items 数组。")
        for number, item in enumerate(data["items"], 1):
            try:
                yield number, json_record(item, source.parent, args.collection)
            except (InputError, OSError) as error:
                yield number, error
    else:
        root = args.legacy_vault
        if root is None or not root.is_dir() or not source.is_relative_to(root):
            raise InputError("旧库迁移需要 --legacy-vault，且输入必须在旧 Vault 内。")
        checked_path(root, source.relative_to(root).parts)
        candidates = [source] if source.is_file() else sorted(source.rglob("*.md"))
        for number, path in enumerate(candidates, 1):
            try:
                if any(part.lower() in RESERVED for part in path.relative_to(root).parts):
                    continue
                checked_path(root, path.relative_to(root).parts)
                record = legacy_record(path, root, args.collection)
                if record:
                    yield number, record
            except (InputError, OSError, UnicodeError) as error:
                yield number, error


def clean_text(value):
    value = "".join(c for c in value if ord(c) >= 32 or c in "\n\t")
    return QUERY_SECRET.sub("[已移除链接参数]", value)


def safe_markdown(value):
    value = clean_text(value)
    return value.replace("\\", r"\\").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("[", r"\[").replace("]", r"\]")


def note_markdown(record, attachments):
    title = " ".join(clean_text(record.title).split()) or "小红书收藏"
    tags = dict.fromkeys(
        tag for tag in record.tags if re.fullmatch(r"[\w/-]+", tag) and not tag.isdigit() and tag != "rednote"
    )
    properties = {
        "title": title, "aliases": [title], "sourcePlatform": "rednote",
        "sourceId": record.identity, "sourceUrl": record.url, "author": clean_text(record.author),
        "collection": clean_text(record.collection), "tags": ["rednote", *tags],
        "localMediaCount": len(attachments),
    }
    lines = ["---", *(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in properties.items()), "---", "", f"# {safe_markdown(title)}", ""]
    lines.extend([f"[打开小红书原帖]({record.url})", ""])
    if record.body:
        lines.extend(["## 收藏内容", "", safe_markdown(record.body), ""])
    if attachments:
        lines.extend(["## 本地附件", "", *(f"![[{p.as_posix()}]]" for p in attachments), ""])
    if record.remote_media:
        lines.extend([f"旧笔记有 {record.remote_media} 个远程媒体链接。", ""])
    return "\n".join(lines) + "\n"


def sha256_file(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def publish_file(dest, source=None, content=None, expected_digest=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".rednote-", delete=False) as handle:
            temp = Path(handle.name)
            if source:
                with source.open("rb") as media:
                    shutil.copyfileobj(media, handle)
            else:
                handle.write(content.encode("utf-8"))
        if expected_digest and sha256_file(temp) != expected_digest:
            raise InputError("附件在导入期间发生变化，请重新运行。")
        try:
            os.link(temp, dest)
            return True
        except FileExistsError:
            if dest.is_symlink() or not dest.is_file():
                raise InputError("输出路径冲突或目标是符号链接。")
            if expected_digest and sha256_file(dest) != expected_digest:
                raise InputError("目标附件存在但内容不一致。")
            return False
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def import_record(record, vault, folder, dry_run):
    note = checked_path(vault, (*folder, "笔记", f"{record.identity}.md"))
    if note.exists():
        if not note.is_file():
            raise InputError("笔记输出路径已被同名目录占用。")
        return "existing", note.relative_to(vault), 0
    attachments = {}
    for path in record.media:
        digest = sha256_file(path)
        parts = (*folder, "媒体", record.identity, f"{digest}{path.suffix.lower()}")
        dest = checked_path(vault, parts)
        if dest.exists() and (not dest.is_file() or sha256_file(dest) != digest):
            raise InputError("目标附件路径冲突或文件内容不一致。")
        attachments.setdefault(dest, (path, digest))
    if dry_run:
        return "planned", note.relative_to(vault), len(attachments)
    for dest, (path, digest) in attachments.items():
        if not dest.exists():
            publish_file(dest, source=path, expected_digest=digest)
    links = [path.relative_to(vault) for path in attachments]
    created = publish_file(note, content=note_markdown(record, links))
    return ("created" if created else "existing"), note.relative_to(vault), (len(links) if created else 0)


def user_error(error):
    if isinstance(error, InputError):
        return str(error)
    if isinstance(error, json.JSONDecodeError):
        return f"JSON 无法解析，检查第 {error.lineno} 行。"
    if isinstance(error, UnicodeError):
        return "输入须保存为 UTF-8 文本。"
    return "无法读取或写入文件，请检查文件类型、权限、磁盘空间和文件系统。"


def run(args):
    vault = args.vault.resolve(strict=True)
    args.source = Path(os.path.abspath(args.source))
    if args.legacy_vault:
        supplied_root = Path(os.path.abspath(args.legacy_vault))
        args.legacy_vault = supplied_root.resolve(strict=True)
        if args.source.is_relative_to(supplied_root):
            args.source = args.legacy_vault / args.source.relative_to(supplied_root)
    folder = relative_parts(args.folder)
    if not vault.is_dir():
        raise InputError("目标 Vault 必须是已存在的文件夹。")
    if args.source.is_symlink() or not args.source.exists():
        raise InputError("请提供存在的输入文件或旧笔记目录。")
    if args.format != "legacy" and not args.source.is_file():
        raise InputError("该输入格式需要一个文件。")
    output = checked_path(vault, folder)
    if args.format == "legacy" and args.source.is_dir() and output.is_relative_to(args.source):
        raise InputError("旧笔记源目录包含输出目录。请缩小输入范围或换一个目标 Vault。")
    report = {"mode": "preview" if args.dry_run else "import", "created": 0, "planned": 0, "existing": 0,
              "duplicate": 0, "failed": 0, "local_media": 0, "remote_media_skipped": 0,
              "unsupported_media_skipped": 0, "records": []}
    seen = set()
    for number, record in records(args):
        item = {"input": number}
        try:
            if isinstance(record, Exception):
                raise record
            if record.identity in seen:
                status, count = "duplicate", 0
            else:
                status, path, count = import_record(record, vault, folder, args.dry_run)
                item["note"] = path.as_posix()
                seen.add(record.identity)
                if status in {"created", "planned"}:
                    report["remote_media_skipped"] += record.remote_media
                    report["unsupported_media_skipped"] += record.unsupported_media
            report["local_media"] += count
        except (InputError, OSError, UnicodeError) as error:
            status = "failed"
            item["reason"] = user_error(error)
        report[status] += 1
        item["status"] = status
        report["records"].append(item)
    if not report["records"]:
        raise InputError("没有可导入的收藏。请检查输入格式与链接，或选择实际的旧笔记目录。")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["failed"] else 0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--format", choices=["shares", "json", "legacy"], required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--legacy-vault", type=Path)
    parser.add_argument("--folder", default="小红书收藏", help="目标 Vault 内的相对目录")
    parser.add_argument("--collection", default="", help="默认收藏夹名")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        return run(args)
    except (InputError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(json.dumps({"error": user_error(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
