"""Describe a reversible local edit without putting old note text in the report."""

import hashlib
import json

from rednote_files import ConflictError, InputError, publish_file


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def describe_change(path, kind, original, updated, patches):
    encoded = json.dumps(patches, ensure_ascii=False, separators=(",", ":"))
    return {
        "path": path.as_posix(), "kind": kind,
        "before_sha256": digest(original), "after_sha256": digest(updated),
        "patch_sha256": digest(encoded),
    }


def apply_patches(text, patches, reverse=False):
    for before, after in (reversed(patches) if reverse else patches):
        old, new = (after, before) if reverse else (before, after)
        if old:
            if text.count(old) != 1:
                raise ConflictError("受控内容已改变，无法安全应用这次修改。")
            text = text.replace(old, new, 1)
        elif reverse:
            raise InputError("撤销记录中的受控内容为空。")
        else:
            text += new
    return text


def restore_change(path, descriptor, patches, dry_run):
    raw = path.read_bytes()
    current = raw.decode("utf-8")
    encoded = json.dumps(patches, ensure_ascii=False, separators=(",", ":"))
    if digest(encoded) != descriptor["patch_sha256"]:
        raise InputError("修改记录的校验值不一致。")
    current_hash = digest(current)
    if descriptor["kind"] == "ai":
        newline = "\r\n" if "\r\n" in current else "\n"

        def adapt(value):
            return value.replace("\r\n", "\n").replace("\n", newline)

        patches = [[before, after] if after in current else [adapt(before), adapt(after)]
                   for before, after in patches]
    marker = "<!-- rednote:ai:start -->"
    # A patch that inserted a block counts as undone only once no managed block remains.
    inserted = any(marker in after and marker not in before for before, after in patches)
    if current_hash == descriptor["before_sha256"] or (
        descriptor["kind"] == "ai" and (not inserted or marker not in current) and all(
            (current.count(before) == 1 if before else "<!-- rednote:ai:start -->" not in current)
            and after not in current
            for before, after in patches
        )
    ):
        return "already_restored"
    if descriptor["kind"] == "inbox" and current_hash != descriptor["after_sha256"]:
        raise ConflictError("收件箱在导入后有新修改。")
    updated = apply_patches(current, patches, reverse=True)
    if not dry_run:
        publish_file(path, content=updated, expected_note=raw)
    return "would_restore" if dry_run else "restored"
