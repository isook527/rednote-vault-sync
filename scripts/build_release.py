#!/usr/bin/env python3
"""Build and verify the full release and the Claude custom Skill archive."""

import argparse
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/rednote-vault-sync"
FIXED = ("README.md", "CHANGELOG.md", "LICENSE", "PRIVACY.md", "SOURCES.md")
TREES = (".github/workflows", "docs", "evals/cases", "evals/results", "examples", "scripts", "skills", "tests")
EVAL_FILES = ("evals/README.md", "evals/prepare.py", "evals/triggers.md")


def release_version():
    return re.search(r'^  version: "(\d+\.\d+\.\d+)"$', (SKILL / "SKILL.md").read_text(encoding="utf-8"), re.M)[1]


def trees(directory):
    for file in sorted(directory.rglob("*")):
        relative = file.relative_to(directory)
        if file.is_file() and not file.is_symlink() and not any(
            part.startswith(".") or part == "__pycache__" for part in relative.parts
        ) and file.suffix in {".md", ".py", ".yaml", ".yml", ".json", ".txt", ".png", ".svg", ".mp4", ""}:
            yield file


def archive(destination, files, base, prefix):
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as package:
        for file in files:
            package.write(file, (Path(prefix) / file.relative_to(base)).as_posix())


def self_test(full, standalone, version, prefix):
    with tempfile.TemporaryDirectory(prefix="rednote-release-") as temporary:
        folder = Path(temporary)
        with ZipFile(full) as package:
            package.extractall(folder / "full")
        with ZipFile(standalone) as package:
            package.extractall(folder / "skill")
        root = folder / "full" / prefix
        clean_vault = folder / "演示 Vault"
        command = [sys.executable, "-B", str(root / "scripts/demo.py"), "--vault", str(clean_vault)]
        subprocess.run(command, cwd=root, check=True)
        subprocess.run(command, cwd=root, check=True)
        notes = clean_vault / "小红书收藏/笔记"
        if len(list(notes.glob("*.md"))) != 2 or not (notes.parent / "收藏.base").is_file():
            raise RuntimeError("发布包示例产物不完整。")
        standalone_cli = folder / "skill/rednote-vault-sync/scripts/import_rednote.py"
        result = subprocess.check_output([sys.executable, "-I", "-B", str(standalone_cli), "--version"], text=True)
        if result.strip() != version:
            raise RuntimeError("独立 Skill 的版本号不一致。")


def build(destination):
    version = release_version()
    prefix = f"RedNote-Vault-Sync-v{version}"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rednote-build-") as temporary:
        build_dir = Path(temporary)
        full = build_dir / f"{prefix}.zip"
        standalone = build_dir / f"rednote-vault-sync-skill-v{version}.zip"
        files = [ROOT / name for name in (*FIXED, *EVAL_FILES)]
        files.extend(file for tree in TREES for file in trees(ROOT / tree))
        archive(full, files, ROOT, prefix)
        archive(standalone, trees(SKILL), SKILL.parent, "")
        self_test(full, standalone, version, prefix)
        checksums = build_dir / "SHA256SUMS.txt"
        checksums.write_text("".join(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}\n"
                                     for file in (full, standalone)), encoding="ascii")
        for file in (full, standalone, checksums):
            (destination / file.name).write_bytes(file.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    build(parser.parse_args().output)


if __name__ == "__main__":
    main()
