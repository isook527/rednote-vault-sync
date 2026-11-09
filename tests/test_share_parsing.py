import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills/rednote-vault-sync/scripts/import_rednote.py"
FIXTURES = ROOT / "tests/fixtures/shares_real.txt"


def test_mobile_and_web_share_formats(tmp_path):
    vault = tmp_path / "带空格 Vault"
    vault.mkdir()
    result = subprocess.run([
        sys.executable, "-B", str(CLI), "--vault", str(vault), "--format", "shares",
        "--source", str(FIXTURES), "--filename-style", "id",
    ], capture_output=True, encoding="utf-8")
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["created"] == 8 and report["failed"] == 1
    direct = "https://www.xiaohongshu.com/explore/"
    rows = [
        ("高考英语3500词，一张图记完！", "Korn词汇档案", direct + "65a1b2c3d4e5f6a7b8c9d0e1"),
        ("雅思口语P2万能素材", "某某老师", "https://xhslink.com/a/AbC123xyz"),
        ("老版本标题", "", "https://xhslink.com/AbC123"),
        ("小红书收藏", "", direct + "65a1b2c3d4e5f6a7b8c9d0e2"),
        ("小红书收藏", "", direct + "65a1b2c3d4e5f6a7b8c9d0e3"),
        ("小米 - 新品 - 开箱", "数码阿明", "https://xhslink.com/a/Mi123"),
        ("收藏这个", "", "https://xhslink.com/a/Paren1"),
        ("标题 #话题 🔥", "作者", "https://xhslink.com/a/Emoji1"),
    ]
    for item, (title, author, url) in zip(report["records"], rows):
        note = vault / item["note"]
        props = {}
        for line in note.read_text(encoding="utf-8").split("---",2)[1].splitlines():
            if ": " in line:
                key, value = line.split(": ",1)
                if key in ("title","author","sourceUrl","sourceId"):
                    props[key] = json.loads(value)
        identity = url.rsplit("/",1)[1] if url.startswith(direct) else "share-" + hashlib.sha256(url.encode()).hexdigest()[:24]
        assert props == {"title":title, "author":author, "sourceUrl":url, "sourceId":identity}
    assert "用户主页" in report["records"][-1]["reason"]


def test_gbk_environment_keeps_utf8_table(tmp_path):
    vault=tmp_path/"中文 Vault"
    vault.mkdir()
    source=tmp_path/"分享文案.txt"
    source.write_text("演示 https://xhslink.com/a/KornviaDemo\n",encoding="utf-8")
    result=subprocess.run([sys.executable,"-B",str(CLI),"--vault",str(vault),
                           "--format","shares","--source",str(source),"--output","table","--dry-run"],
                          env={**os.environ,"PYTHONIOENCODING":"gbk"},capture_output=True)
    assert result.returncode == 0 and "演示" in result.stdout.decode("utf-8")
