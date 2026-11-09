"""Apply a local agent's structured annotations to bounded Markdown sections."""

import json
import re
from pathlib import Path

from rednote_changes import apply_patches, describe_change, digest
from rednote_files import (
    ConflictError, InputError, checked_path, enrichment_lock, publish_file,
    read_text, relative_parts, text_field, user_error, valid_source_id,
)
from rednote_notes import clean_text, legacy_frontmatter, safe_markdown, scan_notes

AI_START = "<!-- rednote:ai:start -->"
AI_END = "<!-- rednote:ai:end -->"
AI_BLOCK = re.compile(
    re.escape(AI_START) + r"\r?\n<!-- rednote:ai:sha256 ([0-9a-f]{64}) -->\r?\n(.*?)" + re.escape(AI_END),
    re.S,
)


def strings(item, key):
    values = item.get(key, [])
    if not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values):
        raise InputError(f"{key} 必须是由非空字符串组成的数组。")
    return list(dict.fromkeys(x.strip() for x in values))


def annotation_text(item, vault, note):
    summary = text_field(item, "summary")
    points = strings(item, "points")
    transcript = text_field(item, "transcript")
    sources = strings(item, "transcript_source")
    if not summary or len(points) > 3:
        raise InputError("每篇整理须有摘要，最多三个要点。")
    if transcript and not sources:
        raise InputError("图片转写须标明来源图片。")
    if not transcript and sources:
        raise InputError("transcript_source 需与图片转写一起提供。")
    sections = ["## 整理", "", safe_markdown(summary)]
    if points:
        sections.extend(["", *(f"- {safe_markdown(point)}" for point in points)])
    if transcript:
        sections.extend(["", "### 图片转写（AI）", "", "来源图片 · " + "、".join(safe_markdown(x) for x in sources),
                         "", safe_markdown(transcript)])
    related = strings(item, "related")
    if len(related) > 3:
        raise InputError("每篇整理最多关联三篇笔记。")
    links = []
    for target in related:
        path = checked_path(vault, relative_parts(target if target.endswith(".md") else target + ".md"))
        if not path.is_file() or path == note:
            raise InputError("关联笔记须是 Vault 中另一篇真实存在的 Markdown 笔记。")
        rel = path.relative_to(vault).with_suffix("").as_posix()
        if "#" in rel:
            raise InputError("关联路径不能包含 #。")
        if rel not in links:
            links.append(rel)
    if links:
        sections.extend(["", "### 相关笔记", "", *(f"- [[{link}]]" for link in links)])
    return summary, sections


def make_patches(text, item, sections):
    newline = "\r\n" if "\r\n" in text else "\n"
    block_text = newline.join(sections) + newline
    block = newline.join([AI_START, f"<!-- rednote:ai:sha256 {digest(block_text)} -->", block_text + AI_END])
    patches = []
    if AI_START in text or AI_END in text:
        matches = list(AI_BLOCK.finditer(text))
        if len(matches) != 1 or text.count(AI_START) != 1 or text.count(AI_END) != 1 or digest(matches[0][2]) != matches[0][1]:
            raise ConflictError("整理区块已被手动修改，请先核对。")
        old = matches[0][0]
        if old != block:
            patches.append([old, block])
    else:
        patches.append(["", newline * 2 + block + newline])

    tags = strings(item, "tags")
    fields, _ = legacy_frontmatter(text.removeprefix("\ufeff").replace("\r\n", "\n"))
    old_tags = fields.get("tags", [])
    new_tags = list(dict.fromkeys([*old_tags, *(
        tag for tag in tags if re.fullmatch(r"[\w/-]+", tag) and not tag.isdigit()
    )]))
    if new_tags != old_tags:
        header = re.match(r"\A\ufeff?---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.S)
        old = re.search(r"^tags:[^\r\n]*(?:\r?\n[ \t]*-[ \t]+[^\r\n]+)*", header[1], re.M) if header else None
        if not old:
            raise ConflictError("找不到可安全更新的 tags 属性。")
        patches.append([old[0], "tags: " + json.dumps(new_tags, ensure_ascii=False)])
    return patches, new_tags


def annotate(args, vault, folder, report):
    source = args.annotate
    if source.is_symlink() or not source.is_file():
        raise InputError("请提供存在的 annotations.json。")
    data = json.loads(read_text(source))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("items"), list):
        raise InputError("annotations.json 需要 version: 1 和 items 数组。")
    directory = checked_path(vault, (*folder, "笔记"))
    notes = scan_notes(directory)
    changes = []
    report.update({
        "mode": "annotate_preview" if args.dry_run else "annotate",
        "updated": 0, "planned_update": 0, "existing": 0, "failed": 0, "conflict": 0,
        "index": False, "created_files": [], "modified_files": [],
    })
    seen = set()
    for number, item in enumerate(data["items"], 1):
        entry = {"input": number}
        try:
            if not isinstance(item, dict):
                raise InputError("整理项必须是 JSON 对象。")
            identity = text_field(item, "id").lower()
            if not valid_source_id(identity) or identity in seen:
                raise InputError("整理项的 ID 无效或重复。")
            seen.add(identity)
            targets = notes.get(identity, [])
            if len(targets) != 1:
                raise ConflictError("未找到唯一的目标收藏笔记。")
            note = targets[0]
            relative = note.relative_to(vault)
            entry["note"] = relative.as_posix()
            raw = note.read_bytes()
            original = raw.decode("utf-8")
            summary, sections = annotation_text(item, vault, note)
            patches, tags = make_patches(original, item, sections)
            entry.update(summary=clean_text(summary), tags=tags)
            if not patches:
                status = "existing"
            else:
                updated = apply_patches(original, patches)
                descriptor = describe_change(relative, "ai", original, updated, patches)
                status = "planned_update" if args.dry_run else "updated"
                if not args.dry_run:
                    with enrichment_lock(note):
                        publish_file(note, content=updated, expected_note=raw)
                    report["modified_files"].append(descriptor)
                    changes.append(patches)
        except (InputError, UnicodeError, OSError) as error:
            status = "conflict" if isinstance(error, ConflictError) else "failed"
            entry["reason"] = user_error(error)
        entry["status"] = status
        report[status] += 1
        report["records"].append(entry)
    return changes, 1 if report["failed"] or report["conflict"] else 0
