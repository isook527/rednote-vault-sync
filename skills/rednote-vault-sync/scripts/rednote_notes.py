"""Shared reading and Markdown handling for RedNote notes."""

import json
import re
from rednote_files import read_text, valid_source_id

QUERY_SECRET = re.compile(r"(?i)(?:xsec_token|access_token|refresh_token|api_key|session_id)=[^\s&#)\]<>]+")
ACTIVE_HTML = re.compile(
    r"<(?=/?(?:script|style|iframe|object|embed|img|svg|math|link|base|form|input|button|"
    r"video|audio|source|track|a|meta|canvas|template|textarea)\b|!--|"
    r"[A-Za-z][\w:-]*\s[^>\n]*\bon\w+\s*=)",
    re.I,
)
INLINE_HASHTAG = re.compile(r"(?<![\w\\#/:?&=])#(?=[\w/-]*[^\W\d]|[\w/-]*_)", re.UNICODE)
ACTIVE_MARKDOWN = re.compile(r"!\[(?=\[|[^\]\n]*\][(\[])")
ACTIVE_LINK = re.compile(r"(?i)(\]\(\s*<?)(?:javascript|vbscript|data|obsidian|file)\s*:")


def legacy_frontmatter(text):
    if not text.startswith("---\n"):
        return {}, text
    header, separator, tail = text[4:].partition("\n---\n")
    if not separator:
        return {}, text
    fields = {}
    tags = []
    reading_tags = False
    for line in header.splitlines():
        entry = re.fullmatch(r"\s+-\s+(.+)", line)
        if reading_tags and entry:
            value = legacy_scalar(entry.group(1))
            if value:
                tags.append(value)
            continue
        reading_tags = line.strip() == "tags:"
        pair = re.fullmatch(r"([A-Za-z]+):\s*(.*)", line)
        if pair:
            key, value = pair.groups()
            if key == "tags" and value.startswith("["):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    parsed = []
                if isinstance(parsed, list):
                    tags.extend(tag for tag in parsed if isinstance(tag, str))
            else:
                fields[key] = legacy_scalar(value)
    fields["tags"] = tags
    return fields, tail


def legacy_scalar(value):
    if value.startswith('"'):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ""
    elif len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value if isinstance(value, str) else ""


def clean_text(value):
    value = "".join(c for c in value if ord(c) >= 32 or c in "\n\t")
    return QUERY_SECRET.sub("[已移除链接参数]", value)


def safe_markdown(value, keep_hashtags=False):
    value = clean_text(value)
    value = ACTIVE_HTML.sub("&lt;", value)
    value = ACTIVE_MARKDOWN.sub("&#33;&#91;", value)
    value = ACTIVE_LINK.sub(lambda match: "&#93;(" + match[0][2:], value)
    return value if keep_hashtags else INLINE_HASHTAG.sub("&#35;", value)


def note_fields(path):
    fields, _ = legacy_frontmatter(read_text(path))
    return fields


def scan_notes(directory):
    notes = {}
    for note in sorted(directory.glob("*.md")):
        if note.is_symlink() or not note.is_file():
            continue
        try:
            fields = note_fields(note)
        except (OSError, UnicodeError):
            continue
        source_id = fields.get("sourceId", "")
        if fields.get("sourcePlatform") == "rednote" and valid_source_id(source_id):
            notes.setdefault(source_id, []).append(note)
    return notes
