"""Regression tests for the 1.3.1 fixes."""

import re

from test_v13_workflows import BASE, FIRST, ROOT, SECOND, command, data, save, vault  # noqa: F401

THIRD = "000000000000000000000103"


def make_notes(vault, tmp_path, items):
    report = command(vault, "--format", "json", "--source", data(tmp_path / "manifest.json", items))
    return [vault / record["note"] for record in report["records"]]


def test_inbox_failure_reason_is_a_visible_callout_and_clears_on_retry(vault):
    path = vault / "小红书收藏/收件箱.md"
    command(vault, "--format", "inbox", "--init-inbox")
    bad = "坏链接 https://example.com/not-rednote\n"
    follow = f"{BASE}{SECOND}\n"
    save(path, "说明\n" + bad + follow)
    command(vault, "--format", "inbox", code=1)
    text = path.read_text(encoding="utf-8")
    pending = text.split("## 已处理")[0]
    assert "<!--" not in pending
    assert re.search(r"^坏链接 .*\n> \[!warning\] 未导入：链接格式不受支持.*\n\n", pending, re.M)
    note = next((vault / "小红书收藏/笔记").glob(f"*{SECOND[-6:]}.md"))
    assert "未导入" not in note.read_text(encoding="utf-8")

    # A link-only line written right under the warning must not borrow it as a title.
    lines = pending.splitlines(keepends=True)
    insert = next(i for i, line in enumerate(lines) if line.startswith("> [!warning]")) + 1
    lines.insert(insert, f"{BASE}{THIRD}\n")
    save(path, "".join(lines) + "## 已处理" + text.split("## 已处理")[1])
    command(vault, "--format", "inbox", code=1)
    third = next((vault / "小红书收藏/笔记").glob(f"*{THIRD[-6:]}.md"))
    assert "未导入" not in third.read_text(encoding="utf-8")

    # Fixing the line removes the old warning and leaves no stray blank lines behind.
    fixed = path.read_text(encoding="utf-8").replace(bad, f"修好了 {BASE}{FIRST}\n")
    save(path, fixed)
    command(vault, "--format", "inbox")
    pending = path.read_text(encoding="utf-8").split("## 已处理")[0]
    assert "未导入" not in pending and pending.strip() == "说明"


def test_inbox_clears_legacy_comment_warnings(vault):
    path = vault / "小红书收藏/收件箱.md"
    command(vault, "--format", "inbox", "--init-inbox")
    save(path, f"旧 https://example.com/x\n<!-- rednote:inbox:error 旧原因 -->\n")
    command(vault, "--format", "inbox", code=1)
    text = path.read_text(encoding="utf-8")
    assert "<!-- rednote:inbox:error" not in text and text.count("> [!warning] 未导入：") == 1


def test_annotation_block_goes_before_my_notes(vault, tmp_path):
    note, = make_notes(vault, tmp_path, [{"id": FIRST, "title": "布光", "body": "正文"}])
    original = note.read_text(encoding="utf-8")
    first = command(vault, "--annotate", data(tmp_path / "a.json", [{"id": FIRST, "summary": "摘要"}]))
    text = note.read_text(encoding="utf-8")
    assert text.index("<!-- rednote:ai:end -->") < text.index("## 我的笔记")
    assert text.rstrip().endswith("## 我的笔记")
    save(note, text + "我的批注。\n")
    command(vault, "--annotate", data(tmp_path / "a.json", [{"id": FIRST, "summary": "新摘要"}]))
    text = note.read_text(encoding="utf-8")
    assert text.index("新摘要") < text.index("## 我的笔记") < text.index("我的批注。")
    assert text.endswith("我的批注。\n")
    # Undo the second write, then the first: only the managed block goes away.
    reports = sorted((vault / "小红书收藏/.导入记录").glob("*.json"))
    command(vault, "--undo", reports[-1])
    command(vault, "--undo", vault / first["report_path"])
    assert note.read_text(encoding="utf-8") == original + "我的批注。\n"


def test_annotation_without_my_notes_heading_still_appends(vault, tmp_path):
    note, = make_notes(vault, tmp_path, [{"id": FIRST, "title": "布光"}])
    save(note, note.read_text(encoding="utf-8").replace("## 我的笔记\n", ""))
    command(vault, "--annotate", data(tmp_path / "a.json", [{"id": FIRST, "summary": "摘要"}]))
    assert note.read_text(encoding="utf-8").rstrip().endswith("<!-- rednote:ai:end -->")


def test_annotation_skips_invalid_related_and_tags_with_warnings(vault, tmp_path):
    note, other = make_notes(vault, tmp_path, [{"id": FIRST, "title": "布光"}, {"id": SECOND, "title": "补光"}])
    related = other.relative_to(vault).with_suffix("").as_posix()
    source = data(tmp_path / "a.json", [{
        "id": FIRST, "summary": "摘要", "tags": ["灯光", "bad tag!"],
        "related": ["小红书收藏/笔记/不存在", related, note.relative_to(vault).with_suffix("").as_posix()],
    }])
    preview = command(vault, "--annotate", source, "--dry-run")
    record = preview["records"][0]
    assert record["status"] == "planned_update" and len(record["warnings"]) == 3
    assert any("不存在" in w for w in record["warnings"]) and any("bad tag!" in w for w in record["warnings"])
    report = command(vault, "--annotate", source)
    assert report["updated"] == 1 and report["failed"] == 0
    text = note.read_text(encoding="utf-8")
    assert f"[[{related}]]" in text and "不存在" not in text
    assert '"灯光"' in text and "bad tag" not in text


def test_query_ignores_related_links_and_returns_plain_snippets(vault, tmp_path):
    note, other = make_notes(vault, tmp_path, [
        {"id": FIRST, "title": "高考词汇", "body": "**重点**词表"},
        {"id": SECOND, "title": "雅思口语"},
    ])
    related = other.relative_to(vault).with_suffix("").as_posix()
    command(vault, "--annotate", data(tmp_path / "a.json", [
        {"id": FIRST, "summary": "一张图记词", "points": ["分组"], "related": [related]},
    ]))
    save(note, note.read_text(encoding="utf-8") + "\n> 参考 [[别处/页面|我的页面]]\n")

    def ask(text):
        return command(vault, "--text", text, script="query_rednote.py")

    assert ask("高考 雅思")["count"] == 0
    assert ask("整理")["count"] == 0 and ask("我的笔记")["count"] == 0
    hit = ask("我的页面")
    assert hit["count"] == 1
    snippet = ask("重点")["matches"][0]["snippet"]
    assert "重点词表" in snippet
    for mark in ("**", "[[", "###", "](", "<!--", "打开小红书原帖"):
        assert mark not in snippet + hit["matches"][0]["snippet"]


def test_docs_point_to_codex_skills_page():
    for name in ("README.md", "SOURCES.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "developers.openai.com/api/reference/cli" not in text
        assert "learn.chatgpt.com/docs/build-skills" in text
    assert "~/.agents/skills/" in (ROOT / "README.md").read_text(encoding="utf-8")
