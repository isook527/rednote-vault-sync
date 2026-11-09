#!/usr/bin/env python3
"""Import user-supplied RedNote collections into an existing Obsidian vault."""

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

if sys.flags.isolated:
    # Isolated Python omits the script directory from its import path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from rednote_files import (
    InputError, ConflictError, RESERVED, checked_path, enrichment_lock, publish_file,
    read_text, relative_parts, report_text, sha256_file, text_field, user_error, valid_source_id,
)
from rednote_undo import save_receipt, undo_import
from rednote_annotate import annotate
from rednote_inbox import Inbox, INTRO
from rednote_notes import clean_text, legacy_frontmatter, note_fields, safe_markdown, scan_notes

NOTE_ID = re.compile(r"[0-9a-f]{24}", re.I)
URL_IN_TEXT = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&'*+,;=%]+", re.I)
SHARE_WRAPPER = re.compile(r"【([^】]+?)\s*\|\s*小红书[^】]*】")
SHARE_CODE = re.compile(r"([\U0001F300-\U0001FAFF])\s*[A-Za-z0-9]+\s*\1")
SHARE_FOOTER = re.compile(r"\s*(?:复制本条(?:信息)?|打开【小红书】(?:App)?|点击链接).*$", re.I)
WIKI_MEDIA = re.compile(r"!\[\[([^\]\n]+)\]\]")
REMOTE_MEDIA = re.compile(r"!\[[^\]\n]*\]\(https?://[^\n)]*\)", re.I)
MEDIA_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".mkv", ".mov", ".mp4", ".ogv", ".webm"}
BODY_START = "<!-- rednote:body:start -->"
BODY_END = "<!-- rednote:body:end -->"
INDEX_HEADER = re.compile(r"\A<!-- rednote:index ([0-9a-f]{64}) -->\n")
BASE_HEADER = re.compile(r"\A# rednote:base ([0-9a-f]{64})\n")


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
    keep_hashtags: bool = False
    input_start: int = 0


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
        if re.fullmatch(r"/user/profile/[0-9a-f]{24}", path, re.I):
            raise InputError("这是用户主页链接，请提供单篇笔记的分享链接。")
        match = re.fullmatch(r"/(?:explore|discovery/item|user/profile/[0-9a-f]{24})/([0-9a-f]{24})", path, re.I)
        if match:
            identity = match.group(1).lower()
            return identity, f"https://www.xiaohongshu.com/explore/{identity}"
    if host in {"xhslink.com", "www.xhslink.com"} and re.fullmatch(r"/(?:[A-Za-z0-9_-]{1,12}/)?[A-Za-z0-9_-]{3,64}", path):
        url = f"https://xhslink.com{path}"
        return "share-" + hashlib.sha256(url.encode()).hexdigest()[:24], url
    raise InputError("链接格式不受支持。请使用原帖直链或分享短链。")


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


def parse_share_line(line, match=None):
    text = line[:match.start()] if match else line
    text = re.sub(r"^\s*\d+\s+", "", text.strip())
    wrapped = SHARE_WRAPPER.search(text)
    if wrapped:
        label = wrapped[1].strip()
        title, separator, author = label.rpartition(" - ")
        return (title.strip(), author.strip()) if separator else (label, "")
    text = SHARE_CODE.sub("", text)
    text = SHARE_FOOTER.sub("", text)
    return text.strip(" \t-·:：|（(［[【"), ""


def share_items(path, collection):
    yield from share_lines(read_text(path).splitlines(), collection)


def share_lines(lines, collection):
    previous = ""
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith(("#", "<!--", "> [!warning]")):
            previous = ""
            continue
        links = list(URL_IN_TEXT.finditer(line))
        if not links:
            previous = line
            continue
        for match in links:
            raw = match.group().rstrip("，。；、）！？：,.!?;»')]")
            try:
                identity, url = canonical_url(raw)
            except InputError as error:
                yield number, error
                continue
            title, author = parse_share_line(line, match) if len(links) == 1 else ("", "")
            start = number
            if len(links) == 1 and not title and previous:
                title, author = parse_share_line(previous)
                if title:
                    start = number - 1
            yield number, Record(identity, title or "小红书收藏", url, author=author,
                                 collection=collection, input_start=start)
        previous = ""


