import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/rednote-vault-sync/scripts"
FIRST = "000000000000000000000101"
SECOND = "000000000000000000000102"
BASE = "https://www.xiaohongshu.com/explore/"
MEDIA = ROOT / "examples/media/reading-corner.png"


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "中文 Vault"
    (path / ".obsidian").mkdir(parents=True)
    return path


def command(vault, *args, code=0, script="import_rednote.py"):
    completed = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPTS/script),
                                "--vault", str(vault), *map(str,args)], capture_output=True, encoding="utf-8")
    assert completed.returncode == code, (completed.stdout, completed.stderr)
    return json.loads(completed.stderr if code==2 else completed.stdout)


def save(path, content):
    path.write_bytes(content.encode("utf-8"))
    return path


def data(path, items):
    return save(path, json.dumps({"version":1,"items":items}, ensure_ascii=False))


def test_new_defaults_and_reading_state(vault, tmp_path):
    share = save(tmp_path/"分享.txt", f"阅读角 {BASE}{FIRST}\n")
    preview = command(vault, "--format","shares","--source",share,"--dry-run")
    assert preview["planned"] == preview["link_only"] == 1
    assert list(vault.iterdir()) == [vault/".obsidian"]
    report = command(vault, "--format","shares","--source",share)
    note = vault/report["records"][0]["note"]
    assert note.name == "阅读角 - 000101.md"
    text = note.read_text(encoding="utf-8")
    assert "savedAt: " in text and 'status: "未读"' in text and "## 我的笔记" in text
    assert (vault/report["report_path"]).is_file()
    index = vault/"小红书收藏/索引.md"
    base = vault/"小红书收藏/收藏.base"
    assert note.stem in index.read_text(encoding="utf-8")
    payload = base.read_text(encoding="utf-8")
    assert all(f"name: {label}" in payload for label in ("全部","按收藏夹","未读"))
    assert "property: note.savedAt" in payload and "direction: DESC" in payload
    before = text.replace('status: "未读"', 'status: "已用"') + "\n手写批注。\n"
    save(note,before)
    body=save(tmp_path/"补正文.txt","新的本地正文。")
    command(vault,"--format","shares","--source",share,"--enrich","--body-file",body)
    assert note.read_text(encoding="utf-8").startswith(before)
    save(base, payload+"\n# 手动编辑\n")
    command(vault,"--format","shares","--source",share,code=1)
    assert base.read_text(encoding="utf-8")==payload+"\n# 手动编辑\n"


def test_annotation_preview_update_conflict_and_reversible_local_patch(vault,tmp_path):
    report=command(vault,"--format","json","--source",data(tmp_path/"manifest.json",[
        {"id":FIRST,"title":"布光","body":"窗边布光正文","tags":["已有"]},
        {"id":SECOND,"title":"补光灯"},
    ]))
    note=vault/report["records"][0]["note"]
    related=report["records"][1]["note"]
    original=note.read_bytes()
    source=data(tmp_path/"annotations.json",[{
        "id":FIRST,"summary":"窗边布光使用暖光","points":["一条要点"],"transcript":"截图里的正文",
        "transcript_source":["图1"],"tags":["已有","摄影/布光"],"related":[related],
    }])
    preview=command(vault,"--annotate",source,"--dry-run")
    assert preview["planned_update"]==1 and preview["records"][0]["summary"]=="窗边布光使用暖光"
    assert note.read_bytes()==original
    first=command(vault,"--annotate",source)
    annotated=note.read_text(encoding="utf-8")
    assert "### 图片转写（AI）" in annotated and "来源图片 · 图1" in annotated
    assert 'tags: ["rednote", "已有", "摄影/布光"]' in annotated and "[["+related[:-3]+"]]" in annotated
    assert "_patches" not in json.dumps(first)
    assert command(vault,"--annotate",source)["existing"]==1
    save(note,annotated+"\n我的额外批注。\n")
    second=command(vault,"--annotate",data(source,[{"id":FIRST,"summary":"更具体的摘要"}]))
    assert note.read_text(encoding="utf-8").count("<!-- rednote:ai:start -->")==1
    assert command(vault,"--undo",vault/second["report_path"],"--dry-run")["would_restore"]==1
    assert command(vault,"--undo",vault/second["report_path"])["restored"]==1
    assert note.read_text(encoding="utf-8")==annotated+"\n我的额外批注。\n"
    assert command(vault,"--undo",vault/first["report_path"])["restored"]==1
    assert note.read_bytes()==original+"\n我的额外批注。\n".encode()
    assert command(vault,"--undo",vault/first["report_path"])["already_restored"]==1
    first=command(vault,"--annotate",source)
    edited=note.read_text(encoding="utf-8").replace("更具体的摘要","手改了摘要")
    save(note,edited)
    assert command(vault,"--annotate",source,code=1)["conflict"]==1
    assert command(vault,"--undo",vault/first["report_path"],code=1)["kept"]==1
    assert note.read_text(encoding="utf-8")==edited


