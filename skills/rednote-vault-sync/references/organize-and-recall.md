# AI 整理、回忆与收件箱

在 Skill 目录运行下列命令。Windows 将 `python3` 改为 `py -3`。

## AI 整理

让 agent 根据本地正文和图片制作 UTF-8 JSON。每篇使用原笔记的 `sourceId`。摘要、要点和转写应能在输入找到依据。相关笔记用 Vault 相对路径，最多三篇。

```json
{
  "version": 1,
  "items": [{
    "id": "000000000000000000000101",
    "summary": "把照明留在窗边阅读区。",
    "points": ["搭配木色家具", "选择暖白灯", "留意玻璃反光"],
    "transcript": "把照明留给阅读区。",
    "transcript_source": ["reading-corner.png"],
    "tags": ["空间", "灯光"],
    "related": ["小红书收藏/笔记/居家布光的三个记录点 - 000102"]
  }]
}
```

```bash
python3 scripts/import_rednote.py --vault "我的 Vault" --annotate annotations.json --dry-run
```

看完摘要、标签及冲突后，授权时去掉 `--dry-run`。整理区块写在 `## 我的笔记` 之前，手写内容始终在笔记末尾。重复整理会替换脚本自己的区块。手改过的区块会报告冲突。不存在的关联笔记和不符合 Obsidian 规则的标签会跳过，写入该篇的 `warnings`，其余内容照常写入；指向 Vault 外的路径仍会使该篇失败。把 `warnings` 转告用户。撤销报告将撤回未被再次改动的整理区块和对应标签改动。

## 回忆

```bash
python3 scripts/query_rednote.py --vault "我的 Vault" --text "布光 窗边" --status 未读 --limit 10 --output table
```

多个关键词用空格分开，全部命中才返回。也可使用 `--tag`、`--collection`、`--author`、`--since YYYY-MM-DD` 或 `--folder`。默认最多 20 条，默认输出 JSON，包含 Vault 相对路径、双链、匹配片段和属性。检索范围是标题、正文、整理、图片转写和手写笔记，不含「相关笔记」链接列表和页面固定标题；片段已去掉 Markdown 符号。脚本只读。

## 收件箱

```bash
python3 scripts/import_rednote.py --vault "我的 Vault" --format inbox --init-inbox --dry-run
python3 scripts/import_rednote.py --vault "我的 Vault" --format inbox --dry-run
```

第一条只用于还没有收件箱的 Vault。获同意后去掉 `--dry-run` 创建。在 Obsidian 打开 `小红书收藏/收件箱.md`，每行粘贴一条分享文案，放在 `## 已处理` 以上。第二条预览已有收件箱，获同意后去掉 `--dry-run` 移动成功的条目。失败条目留在原处，下方加一行 `> [!warning] 未导入：原因` 提示框，在 Obsidian 阅读视图中也能看到。修正后再处理时，旧提示会自动清除。导入期间发现收件箱发生变化时保留用户的编辑，下次可复用已归档的笔记。撤销也会检查收件箱之后是否有编辑。
