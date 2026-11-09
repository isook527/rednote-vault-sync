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
            [sys.executable, str(CLI), "--format", mode, "--source", str(source),
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

    def test_bad_json_is_fatal_without_output(self):
        source = self.root / "bad.json"
        source.write_text("{invalid", encoding="utf-8")
        self.assertIn("JSON", self.invoke("json", source, expect=2)["error"])
        self.assertEqual(list(self.vault.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
