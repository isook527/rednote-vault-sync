"""Move successfully archived local inbox entries into their history."""

import re
from collections import defaultdict
from datetime import date

from rednote_changes import describe_change
from rednote_notes import clean_text
from rednote_files import ConflictError, InputError, enrichment_lock, publish_file

ERROR_PREFIX = "<!-- rednote:inbox:error "
HEADING = re.compile(r"^## 已处理[ \t]*\r?$", re.M)
INTRO = "请每行粘贴一条小红书分享文案。\n"


class Inbox:
    def __init__(self, path):
        self.path = path
        if not path.is_file():
            raise InputError("收件箱不存在。确认创建后运行 --format inbox --init-inbox。")
        self.raw = path.read_bytes()
        self.original = self.raw.decode("utf-8")
        self.bom = "\ufeff" if self.original.startswith("\ufeff") else ""
        content = self.original.removeprefix("\ufeff")
        heading = HEADING.search(content)
        self.lines = (content[:heading.start()] if heading else content).splitlines(keepends=True)
        self.history = content[heading.start():] if heading else ""
        self.newline = "\r\n" if "\r\n" in content else "\n"

    def plan(self, records):
        grouped = defaultdict(list)
        for item in records:
            grouped[item["input"]].append(item)
        moved = {}
        reasons = {}
        for number, items in grouped.items():
            if any(item["status"] in {"failed", "conflict"} for item in items):
                reasons[number] = next(item["reason"] for item in items if item["status"] in {"failed", "conflict"})
            else:
                links = []
                for item in items:
                    path = item.get("note")
                    if not path:
                        break
                    link = "[[" + path.removesuffix(".md") + "]]"
                    if link not in links:
                        links.append(link)
                else:
                    start = min(item.get("input_start", number) for item in items)
                    moved[number] = start, links
        removed = {line for end, (start, _) in moved.items() for line in range(start, end + 1)}
        pending = []
        for number, line in enumerate(self.lines, 1):
            if number in removed or line.lstrip().startswith(ERROR_PREFIX):
                continue
            pending.append(line)
            if number in reasons:
                message = " ".join(reasons[number].split()).replace("--", "—")
                if not line.endswith(("\n", "\r")):
                    pending.append(self.newline)
                pending.append(f"{ERROR_PREFIX}{message} -->{self.newline}")
        history = self.history
        if moved:
            if not history:
                history = f"## 已处理{self.newline}"
            for end, (start, links) in moved.items():
                if not history.endswith(("\n", "\r")):
                    history += self.newline
                original = " ".join(line.strip() for line in self.lines[start - 1:end])
                original = clean_text(original).replace("<", "&lt;")
                history += f"- {date.today().isoformat()} {' '.join(links)} · {original}{self.newline}"
        pending_text = "".join(pending)
        if history and pending_text and not pending_text.endswith(("\n", "\r")):
            pending_text += self.newline
        return self.bom + pending_text + history, len(moved)

    def update(self, records, vault, dry_run, lock_path):
        changed, count = self.plan(records)
        if dry_run or changed == self.original:
            return count, None, None
        with enrichment_lock(lock_path):
            if self.path.is_symlink() or not self.path.is_file() or self.path.read_bytes() != self.raw:
                raise ConflictError("收件箱在导入期间发生变化。已保存的笔记可在下次运行时识别。")
            publish_file(self.path, content=changed, expected_note=self.raw)
        patches = [[self.original, changed]]
        descriptor = describe_change(self.path.relative_to(vault), "inbox", self.original, changed, patches)
        return count, descriptor, patches
