#!/usr/bin/env python3
"""Build a local demo Vault from the bundled, synthetic manifest."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, default=Path("demo-vault"))
    args = parser.parse_args()
    vault = args.vault
    if vault.is_symlink() or (vault.exists() and not (vault / ".obsidian").is_dir()):
        parser.error("请选择不存在的示例目录或已有 Obsidian Vault。")
    if not vault.exists():
        (vault / ".obsidian").mkdir(parents=True)
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "skills/rednote-vault-sync/scripts/import_rednote.py"),
         "--format", "json", "--source", str(ROOT / "examples/manifest.json"), "--vault", str(vault)],
        check=True, stdout=subprocess.PIPE, encoding="utf-8",
    )
    report = json.loads(result.stdout)
    print(f"演示 Vault　{vault.resolve()}")
    print(f"新增 {report.get('created', 0)} 篇，已存在 {report.get('existing', 0)} 篇")
    print("在 Obsidian 打开此 Vault，然后查看「小红书收藏/收藏.base」或「小红书收藏/索引.md」。")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