def share_batch(args, report):
    items = list(share_items(args.source, args.collection))
    if not args.attach and not args.body_file:
        return items
    identities = {record.identity for _, record in items if isinstance(record, Record)}
    if len(identities) != 1 or any(isinstance(record, Exception) for _, record in items):
        report["unassigned_media"] = list(range(1, len(args.attach) + 1))
        report["unassigned_body"] = bool(args.body_file)
        return items

    number, record = items[0]
    try:
        if args.body_file:
            if args.body_file.is_symlink() or not args.body_file.is_file():
                raise InputError("请指定可读取的正文文本文件。")
            record.body = read_text(args.body_file).strip()
        for index, path in enumerate(args.attach, 1):
            try:
                record.media.append(media_file(path.parent, path.name))
            except (InputError, OSError) as error:
                raise InputError(f"第 {index} 个附件无法导入。{user_error(error)}") from error
    except (InputError, OSError, UnicodeError) as error:
        return [(number, error)]
    return items


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


def records(args, report):
    source = args.source
    if args.format == "shares":
        yield from share_batch(args, report)
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


def body_section(record):
    return ["## 收藏内容", "", BODY_START, safe_markdown(record.body, record.keep_hashtags), BODY_END, ""]


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
        "status": "未读",
    }
    lines = ["---", *(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in properties.items()),
             f"savedAt: {date.today().isoformat()}", "---", "", f"# {safe_markdown(title)}", ""]
    lines.extend([f"[打开小红书原帖]({record.url})", ""])
    if record.body:
        lines.extend(body_section(record))
    if attachments:
        lines.extend(["## 本地附件", "", *(f"![[{p.as_posix()}]]" for p in attachments), ""])
    if record.remote_media:
        lines.extend([f"旧笔记有 {record.remote_media} 个远程媒体链接。", ""])
    lines.extend(["## 我的笔记", ""])
    return "\n".join(lines) + "\n"


