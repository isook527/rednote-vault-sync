import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("input_format", ["shares", "inbox"])
def test_iphone_device_shares(tmp_path, input_format):
    vault = tmp_path / "iPhone Vault"
    (vault / ".obsidian").mkdir(parents=True)
    fixture = FIXTURES.with_name("shares_device.txt")
    shares = fixture.read_text(encoding="utf-8")
    command = [
        sys.executable, "-B", str(CLI), "--vault", str(vault), "--format", input_format,
    ]
    if input_format == "shares":
        command.extend(["--source", str(fixture)])
    else:
        inbox = vault / "小红书收藏/收件箱.md"
        inbox.parent.mkdir()
        inbox.write_text(f"# 收件箱\n\n{shares}\n## 已处理\n", encoding="utf-8")

    result = subprocess.run(command, capture_output=True, encoding="utf-8")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report["records"]
    assert report["created"] == report["link_only"] == 3
    expected = [
        ("想一个月多看2本的朋友，可以看看这篇 不需要书单 不需...", "DemoRead001"),
        ("12种花草一口气认清｜上篇（1–6） 花草分不清？先把...", "DemoLeaf002"),
        ("𐂐 🍪 𖥣｜分享今日色彩zui佳画册～ 今日画册、随手翻...", "DemoBook003"),
    ]
    assert [record["title"] for record in report["records"]] == [title for title, _ in expected]
    for record, (_, short_id) in zip(report["records"], expected):
        url = f"https://xhslink.cn/o/{short_id}"
        identity = "share-" + hashlib.sha256(url.encode()).hexdigest()[:24]
        note = (vault / record["note"]).read_text(encoding="utf-8")
        assert f'sourceUrl: "{url}"' in note
        assert f'sourceId: "{identity}"' in note
        assert "【小红书】" not in note

    if input_format == "inbox":
        assert report["inbox_moved"] == 3
        pending, history = inbox.read_text(encoding="utf-8").split("## 已处理")
        assert "xhslink.cn" not in pending
        assert history.count("[[") == 3

    repeated = subprocess.run(command, capture_output=True, encoding="utf-8", check=True)
    repeated_report = json.loads(repeated.stdout)
    assert repeated_report["created"] == 0
    assert repeated_report["existing"] == (3 if input_format == "shares" else 0)
    assert len(list((vault / "小红书收藏/笔记").glob("*.md"))) == 3


@pytest.mark.parametrize("markdown_link", [False, True], ids=["plain", "markdown"])
def test_web_share_urls(tmp_path, markdown_link):
    vault = tmp_path / "Vault"
    (vault / ".obsidian").mkdir(parents=True)
    source = FIXTURES.with_name("shares_web.txt")
    if markdown_link:
        urls = source.read_text(encoding="utf-8").splitlines()
        source = tmp_path / "markdown-links.txt"
        source.write_text("\n".join(f"[{url}]({url})" for url in urls), encoding="utf-8")

    command = [
        sys.executable, "-B", str(CLI), "--vault", str(vault), "--format", "shares",
        "--source", str(source), "--filename-style", "id",
    ]
    result = subprocess.run(command, capture_output=True, encoding="utf-8", check=True)
    report = json.loads(result.stdout)
    assert len(report["records"]) == report["created"] == report["link_only"] == 3
    assert report["duplicate"] == 0
    for index, record in enumerate(report["records"], 301):
        identity = f"{index:024d}"
        note = (vault / record["note"]).read_text(encoding="utf-8")
        assert f'sourceId: "{identity}"' in note
        assert f'sourceUrl: "https://www.xiaohongshu.com/explore/{identity}"' in note
        assert "xsec_token" not in note and "xsec_source" not in note

    repeated = subprocess.run(command, capture_output=True, encoding="utf-8", check=True)
    repeated_report = json.loads(repeated.stdout)
    assert repeated_report["created"] == 0
    assert repeated_report["existing"] == 3
    assert repeated_report["duplicate"] == 0


def test_gbk_environment_keeps_utf8_table(tmp_path):
    vault=tmp_path/"中文 Vault"
    vault.mkdir()
    source=tmp_path/"分享文案.txt"
    source.write_text("演示 https://xhslink.com/a/KornviaDemo\n",encoding="utf-8")
    result=subprocess.run([sys.executable,"-B",str(CLI),"--vault",str(vault),
                           "--format","shares","--source",str(source),"--output","table","--dry-run"],
                          env={**os.environ,"PYTHONIOENCODING":"gbk"},capture_output=True)
    assert result.returncode == 0 and "演示" in result.stdout.decode("utf-8")
