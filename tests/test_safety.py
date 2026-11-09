import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills/rednote-vault-sync/scripts/import_rednote.py"
PNG = ROOT / "examples/media/reading-corner.png"
BASE = "https://www.xiaohongshu.com/explore/"
FIRST = "000000000000000000000101"
SECOND = "000000000000000000000102"
SHORT = "https://xhslink.com/a/KornviaDemo"


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "Vault"
    root.mkdir()
    return root


def cli(vault, *flags, status=0, output="json"):
    process = subprocess.run(
        [sys.executable, "-B", str(CLI), "--vault", str(vault), *map(str, flags)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert process.returncode == status, (process.stdout, process.stderr)
    if status == 2:
        return json.loads(process.stderr)
    return json.loads(process.stdout) if output == "json" else process


def share(tmp_path, title="阅读角", link=BASE + FIRST):
    path = tmp_path / "share.txt"
    path.write_text(f"{title} {link}\n", encoding="utf-8")
    return ["--format", "shares", "--source", path]


def imported_note(vault, report):
    return vault / report["records"][0]["note"]


def test_markdown_keeps_plain_punctuation_and_neutralizes_active_tokens(vault, tmp_path):
    source = share(tmp_path)
    body = tmp_path / "body.txt"
    body.write_text(
        "a & b <c> [d] #tag\n\n<script>alert(1)</script>\n"
        "<!-- rednote:body:end -->\n![[private.png]]\n![remote](https://example.invalid/img)\n"
        "[active](javascript:alert(1))\n<c onclick='alert(1)'>", encoding="utf-8",
    )
    flags = ["--body-file", body]
    report = cli(vault, *source, *flags)
    note = imported_note(vault, report).read_text(encoding="utf-8")
    assert "a & b <c> [d] &#35;tag" in note
    assert "<script>" not in note and "<c onclick" not in note
    assert "![[private.png]]" not in note and "![remote](" not in note
    assert "[active](javascript:" not in note
    assert note.count("<!-- rednote:body:end -->") == 1
    other = cli(vault, *source, *flags, "--keep-hashtags", "--folder", "保留标签")
    assert "a & b <c> [d] #tag" in imported_note(vault, other).read_text(encoding="utf-8")


def test_preview_report_and_vault_warning(vault, tmp_path):
    path = tmp_path / "预览.json"
    body = tmp_path / "body.txt"
    body.write_text("两字", encoding="utf-8")
    result = cli(vault, *share(tmp_path), "--body-file", body, "--attach", PNG,
                 "--dry-run", "--report-file", path, "--output", "table", "--progress", output="table")
    report = json.loads(path.read_text(encoding="utf-8"))
    entry = report["records"][0]
    assert (entry["title"], entry["attachments"], entry["body_chars"], entry["media_to_write"]) == ("阅读角", 1, 2, 1)
    assert entry["reason"] and ".obsidian" in report["warnings"][0]
    assert "阅读角" in result.stdout and "处理完成，共 1 条" in result.stderr
    assert not list(vault.iterdir())
    (vault / ".obsidian").mkdir()
    assert cli(vault, *share(tmp_path), "--dry-run")["warnings"] == []


def test_title_and_index_keep_note_identity_across_runs(vault, tmp_path):
    first = cli(vault, *share(tmp_path, "窗边：阅读角"), "--filename-style", "title", "--index")
    note = imported_note(vault, first)
    assert note.name.endswith(f" - {FIRST[-6:]}.md")
    index = vault / "小红书收藏/索引.md"
    assert note.stem in index.read_text(encoding="utf-8")
    next_report = cli(vault, *share(tmp_path, "改了分享标题"))
    assert (next_report["created"], next_report["existing"]) == (0, 1)
    assert imported_note(vault, next_report) == note
    second = cli(vault, *share(tmp_path, "新收藏", BASE + SECOND), "--filename-style", "title")
    assert imported_note(vault, second).stem in index.read_text(encoding="utf-8")
    user_index = index.read_bytes() + "\n个人索引说明。\n".encode()
    index.write_bytes(user_index)
    result = cli(vault, *share(tmp_path), "--index", status=1)
    assert any("手写" in warning for warning in result["warnings"])
    assert index.read_bytes() == user_index


def test_map_deduplicates_variants_and_enriches_preexisting_shortlink(vault, tmp_path):
    short = cli(vault, *share(tmp_path, "历史短链", SHORT + "?xsec_token=synthetic"))
    note = imported_note(vault, short)
    link_map = tmp_path / "短链映射.json"
    link_map.write_text(json.dumps({SHORT: FIRST}), encoding="utf-8")
    full = share(tmp_path, "后续资料", "https://xiaohongshu.com/discovery/item/" + FIRST.upper())
    report = cli(vault, *full, "--link-map", link_map, "--enrich", "--attach", PNG)
    assert (report["created"], report["updated"]) == (0, 1)
    assert imported_note(vault, report) == note
    again = cli(vault, *share(tmp_path, "短链重跑", SHORT), "--link-map", link_map)
    assert again["existing"] == 1 and len(list(vault.rglob("*.md"))) == 1


def test_undo_preview_and_actual_removal_only_touch_verified_new_files(vault, tmp_path):
    path = tmp_path / "导入.json"
    original = cli(vault, *share(tmp_path), "--attach", PNG, "--report-file", path, "--index")
    files = [vault / entry["path"] for entry in original["created_files"]]
    assert len(files) == 3 and all(path.is_file() for path in files)
    preview = cli(vault, "--undo", path, "--dry-run")
    assert preview["would_remove"] == 3 and all(path.is_file() for path in files)
    undo = cli(vault, "--undo", path)
    assert undo["removed"] == 3 and all(not path.exists() for path in files)
    assert cli(vault, "--undo", path)["already_missing"] == 3


def test_undo_preserves_edited_notes_and_referenced_attachments(vault, tmp_path):
    path = tmp_path / "导入.json"
    original = cli(vault, *share(tmp_path), "--attach", PNG, "--report-file", path)
    note = imported_note(vault, original)
    before = note.read_bytes() + "\n手写批注。\n".encode()
    note.write_bytes(before)
    report = cli(vault, "--undo", path, status=1)
    assert (report["removed"], report["kept"]) == (0, 2)
    assert note.read_bytes() == before and len(list(vault.rglob("*.png"))) == 1


def test_undo_keeps_media_added_to_existing_note_and_rejects_forgery(vault, tmp_path):
    source = share(tmp_path)
    first = cli(vault, *source)
    note = imported_note(vault, first)
    report_path = tmp_path / "补全.json"
    enrich = cli(vault, *source, "--enrich", "--attach", PNG, "--report-file", report_path)
    assert enrich["updated"] == 1
    assert cli(vault, "--undo", report_path, status=1)["kept"] == 1
    forged = json.loads(report_path.read_text(encoding="utf-8"))
    forged["receipt"]["created_files"].append({
        "path": note.relative_to(vault).as_posix(), "sha256": hashlib.sha256(note.read_bytes()).hexdigest()
    })
    wrong = tmp_path / "伪造.json"
    wrong.write_text(json.dumps(forged), encoding="utf-8")
    assert "不一致" in cli(vault, "--undo", wrong, status=2)["error"]
    assert note.is_file() and len(list(vault.rglob("*.png"))) == 1


def test_size_limits_empty_and_sparse_files(vault, tmp_path):
    source = share(tmp_path)
    empty = tmp_path / "empty.png"
    empty.touch()
    assert cli(vault, *source, "--attach", empty, status=1)["failed"] == 1
    huge = tmp_path / "huge.png"
    with huge.open("wb") as stream:
        stream.truncate(2**31)
    record = cli(vault, *source, "--attach", huge, "--max-file-bytes", 65536, status=1)
    assert "单文件上限" in record["records"][0]["reason"]
    other = ROOT / "docs/03-vault-output.png"
    cap = max(PNG.stat().st_size, other.stat().st_size)
    record = cli(vault, *source, "--attach", PNG, "--attach", other, "--max-total-bytes", cap, status=1)
    assert "总量上限" in record["records"][0]["reason"]
    assert not list(vault.iterdir())


def test_json_argument_error(vault):
    error = cli(vault, "--max-total-bytes", -1, "--json-errors", status=2)
    assert error["code"] == "arguments" and error["error"]


def test_dry_run_and_import_resolve_title_collisions_consistently(vault, tmp_path):
    other = "100000000000000000000101"
    source = tmp_path / "shares.txt"
    source.write_text(f"重名收藏 {BASE}{FIRST}\n重名收藏 {BASE}{other}\n", encoding="utf-8")
    flags = ("--format", "shares", "--source", source, "--filename-style", "title")
    preview = cli(vault, *flags, "--dry-run")
    actual = cli(vault, *flags)
    paths = [item["note"] for item in actual["records"]]
    assert paths == [item["note"] for item in preview["records"]]
    assert len(set(paths)) == 2
    assert actual["created"] == 2


def test_undo_does_not_remove_index_updated_by_later_import(vault, tmp_path):
    path = tmp_path / "first.json"
    old = cli(vault, *share(tmp_path), "--index", "--report-file", path)
    second = cli(vault, *share(tmp_path, "保留后导入的笔记", BASE + SECOND))
    index = vault / "小红书收藏/索引.md"
    assert FIRST in index.read_text(encoding="utf-8") and SECOND in index.read_text(encoding="utf-8")
    preview = cli(vault, "--undo", path, "--dry-run")
    assert any(item["status"] == "would_refresh_index" for item in preview["records"])
    actual = cli(vault, "--undo", path)
    assert actual["removed"] == 1
    assert any(item["status"] == "refreshed_index" for item in actual["records"])
    assert not list(vault.rglob("*.lock"))
    assert not imported_note(vault, old).exists()
    assert imported_note(vault, second).exists()
    assert FIRST not in index.read_text(encoding="utf-8")
    assert SECOND in index.read_text(encoding="utf-8")


def test_total_cap_applies_across_batch(vault, tmp_path):
    attachment = tmp_path / "test.png"
    attachment.write_bytes(PNG.read_bytes())
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps({"version": 1, "items": [
        {"id": FIRST, "media": [attachment.name]}, {"id": SECOND, "media": [attachment.name]},
    ]}), encoding="utf-8")
    flags = ("--format", "json", "--source", source, "--max-total-bytes", attachment.stat().st_size)
    preview = cli(vault, *flags, "--dry-run", status=1)
    actual = cli(vault, *flags, status=1)
    assert (preview["planned"], preview["failed"], actual["created"], actual["failed"]) == (1, 1, 1, 1)
    assert "总量上限" in actual["records"][1]["reason"] and len(list(vault.rglob("*.png"))) == 1
