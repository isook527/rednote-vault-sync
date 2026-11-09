import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "skills/rednote-vault-sync/scripts/import_rednote.py"
EXAMPLE = REPO / "examples"
FIRST = "000000000000000000000101"
SECOND = "000000000000000000000102"
BASE = "https://www.xiaohongshu.com/explore/"


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.vault = self.root / "空白 Vault"
        self.vault.mkdir()

    def invoke(self, mode, source, *args, expect=0):
        result = subprocess.run(
            [sys.executable, str(CLI), "--filename-style", "id", "--no-index", "--format", mode, "--source", str(source),
             "--vault", str(self.vault), *args],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return json.loads(result.stderr if expect == 2 else result.stdout)

    def manifest(self, *items):
        path = self.root / "collection.json"
        path.write_text(json.dumps({"version": 1, "items": items}), encoding="utf-8")
        return path

    def note(self, identity=FIRST):
        return self.vault / "小红书收藏" / "笔记" / f"{identity}.md"

    def example_import(self, *args):
        return self.invoke("json", EXAMPLE / "collection.json", *args)

    def test_preview_is_read_only(self):
        result = self.example_import("--dry-run")
        self.assertEqual((result["planned"], result["local_media"]), (2, 2))
        self.assertEqual(list(self.vault.iterdir()), [])

    def test_import_resolves_and_preserves_actual_media(self):
        result = self.example_import()
        self.assertEqual((result["created"], result["local_media"]), (2, 2))
        note = self.note().read_text(encoding="utf-8")
        self.assertIn('collection: "审美收藏"', note)
        self.assertIn("窗边阅读角的暖光搭配", note)
        links = re.findall(r"!\[\[([^\]]+)\]\]", note)
        self.assertEqual(len(links), 2)
        for link in links:
            path = self.vault / link
            original = EXAMPLE / "media" / f"reading-corner{path.suffix}"
            self.assertEqual(path.read_bytes(), original.read_bytes())

    def test_reimport_keeps_user_edits(self):
        self.example_import()
        with self.note().open("a", encoding="utf-8") as file:
            file.write("\n用户补充\n")
        before = self.note().read_bytes()
        result = self.example_import()
        self.assertEqual((result["created"], result["existing"]), (0, 2))
        self.assertEqual(self.note().read_bytes(), before)

    def test_shares_dedupe_across_direct_link_forms(self):
        source = self.root / "share.txt"
        source.write_text(
            f"一篇收藏 {BASE}{FIRST}?xsec_token=synthetic-secret\n"
            f"https://www.xiaohongshu.com/discovery/item/{FIRST}\n"
            "另一篇 https://xhslink.com/a/KornviaDemo\n",
            encoding="utf-8",
        )
        result = self.invoke("shares", source, "--collection", "灵感")
        self.assertEqual((result["created"], result["duplicate"]), (2, 1))
        content = self.note().read_text(encoding="utf-8")
        self.assertIn('collection: "灵感"', content)
        self.assertNotIn("synthetic-secret", content)

    def test_partial_errors_are_recoverable(self):
        source = self.manifest({"id": FIRST, "media": ["image.png"]}, {"id": SECOND})
        result = self.invoke("json", source, expect=1)
        self.assertEqual((result["created"], result["failed"]), (1, 1))
        self.assertFalse(self.note().exists())
        (self.root / "image.png").write_bytes((EXAMPLE / "media/reading-corner.png").read_bytes())
        result = self.invoke("json", source)
        self.assertEqual((result["created"], result["existing"]), (1, 1))

    def test_invalid_urls_do_not_echo_secrets(self):
        source = self.manifest(
            {"url": "https://www.xiaohongshu.com.evil.example/a?xsec_token=synthetic-secret"},
            {"id": FIRST, "url": f"{BASE}{SECOND}"},
        )
        result = self.invoke("json", source, expect=1)
        self.assertEqual(result["failed"], 2)
        self.assertNotIn("synthetic-secret", json.dumps(result))
        self.assertEqual(list(self.vault.iterdir()), [])

    def test_unsupported_obsidian_video_extension_is_rejected(self):
        video = self.root / "video.m4v"
        video.write_bytes((EXAMPLE / "media/reading-corner.mp4").read_bytes())
        report = self.invoke("json", self.manifest({"id": FIRST, "media": [video.name]}), expect=1)
        self.assertEqual(report["failed"], 1)
        self.assertFalse(self.note().exists())

    def test_manifest_path_traversal_rejected(self):
        source = self.manifest({"id": FIRST, "media": ["../outside.png"]})
        self.assertEqual(self.invoke("json", source, expect=1)["failed"], 1)
        self.assertFalse(self.note().exists())

    def test_absolute_output_and_output_symlink_rejected(self):
        source = self.manifest({"id": FIRST})
        self.assertIn("路径", self.invoke("json", source, "--folder", "//outside", expect=2)["error"])
        outside = self.root / "outside"
        outside.mkdir()
        (self.vault / "小红书收藏").symlink_to(outside, target_is_directory=True)
        self.assertIn("符号链接", self.invoke("json", source, expect=2)["error"])
        self.assertEqual(list(outside.iterdir()), [])

    def test_preview_reports_blocked_output_parent(self):
        (self.vault / "小红书收藏").write_text("keep", encoding="utf-8")
        source = self.manifest({"id": FIRST})
        report = self.invoke("json", source, "--dry-run", expect=1)
        self.assertEqual(report["planned"], 0)
        self.assertEqual(report["failed"], 1)

    def test_resume_reuses_previously_written_media(self):
        raw = (EXAMPLE / "media/reading-corner.png").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        dest = self.vault / "小红书收藏" / "媒体" / FIRST / (digest + ".png")
        dest.parent.mkdir(parents=True)
        dest.write_bytes(raw)
        inode = dest.stat().st_ino
        self.example_import()
        self.assertEqual(dest.stat().st_ino, inode)
        self.assertTrue(self.note().is_file())

    def test_legacy_migration_skips_index_and_private_fields(self):
        old = self.root / "old"
        notes = old / "旧笔记"
        notes.mkdir(parents=True)
        assets = old / "附件"
        assets.mkdir()
        media = EXAMPLE / "media/reading-corner.png"
        (assets / "照片.png").write_bytes(media.read_bytes())
        common = (
            'sourcePlugin: "rednote-vault-sync"\nsourcePlatform: "rednote"\n'
            f'sourceId: "{FIRST}"\naccountId: "synthetic-private"\n'
            'title: "旧版收藏"\naestheticAlbumName: "审美"\ntags:\n  - "设计"\n'
        )
        legacy = notes / "a.md"
        legacy.write_text(
            f'---\n{common}sourceType: "aesthetic"\n---\n# 旧版收藏\n\n'
            '![[附件/照片.png]]\n![[手稿.pdf]]\n![](https://example.invalid/pic)\n旧正文\n',
            encoding="utf-8",
        )
        (notes / "b.md").write_text(
            f'---\n{common}sourceType: "aesthetic-media-index"\n---\n', encoding="utf-8",
        )
        before = legacy.read_bytes()
        report = self.invoke("legacy", notes, "--legacy-vault", str(old))
        self.assertEqual((report["created"], report["local_media"], report["remote_media_skipped"]), (1, 1, 1))
        note = self.note().read_text(encoding="utf-8")
        self.assertEqual(report["unsupported_media_skipped"], 1)
        self.assertIn("手稿.pdf", note)
        self.assertNotIn("![[手稿.pdf]]", note)
        self.assertIn("旧正文", note)
        self.assertIn('"设计"', note)
        self.assertNotIn("synthetic-private", note)
        self.assertNotIn("example.invalid", note)
        self.assertEqual(legacy.read_bytes(), before)

    def test_share_to_enriched_note_preserves_edits_and_is_idempotent(self):
        third = "000000000000000000000103"
        shares = self.root / "分享.txt"
        shares.write_text(
            f"窗边阅读角\n{BASE}{FIRST}\n\n第二条 {BASE}{SECOND}\n\n第三条 {BASE}{third}\n",
            encoding="utf-8",
        )
        self.assertEqual(self.invoke("shares", shares, "--dry-run")["planned"], 3)
        self.assertEqual(list(self.vault.iterdir()), [])
        self.assertEqual(self.invoke("shares", shares)["created"], 3)
        note = self.note()
        annotation = "\r\n## 我的批注\r\n\r\n试试把灯放到左侧。\r\n"
        original = note.read_bytes().replace(b"\n", b"\r\n") + annotation.encode()
        note.write_bytes(original)
        self.assertIn("窗边阅读角", original.decode())
        shares.write_text(f"{BASE}{FIRST}\n", encoding="utf-8")
        body = self.root / "正文.txt"
        body.write_text("灯光观察。\n\n## 记录\n照度偏低。", encoding="utf-8")
        attachments = [
            EXAMPLE / "media/reading-corner.png",
            REPO / "docs/03-vault-output.png",
        ]
        flags = ["--enrich", "--body-file", str(body)]
        for path in attachments:
            flags += ["--attach", str(path)]
        plan = self.invoke("shares", shares, *flags, "--dry-run")
        self.assertEqual((plan["planned_update"], plan["local_media"]), (1, 2))
        self.assertEqual(note.read_bytes(), original)
        self.assertFalse((note.parents[1] / "媒体").exists())
        result = self.invoke("shares", shares, *flags)
        self.assertEqual((result["created"], result["updated"], result["local_media"]), (0, 1, 2))
        final = note.read_bytes()
        self.assertTrue(final.startswith(original.replace(b"localMediaCount: 0", b"localMediaCount: 2")))
        self.assertIn(annotation.encode(), final)
        self.assertIn("照度偏低。", final.decode())
        links = re.findall(r"!\[\[([^\]]+)\]\]", final.decode())
        self.assertEqual([hashlib.sha256((self.vault / p).read_bytes()).hexdigest() for p in links],
                         [hashlib.sha256(p.read_bytes()).hexdigest() for p in attachments])
        repeat = self.invoke("shares", shares, *flags)
        self.assertEqual((repeat["created"], repeat["updated"], repeat["existing"]), (0, 0, 1))
        self.assertEqual(note.read_bytes(), final)
        self.assertEqual(len(list(self.vault.rglob("*.md"))), 3)

    def test_shared_attachments_require_a_single_identified_note(self):
        shares = self.root / "shares.txt"
        shares.write_text(f"{BASE}{FIRST}\n{BASE}{SECOND}\n", encoding="utf-8")
        asset = EXAMPLE / "media/reading-corner.png"
        result = self.invoke("shares", shares, "--attach", str(asset), "--dry-run", expect=1)
        self.assertEqual((result["planned"], result["unassigned_media"]), (2, [1]))
        self.assertEqual(list(self.vault.iterdir()), [])
        result = self.invoke("shares", shares, "--attach", str(asset), expect=1)
        self.assertEqual((result["created"], result["local_media"]), (2, 0))
        self.assertFalse((self.note().parents[1] / "媒体").exists())

    def test_conflicting_existing_body_blocks_supplement_without_writes(self):
        shares = self.root / "shares.txt"
        shares.write_text(f"{BASE}{FIRST}\n", encoding="utf-8")
        self.invoke("shares", shares)
        note = self.note()
        original = note.read_bytes() + "\n## 收藏内容\n\n自己抄写的正文。\n".encode()
        note.write_bytes(original)
        body = self.root / "body.txt"
        body.write_text("另一份正文。", encoding="utf-8")
        result = self.invoke(
            "shares", shares, "--enrich", "--body-file", str(body),
            "--attach", str(EXAMPLE / "media/reading-corner.png"), expect=1,
        )
        self.assertEqual(result["conflict"], 1)
        self.assertIn("正文", result["records"][0]["reason"])
        self.assertEqual(note.read_bytes(), original)
        self.assertFalse((note.parents[1] / "媒体").exists())

    def test_enrich_mismatched_link_does_not_create_another_note(self):
        shares = self.root / "shares.txt"
        shares.write_text(f"{BASE}{FIRST}\n", encoding="utf-8")
        self.invoke("shares", shares)
        shares.write_text(f"{BASE}{SECOND}\n", encoding="utf-8")
        asset = EXAMPLE / "media/reading-corner.png"
        result = self.invoke("shares", shares, "--enrich", "--attach", str(asset), expect=1)
        self.assertEqual((result["conflict"], result["created"]), (1, 0))
        self.assertEqual(list(self.vault.rglob("*.md")), [self.note()])
        self.assertFalse((self.note().parents[1] / "媒体").exists())

    def test_old_short_link_note_can_be_enriched_without_resolving_it(self):
        shares = self.root / "shares.txt"
        link = "https://xhslink.com/a/KornviaDemo"
        shares.write_text(link + "\n", encoding="utf-8")
        identity = "share-" + hashlib.sha256(link.encode()).hexdigest()[:24]
        note = self.note(identity)
        note.parent.mkdir(parents=True)
        note.write_text(
            f'---\nsourcePlatform: rednote\nsourceId: "{identity}"\nsourceUrl: {link}\n'
            'localMediaCount: 0\n---\n\n# 旧收藏\n\n这条备注要保留。\n',
            encoding="utf-8",
        )
        media = EXAMPLE / "media/reading-corner.png"
        self.assertEqual(self.invoke("shares", shares, "--attach", str(media))["existing"], 1)
        self.assertEqual(self.invoke("shares", shares, "--enrich", "--attach", str(media))["updated"], 1)
        self.assertIn("这条备注要保留。", note.read_text(encoding="utf-8"))
        self.assertEqual(len(list(self.vault.rglob("*.md"))), 1)

    def test_bad_json_is_fatal_without_output(self):
        source = self.root / "bad.json"
        source.write_text("{invalid", encoding="utf-8")
        self.assertIn("JSON", self.invoke("json", source, expect=2)["error"])
        self.assertEqual(list(self.vault.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
