"""Read, verify, and publish files inside a RedNote Vault."""

import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

RESERVED = {".obsidian", ".git", ".trash"}


class InputError(ValueError):
    """An actionable error without input contents or credentials."""


class ConflictError(InputError):
    """The existing note cannot be changed without review."""


def relative_parts(value):
    if value.startswith('/') or any(c in value for c in '\\:<>"|?*[]') or any(ord(c) < 32 for c in value):
        raise InputError("路径必须使用安全的相对路径和 / 分隔符。")
    parts = PurePosixPath(value).parts
    if not parts or any(p in {"/", ".."} or p.lower() in RESERVED for p in parts):
        raise InputError("路径越界或进入保留目录。")
    return parts


def checked_path(root, parts):
    path = root
    for index, part in enumerate(parts):
        path = path / part
        if path.is_symlink():
            raise InputError("输入与输出均不支持符号链接。")
        if index < len(parts) - 1 and path.exists() and not path.is_dir():
            raise InputError("路径中的文件夹位置被文件占用。")
    return path


def read_text(path):
    return path.read_text(encoding="utf-8-sig")


def text_field(data, key):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise InputError(f"{key} 必须是字符串。")
    return value.strip()


def valid_source_id(value):
    return bool(re.fullmatch(r"(?:share-)?[0-9a-f]{24}", value))


def sha256_file(path, max_bytes=None):
    result = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise InputError("附件在读取期间增长，请重新预览。")
            result.update(chunk)
    return result.hexdigest()


def publish_file(dest, source=None, content=None, expected_digest=None, expected_note=None, expected_size=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".rednote-", delete=False) as handle:
            temp = Path(handle.name)
            if source:
                with source.open("rb") as media:
                    if expected_size is None:
                        shutil.copyfileobj(media, handle)
                    else:
                        remaining = expected_size
                        while remaining:
                            chunk = media.read(min(1024 * 1024, remaining))
                            if not chunk:
                                break
                            handle.write(chunk)
                            remaining -= len(chunk)
                        if remaining or media.read(1):
                            raise InputError("附件在复制期间大小变化，请重新预览。")
            else:
                handle.write(content.encode("utf-8"))
        if expected_digest and sha256_file(temp) != expected_digest:
            raise InputError("附件在导入期间发生变化，请重新运行。")
        if expected_note is not None:
            if dest.is_symlink() or not dest.is_file() or dest.read_bytes() != expected_note:
                raise ConflictError("笔记在导入期间发生变化，请重新预览。")
            os.chmod(temp, dest.stat().st_mode & 0o777)
            os.replace(temp, dest)
            return True
        try:
            os.link(temp, dest)
            return True
        except FileExistsError:
            if dest.is_symlink() or not dest.is_file():
                raise InputError("输出路径冲突或目标是符号链接。")
            if expected_digest and sha256_file(dest) != expected_digest:
                raise InputError("目标附件存在但内容不一致。")
            return False
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


@contextmanager
def enrichment_lock(note):
    directory = Path(tempfile.gettempdir()) / "rednote-vault-sync-locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise ConflictError("临时锁目录路径已被占用。")
    if hasattr(os, "getuid") and directory.stat().st_uid != os.getuid():
        raise ConflictError("临时锁目录不属于当前用户。")
    key = hashlib.sha256(os.fsencode(note.parent.resolve())).hexdigest()
    lock = checked_path(directory, (key + ".lock",))
    if lock.exists() and not lock.is_file():
        raise ConflictError("补全锁路径已被占用。")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ConflictError("另一个导入正在补全笔记，请稍后重试。") from error
        yield


def user_error(error):
    if isinstance(error, InputError):
        return str(error)
    if isinstance(error, json.JSONDecodeError):
        return f"JSON 无法解析，检查第 {error.lineno} 行。"
    if isinstance(error, UnicodeError):
        return "输入须保存为 UTF-8 文本。"
    return "无法读取或写入文件，请检查文件类型、权限、磁盘空间和文件系统。"


def report_text(report):
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