def enrich_markdown(original, record, links, media_prefix, stored_id):
    text = original.decode("utf-8")
    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n")
    fields, body = legacy_frontmatter(normalized)
    try:
        url_id, _ = canonical_url(fields.get("sourceUrl", ""))
    except InputError:
        url_id = None
    if (fields.get("sourcePlatform"), fields.get("sourceId"), url_id) != ("rednote", stored_id, stored_id):
        raise ConflictError("现有文件的来源属性与这条收藏不一致。")

    additions = []
    if record.body:
        if BODY_START in body or BODY_END in body:
            sections = re.findall(r"^" + BODY_START + r"\n(.*?)\n" + BODY_END + r"$", body, re.M | re.S)
            if len(sections) != 1 or body.count(BODY_START) != 1 or body.count(BODY_END) != 1:
                raise ConflictError("收藏正文的边界已被编辑，请核对后再补全。")
        else:
            sections = re.findall(r"^## 收藏内容[ \t]*\n(.*?)(?=^#{1,2} |\Z)", body, re.M | re.S)
        if sections:
            raw = clean_text(record.body)
            legacy = raw.replace("\\", r"\\").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("[", r"\[").replace("]", r"\]")
            expected = {safe_markdown(record.body, record.keep_hashtags).strip(), legacy.strip()}
            if len(sections) != 1 or sections[0].strip() not in expected:
                raise ConflictError("已有收藏正文与本次不同，请核对后再补全。")
        else:
            additions.extend(body_section(record))

    prefix = media_prefix.as_posix() + "/"
    known_links = {
        link for link in WIKI_MEDIA.findall(body)
        if link.startswith(prefix) and re.fullmatch(r"[0-9a-f]{64}\.[a-z0-9]+", link[len(prefix):])
    }
    new_links = [link for link in links if link.as_posix() not in known_links]
    newline = "\r\n" if "\r\n" in text else "\n"
    if new_links:
        header = re.match(r"\A\ufeff?---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.S)
        counts = list(re.finditer(r"^localMediaCount:[ \t]*([0-9]+)[ \t]*(?=\r?$)", header[1], re.M))
        if len(counts) != 1 or int(counts[0][1]) != len(known_links):
            raise ConflictError("现有附件计数和笔记里的本地附件对不上，请核对后再补全。")
        start, end = counts[0].span(1)
        offset = header.start(1)
        text = text[:offset + start] + str(len(known_links) + len(new_links)) + text[offset + end:]
        heading = "## 补充附件" if known_links else "## 本地附件"
        additions.extend([heading, "", *(f"![[{link.as_posix()}]]" for link in new_links), ""])
    if additions:
        text += newline * 2 + newline.join(additions) + newline
    return text, new_links


class MediaBudget:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0
        self.counted = set()

    def check(self, sizes):
        if self.limit and self.used + sum(size for path, size in sizes.items() if path not in self.counted) > self.limit:
            raise InputError("该条附件会超出本次总量上限。")

    def add(self, sizes):
        if self.limit:
            self.used += sum(size for path, size in sizes.items() if path not in self.counted)
            self.counted.update(sizes)


def import_record(record, vault, folder, note, stored_id, args, created_files, budget):
    dry_run, enrich = args.dry_run, args.enrich
    media_id = stored_id or record.identity
    original = None
    if note.exists():
        if not note.is_file():
            raise InputError("笔记输出路径已被同名目录占用。")
        if not enrich or not (record.body or record.media):
            return "existing", note.relative_to(vault), 0
        original = note.read_bytes()
    elif enrich:
        raise ConflictError("找不到原笔记，请核对目标目录和首次归档的链接。")
    attachments = {}
    sizes = {}
    for path in record.media:
        size = path.stat().st_size
        if size == 0 or (args.max_file_bytes and size > args.max_file_bytes):
            raise InputError("附件为空或超过单文件上限。")
        if args.max_total_bytes and size > args.max_total_bytes:
            raise InputError("附件超过本次总量上限。")
        digest = sha256_file(path, max_bytes=size)
        parts = (*folder, "媒体", media_id, f"{digest}{path.suffix.lower()}")
        dest = checked_path(vault, parts)
        if dest.exists() and (not dest.is_file() or sha256_file(dest) != digest):
            raise InputError("目标附件路径冲突或文件内容不一致。")
        attachments.setdefault(dest, (path, digest))
        sizes.setdefault(dest, size)
    budget.check(sizes)
    links = [path.relative_to(vault) for path in attachments]
    if original is not None:
        relative_note = note.relative_to(vault)
        content, new_links = enrich_markdown(original, record, links, Path(*folder, "媒体", media_id), media_id)
        changed = content.encode("utf-8") != original
        missing = {dest: info for dest, info in attachments.items() if not dest.exists()}
        if not changed and not missing:
            return "existing", relative_note, 0
        count = len(set(new_links) | {dest.relative_to(vault) for dest in missing})
        if dry_run:
            budget.add(sizes)
            return "planned_update", relative_note, count
        with enrichment_lock(note):
            if note.is_symlink() or not note.is_file() or note.read_bytes() != original:
                raise ConflictError("笔记在导入期间发生变化，请重新预览。")
            for dest, (path, digest) in missing.items():
                if publish_file(dest, source=path, expected_digest=digest, expected_size=sizes[dest]):
                    created_files[dest.relative_to(vault).as_posix()] = digest
                    budget.add({dest: sizes[dest]})
            if changed:
                publish_file(note, content=content, expected_note=original)
        budget.add(sizes)
        return "updated", relative_note, count
    if dry_run:
        budget.add(sizes)
        return "planned", note.relative_to(vault), len(attachments)
    for dest, (path, digest) in attachments.items():
        if not dest.exists():
            if publish_file(dest, source=path, expected_digest=digest, expected_size=sizes[dest]):
                created_files[dest.relative_to(vault).as_posix()] = digest
                budget.add({dest: sizes[dest]})
    content = note_markdown(record, links)
    created = publish_file(note, content=content)
    if created:
        created_files[note.relative_to(vault).as_posix()] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    budget.add(sizes)
    return ("created" if created else "existing"), note.relative_to(vault), (len(links) if created else 0)


def read_link_map(path):
    if path is None:
        return {}, {}
    data = json.loads(read_text(path))
    if not isinstance(data, dict):
        raise InputError("短链映射必须是 JSON 对象。")
    mapping, reverse = {}, {}
    for url, note_id in data.items():
        short_id, _ = canonical_url(url)
        if not short_id.startswith("share-") or not isinstance(note_id, str) or not NOTE_ID.fullmatch(note_id):
            raise InputError("映射键须为小红书分享短链，值须为 24 位原帖 ID。")
        note_id = note_id.lower()
        if short_id in mapping and mapping[short_id] != note_id:
            raise InputError("同一短链被映射到多个原帖 ID。")
        mapping[short_id] = note_id
        reverse.setdefault(note_id, set()).add(short_id)
    return mapping, reverse


def title_filename(title):
    title = unicodedata.normalize("NFC", clean_text(title))
    title = re.sub(r'[\\/:*?"<>|#\[\]^%]', " ", title)
    title = " ".join(title.split()).strip(". ")
    title = title.encode("utf-8")[:120].decode("utf-8", errors="ignore").rstrip(". ")
    return title or "小红书收藏"


def choose_note(record, vault, folder, notes, aliases, claimed, style):
    found = [
        (path, identity) for identity in (record.identity, *sorted(aliases))
        for path in notes.get(identity, [])
    ]
    if len(found) > 1:
        raise ConflictError("同一来源对应多篇现有笔记，请先核对原帖 ID 和短链映射。")
    if found:
        path, stored_id = found[0]
        return checked_path(vault, path.relative_to(vault).parts), stored_id
    stem = record.identity
    if style == "title":
        stem = f"{title_filename(record.title)} - {record.identity[-6:]}"
    for filename in dict.fromkeys((stem, f"{title_filename(record.title)} - {record.identity}")):
        path = checked_path(vault, (*folder, "笔记", filename + ".md"))
        name_key = unicodedata.normalize("NFC", path.name).casefold()
        if not path.exists() and name_key not in claimed:
            claimed.add(name_key)
            return path, None
        if style == "id":
            break
    raise ConflictError("目标笔记文件名已被其他内容占用。")


def refresh_index(vault, folder, created_files=None, *, dry_run=False, excluded=()):
    path = checked_path(vault, (*folder, "索引.md"))
    lines = ["# 小红书收藏索引", ""]
    directory = checked_path(vault, (*folder, "笔记"))
    for paths in scan_notes(directory).values():
        for note in paths:
            if note in excluded:
                continue
            fields = note_fields(note)
            label = re.sub(r"[|\[\]\n\r]+", " ", fields.get("title") or note.stem)
            target = note.relative_to(vault).with_suffix("").as_posix()
            if any(char in target for char in "|[]#\n\r"):
                continue
            lines.append(f"- [[{target}|{label}]]")
    payload = "\n".join(lines) + "\n"
    return refresh_managed(vault, path, payload, INDEX_HEADER, "<!-- rednote:index {} -->",
                           "索引", created_files, dry_run)


def refresh_base(vault, folder, created_files):
    path = checked_path(vault, (*folder, "收藏.base"))
    expr = 'file.inFolder(' + json.dumps("/".join((*folder, "笔记")), ensure_ascii=False) + ')'
    lines = ["filters:", "  and:", "    - " + json.dumps(expr, ensure_ascii=False),
             "    - 'file.ext == \"md\"'", "views:"]
    for name in ("全部", "按收藏夹", "未读"):
        lines.extend(["  - type: table", f"    name: {name}",
                      "    order: [file.name, note.author, note.collection, note.status, note.savedAt]",
                      "    sort:", "      - property: note.savedAt", "        direction: DESC"])
        if name == "按收藏夹":
            lines.extend(["    groupBy:", "      property: note.collection", "      direction: ASC"])
        if name == "未读":
            lines.extend(["    filters:", "      and:", "        - 'note.status == \"未读\"'"])
    payload = "\n".join(lines) + "\n"
    return refresh_managed(vault, path, payload, BASE_HEADER, "# rednote:base {}",
                           "收藏视图", created_files, False)


def refresh_managed(vault, path, payload, pattern, label, name, created_files, dry_run):
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    content = label.format(digest) + "\n" + payload
    if path.exists():
        old = path.read_bytes()
        previous = old.decode("utf-8")
        mark = pattern.match(previous)
        if not mark or hashlib.sha256(previous[mark.end():].encode("utf-8")).hexdigest() != mark[1]:
            raise ConflictError(f"{name}已有手写内容，请核对后再更新。")
        if previous == content:
            return False
        if not dry_run:
            publish_file(path, content=content, expected_note=old)
        return True
    if not dry_run and publish_file(path, content=content) and created_files is not None:
        created_files[path.relative_to(vault).as_posix()] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return True


def print_report(report, args):
    serialized = report_text(report)
    if args.output == "json":
        print(serialized, end="")
    else:
        print("状态\t标题或文件\t附件写入/跳过\t正文字数\t说明")
        for item in report["records"]:
            label = item.get("title") or item.get("note") or item.get("path") or str(item.get("input", ""))
            files = f"{item.get('media_to_write', 0)}/{item.get('media_skipped', 0)}"
            print(item["status"], label, files, item.get("body_chars", 0), item.get("reason", ""), sep="\t")
        print("  ".join(f"{key}={value}" for key, value in report.items() if type(value) is int))
        for warning in report["warnings"]:
            if args.dry_run or args.json_errors or report["mode"] != "import" or not warning.startswith("目标目录没有 .obsidian"):
                print(warning, file=sys.stderr)
    if args.report_file and not publish_file(args.report_file, content=serialized):
        raise InputError("报告路径已存在。请另选一个文件名。")


def automatic_report(args, vault, folder, report):
    if args.report_file:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")
    path = checked_path(vault, (*folder, ".导入记录", stamp + ".json"))
    args.report_file = path
    report["report_path"] = path.relative_to(vault).as_posix()


def initialize_inbox(args, vault, folder, report):
    path = args.source
    relative = path.relative_to(vault).as_posix()
    report.update(index=False, created_files=[])
    if path.is_file():
        status = "existing"
    elif path.exists():
        raise InputError("收件箱路径已被占用。")
    elif args.dry_run:
        status = "planned"
    elif publish_file(path, content=INTRO):
        status = "created"
        report["created_files"].append({"path": relative, "sha256": hashlib.sha256(INTRO.encode()).hexdigest()})
        save_receipt(report, vault, folder)
        automatic_report(args, vault, folder, report)
    else:
        status = "existing"
    report["records"].append({"note": relative, "status": status})
    report[status] = 1
    print_report(report, args)
    return 0


def run(args):
    if not args.vault.is_dir():
        raise InputError("目标 Vault 路径不存在或不是文件夹。")
    vault = args.vault.resolve(strict=True)
    report = {"mode": "preview" if args.dry_run else "import", "warnings": [], "records": []}
    config = vault / ".obsidian"
    warn_vault = config.is_symlink() or not config.is_dir()
    if warn_vault:
        warning = "目标目录没有 .obsidian，请确认它是预期的 Vault。"
        report["warnings"].append(warning)
    if args.report_file:
        args.report_file = args.report_file.absolute()
        if args.report_file.exists() or args.report_file.is_symlink():
            raise InputError("报告路径已存在。请另选一个文件名。")
    if args.undo:
        status = undo_import(args, vault, report, refresh_index)
        print_report(report, args)
        return status
    if args.annotate:
        folder = relative_parts(args.folder)
        changes, status = annotate(args, vault, folder, report)
        if changes:
            save_receipt(report, vault, folder, changes)
            automatic_report(args, vault, folder, report)
        print_report(report, args)
        return status
    inbox = None
    if args.init_inbox and args.format != "inbox":
        raise InputError("--init-inbox 需要 --format inbox。")
    if args.format == "inbox":
        folder = relative_parts(args.folder)
        if args.source is not None:
            raise InputError("收件箱模式使用目标目录中的收件箱.md。")
        args.source = checked_path(vault, (*folder, "收件箱.md"))
        if args.init_inbox:
            return initialize_inbox(args, vault, folder, report)
        inbox = Inbox(args.source)
    elif not args.format or args.source is None:
        raise InputError("导入需要 --format 和 --source。")

    args.source = Path(os.path.abspath(args.source))
    if args.legacy_vault:
        supplied_root = Path(os.path.abspath(args.legacy_vault))
        args.legacy_vault = supplied_root.resolve(strict=True)
        if args.source.is_relative_to(supplied_root):
            args.source = args.legacy_vault / args.source.relative_to(supplied_root)
    folder = relative_parts(args.folder)
    if args.source.is_symlink() or not args.source.exists():
        raise InputError("请提供存在的输入文件或旧笔记目录。")
    if args.format != "legacy" and not args.source.is_file():
        raise InputError("该输入格式需要一个文件。")
    if args.format != "shares" and (args.attach or args.body_file):
        raise InputError("--attach 和 --body-file 需要 --format shares。")
    output = checked_path(vault, folder)
    if args.format == "legacy" and args.source.is_dir() and output.is_relative_to(args.source):
        raise InputError("旧笔记源目录包含输出目录。请缩小输入范围或换一个目标 Vault。")
    mapping, reverse = read_link_map(args.link_map)
    manage_index = args.index
    notes_dir = output / "笔记"
    notes = scan_notes(notes_dir) if notes_dir.is_dir() and not notes_dir.is_symlink() else {}
    report.update({
        "created": 0, "planned": 0, "existing": 0, "updated": 0, "planned_update": 0,
        "conflict": 0, "unassigned_media": [], "unassigned_body": False, "duplicate": 0, "failed": 0,
        "local_media": 0, "remote_media_skipped": 0, "unsupported_media_skipped": 0,
        "index": manage_index, "created_files": [], "link_only": 0,
    })
    seen, claimed = {}, set()
    created_files, budget = {}, MediaBudget(args.max_total_bytes)
    reasons = {
        "created": "已创建笔记", "planned": "将创建笔记", "existing": "同一篇已存在且没有可补全的内容",
        "updated": "已补全原笔记", "planned_update": "将补全原笔记",
        "duplicate": "同一来源已在本批处理",
    }
    inputs = share_lines(inbox.lines, args.collection) if inbox else records(args, report)
    for number, record in inputs:
        item = {"input": number, "title": "", "attachments": 0, "body_chars": 0,
                "media_to_write": 0, "media_skipped": 0}
        count = 0
        try:
            if isinstance(record, Exception):
                raise record
            item.update(title=" ".join(clean_text(record.title).split()),
                        author=" ".join(clean_text(record.author).split()),
                        attachments=len(record.media), body_chars=len(record.body))
            if inbox:
                item["input_start"] = record.input_start or number
            record.keep_hashtags = args.keep_hashtags
            if record.identity in mapping:
                record.identity, record.url = canonical_url(
                    f"https://www.xiaohongshu.com/explore/{mapping[record.identity]}"
                )
            if record.identity in seen:
                status = "duplicate"
                item["note"] = seen[record.identity]
            else:
                note, stored_id = choose_note(
                    record, vault, folder, notes, reverse.get(record.identity, ()), claimed, args.filename_style
                )
                item["note"] = note.relative_to(vault).as_posix()
                if warn_vault and not args.dry_run and not args.json_errors:
                    print(report["warnings"][0], file=sys.stderr)
                    warn_vault = False
                status, path, count = import_record(record, vault, folder, note, stored_id, args, created_files, budget)
                seen[record.identity] = item["note"]
                if status in {"created", "planned"}:
                    if not record.body and not record.media:
                        report["link_only"] += 1
                    report["remote_media_skipped"] += record.remote_media
                    report["unsupported_media_skipped"] += record.unsupported_media
                    notes.setdefault(record.identity, []).append(vault / path)
            item["reason"] = reasons[status]
        except (InputError, OSError, UnicodeError) as error:
            status = "conflict" if isinstance(error, ConflictError) else "failed"
            item["reason"] = user_error(error)
        item["media_to_write"] = count
        item["media_skipped"] = max(0, item["attachments"] - count)
        report["local_media"] += count
        report[status] += 1
        item["status"] = status
        report["records"].append(item)
        if args.progress and len(report["records"]) % 100 == 0:
            print(f"已处理 {len(report['records'])} 条", file=sys.stderr)
    if not report["records"] and not inbox:
        raise InputError("没有可导入的收藏。请检查输入格式与链接，或选择实际的旧笔记目录。")
    if args.progress:
        print(f"处理完成，共 {len(report['records'])} 条", file=sys.stderr)
    index_error = index_changed = False
    if manage_index and not args.dry_run and notes and report["records"]:
        try:
            index_changed = refresh_index(vault, folder, created_files)
            index_changed = refresh_base(vault, folder, created_files) or index_changed
        except (InputError, OSError, UnicodeError) as error:
            index_error = True
            report["warnings"].append(user_error(error))
    changes = []
    inbox_error = False
    if inbox and report["records"]:
        try:
            count, descriptor, patch = inbox.update(report["records"], vault, args.dry_run, notes_dir / "pending.md")
            report["inbox_would_move" if args.dry_run else "inbox_moved"] = count
            if descriptor:
                report["modified_files"] = [descriptor]
                changes.append(patch)
        except (InputError, OSError, UnicodeError) as error:
            inbox_error = True
            report["warnings"].append(user_error(error))
    report["created_files"] = [{"path": path, "sha256": digest} for path, digest in created_files.items()]
    receipt_error = False
    if report["created_files"] or changes:
        try:
            save_receipt(report, vault, folder, changes)
        except (InputError, OSError, UnicodeError) as error:
            receipt_error = True
            report["warnings"].append("无法保存撤销记录。" + user_error(error))
    if not args.dry_run and (report["created_files"] or report["updated"] or index_changed or changes):
        automatic_report(args, vault, folder, report)
    print_report(report, args)
    return 1 if index_error or receipt_error or inbox_error or any(
        report[key] for key in ("failed", "conflict", "unassigned_media", "unassigned_body")
    ) else 0


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        if "--json-errors" in sys.argv[1:]:
            self.exit(2, json.dumps({"error": message, "code": "arguments"}, ensure_ascii=False) + "\n")
        super().error(message)


def skill_version():
    skill = Path(__file__).resolve().parents[1] / "SKILL.md"
    header = read_text(skill).split("---", 2)[1]
    match = re.search(r'^[ \t]+version:[ \t]*["\']?([0-9]+\.[0-9]+\.[0-9]+)', header, re.M)
    if not match:
        raise InputError("SKILL.md 缺少版本字段。")
    return match[1]


def nonnegative(value):
    try:
        number = int(value)
    except ValueError:
        number = -1
    if number < 0:
        raise argparse.ArgumentTypeError("请输入大于或等于 0 的字节数。")
    return number


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=skill_version())
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--format", choices=["shares", "json", "legacy", "inbox"])
    parser.add_argument("--init-inbox", action="store_true", help="经用户同意后创建空收件箱")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--legacy-vault", type=Path)
    parser.add_argument("--folder", default="小红书收藏", help="目标 Vault 内的相对目录")
    parser.add_argument("--collection", default="", help="默认收藏夹名")
    parser.add_argument("--attach", type=Path, action="append", default=[], help="单篇分享的本地附件，可重复")
    parser.add_argument("--body-file", type=Path, help="单篇分享的 UTF-8 正文")
    parser.add_argument("--enrich", action="store_true", help="给已归档的同一篇笔记补上缺失正文和附件")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-hashtags", action="store_true", help="保留正文里的 Obsidian 标签语法")
    mode.add_argument("--undo", type=Path, help="依据导入 JSON 报告撤销本次新建文件和受控修改")
    mode.add_argument("--annotate", type=Path, help="应用用户 agent 提供的本地 annotations.json")
    parser.add_argument("--report-file", type=Path, help="将 JSON 报告保存为新文件")
    parser.add_argument("--output", choices=["json", "table"], default="json")
    parser.add_argument("--filename-style", choices=["id", "title"], default="title")
    parser.add_argument("--index", dest="index", action="store_true", default=True, help="维护静态索引.md")
    parser.add_argument("--no-index", dest="index", action="store_false", help="跳过索引")
    parser.add_argument("--link-map", type=Path, help="用户确认的短链与原帖 ID 对照表")
    parser.add_argument("--json-errors", action="store_true", help="将命令行参数错误也输出为 JSON")
    parser.add_argument("--progress", action="store_true", help="在标准错误输出中显示处理进度")
    parser.add_argument("--max-file-bytes", type=nonnegative, default=0)
    parser.add_argument("--max-total-bytes", type=nonnegative, default=0)
    args = parser.parse_args()
    try:
        return run(args)
    except (InputError, OSError, UnicodeError, json.JSONDecodeError) as error:
        payload = {"error": user_error(error)}
        if args.json_errors:
            payload["code"] = "input"
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