def test_annotation_rejects_missing_or_unsafe_related_notes(vault,tmp_path):
    report=command(vault,"--format","json","--source",data(tmp_path/"manifest.json",[{"id":FIRST}]))
    path=vault/report["records"][0]["note"]
    before=path.read_bytes()
    for related in ("不存在", "../Vault/外部"):
        source=data(tmp_path/"annotations.json",[{"id":FIRST,"summary":"摘要","related":[related]}])
        assert command(vault,"--annotate",source,code=1)["failed"]==1
        assert path.read_bytes()==before


def test_query_uses_existing_content_and_filters_without_writes(vault,tmp_path):
    report=command(vault,"--format","json","--source",data(tmp_path/"manifest.json",[
        {"id":FIRST,"title":"布光记录","body":"窗边柔和灯光","collection":"摄影","author":"作者甲","tags":["摄影/布光"]},
        {"id":SECOND,"title":"布光收纳","body":"收纳灯架","collection":"摄影"},
    ]))
    note=vault/report["records"][0]["note"]
    with note.open("a",encoding="utf-8") as f:
        f.write("\n手写观察：反光板有帮助。\n")
    before={p:p.read_bytes() for p in vault.rglob("*") if p.is_file()}
    result=command(vault,"--text","布光 反光板","--tag","#摄影/布光","--collection","摄影",
                   "--author","作者甲","--status","未读","--limit",1,script="query_rednote.py")
    assert result["count"]==1 and result["matches"][0]["path"]==note.relative_to(vault).as_posix()
    assert "反光板" in result["matches"][0]["snippet"] and result["matches"][0]["link"].startswith("[[")
    assert command(vault,"--text","不存在",script="query_rednote.py")["count"]==0
    assert {p:p.read_bytes() for p in vault.rglob("*") if p.is_file()}==before


def test_inbox_only_moves_successes_and_undo_restores_exact_text(vault):
    path=vault/"小红书收藏/收件箱.md"
    assert "不存在" in command(vault,"--format","inbox",code=2)["error"]
    assert not path.exists()
    assert command(vault,"--format","inbox","--init-inbox","--dry-run")["planned"]==1
    assert not path.exists()
    assert command(vault,"--format","inbox","--init-inbox")["created"]==1
    original=(f"标题另起一行 ![[其他笔记]] <script>test</script>\n{BASE}{FIRST}?xsec_token=synthetic\n\n"
              "https://www.xiaohongshu.com/user/profile/000000000000000000000999\n\n"
              "## 已处理\n\n旧历史保留。\n")
    save(path,original)
    preview=command(vault,"--format","inbox","--dry-run",code=1)
    assert preview["inbox_would_move"]==1 and path.read_text(encoding="utf-8")==original
    imported=command(vault,"--format","inbox",code=1)
    assert imported["created"]==1 and imported["inbox_moved"]==1
    after=path.read_text(encoding="utf-8")
    pending,history=after.split("## 已处理")
    assert "标题另起一行" not in pending and "标题另起一行" in history
    assert "用户主页" in pending and "旧历史保留。" in history
    assert all(value not in history for value in ("synthetic", "![[其他笔记]]", "<script>"))
    again=command(vault,"--format","inbox",code=1)
    assert again["created"]==0 and "report_path" not in again
    undo=command(vault,"--undo",vault/imported["report_path"])
    assert undo["restored"]==1 and path.read_text(encoding="utf-8")==original
    assert not list((vault/"小红书收藏/笔记").glob("*.md"))


def test_inbox_later_edits_block_deletion_of_its_archived_notes(vault):
    path=vault/"小红书收藏/收件箱.md"
    path.parent.mkdir()
    save(path,f"窗边布光 {BASE}{FIRST}\n")
    report=command(vault,"--format","inbox")
    note=vault/report["records"][0]["note"]
    edited=path.read_text(encoding="utf-8")+"\n我又添加了历史备注。\n"
    save(path,edited)
    undo=command(vault,"--undo",vault/report["report_path"],code=1)
    assert undo["kept"] and note.is_file() and path.read_text(encoding="utf-8")==edited


def test_inbox_snapshot_prevents_overwrite_on_concurrent_edit(vault,monkeypatch):
    sys.path.insert(0,str(SCRIPTS))
    try:
        from rednote_files import ConflictError
        from rednote_inbox import Inbox
    finally:
        sys.path.remove(str(SCRIPTS))
    path=vault/"收件箱.md"
    save(path,f"窗边布光 {BASE}{FIRST}\n")
    inbox=Inbox(path)
    save(path,path.read_text(encoding="utf-8")+"稍后新增一条。\n")
    before=path.read_bytes()
    with pytest.raises(ConflictError):
        inbox.update([{"input":1,"status":"created","note":"笔记/窗边布光.md"}],vault,False,vault/"pending.md")
    assert path.read_bytes()==before


def test_query_runs_with_read_only_vault(vault, tmp_path):
    import os
    import stat

    share = save(tmp_path / "分享.txt", f"阅读角 {BASE}{FIRST}\n")
    command(vault, "--format", "shares", "--source", share)
    if os.name == "nt":
        pytest.skip("Windows chmod does not support POSIX directory permissions")
    permissions = [(path, stat.S_IMODE(path.stat().st_mode)) for path in [vault, *vault.rglob("*")]]
    try:
        for path, mode in reversed(permissions):
            path.chmod(mode & ~0o222)
        assert command(vault, "--text", "阅读角", script="query_rednote.py")["count"] == 1
    finally:
        for path, mode in permissions:
            path.chmod(mode)


def test_import_annotation_recall_inbox_and_undo_run_with_network_disabled(vault, tmp_path):
    share = save(tmp_path / "分享.txt", f"阅读角 {BASE}{FIRST}\n")
    annotations = data(tmp_path / "annotations.json", [{"id": FIRST, "summary": "本地摘要"}])
    inbox = vault / "小红书收藏/收件箱.md"
    inbox.parent.mkdir()
    save(inbox, f"布光 {BASE}{SECOND}\n")
    code = r"""
import contextlib
import io
import json
import runpy
import socket
import sys
from pathlib import Path
from unittest.mock import patch

scripts, vault, share, annotations = map(Path, sys.argv[1:])
sys.path.insert(0, str(scripts))

def run(script, *flags):
    sys.argv = [str(scripts / script), "--vault", str(vault), *map(str, flags)]
    with contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")) as captured:
        try:
            runpy.run_path(sys.argv[0], run_name="__main__")
        except SystemExit as result:
            assert result.code == 0
        captured.flush()
        return json.loads(captured.buffer.getvalue())

with patch.object(socket, "socket", side_effect=AssertionError("Unexpected network access")):
    assert run("import_rednote.py", "--format", "shares", "--source", share)["created"] == 1
    assert run("import_rednote.py", "--annotate", annotations)["updated"] == 1
    assert run("query_rednote.py", "--text", "本地摘要")["count"] == 1
    report = run("import_rednote.py", "--format", "inbox")
    assert report["inbox_moved"] == 1
    assert run("import_rednote.py", "--undo", vault / report["report_path"])["restored"] == 1
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, *map(str, (SCRIPTS, vault, share, annotations))],
                            capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


def test_release_versions_agree():
    import re

    skill = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
    version = re.search(r'^  version: "(\d+\.\d+\.\d+)"$', skill, re.M)[1]
    output = subprocess.check_output([sys.executable, "-I", "-B", str(SCRIPTS / "import_rednote.py"), "--version"], text=True)
    assert output.strip() == version
    assert re.search(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)[1] == version
    assert f"rednote-vault-sync-skill-v{version}.zip" in (ROOT / "README.md").read_text(encoding="utf-8")



def test_annotation_survives_windows_line_ending_conversion(vault, tmp_path):
    report = command(vault, "--format", "json", "--source",
                     data(tmp_path / "manifest.json", [{"id": FIRST}]))
    note = vault / report["records"][0]["note"]
    original = note.read_bytes()
    source = data(tmp_path / "annotations.json", [{"id": FIRST, "summary": "整理一"}])
    first = command(vault, "--annotate", source)
    annotated = note.read_bytes()
    note.write_bytes(annotated.replace(b"\n", b"\r\n"))
    assert command(vault, "--annotate", source)["existing"] == 1
    second = command(vault, "--annotate", data(source, [{"id": FIRST, "summary": "整理二"}]))
    assert command(vault, "--undo", vault / second["report_path"])["restored"] == 1
    assert note.read_bytes() == annotated.replace(b"\n", b"\r\n")
    assert command(vault, "--undo", vault / first["report_path"])["restored"] == 1
    assert note.read_bytes() == original.replace(b"\n", b"\r\n")
