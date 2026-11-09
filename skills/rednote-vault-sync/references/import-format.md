# 导入格式

所有文本输入保存为 UTF-8。目标 Vault 必须是已经存在的文件夹。命令中的脚本路径以实际安装位置为准。Windows 将 python3 改为 py -3。

| 输入 | 参数 | 能生成什么 |
| --- | --- | --- |
| 粘贴自小红书的分享文案或链接 | --format shares | 标题、原帖链接、可选正文及本地附件。可粘贴多条分享文案 |
| 自己整理的 JSON 清单与本地文件 | --format json | 笔记、作者、标签、收藏夹，以及可播放或查看的本地附件 |
| Vault 内的收件箱 | --format inbox | 归档新条目并把成功项移入已处理 |
| RedNote Vault Sync 旧笔记 | --format legacy | 从旧 Markdown 读取正文和原帖信息，复制它引用的本地图片和视频 |

## 分享文案和本地附件

将分享文案复制到 UTF-8 文本。标题和链接可以在同一行，也可以把标题放在链接上一行。多篇用空行分开。

~~~bash
python3 scripts/import_rednote.py --format shares --source 分享.txt --vault '我的 Vault' --dry-run
~~~

已知附件属于哪篇时，将该篇的分享文案单独保存，逐个添加 --attach。--body-file 接受 UTF-8 正文。两者的路径以当前工作目录为起点，也可用绝对路径。带多个不同链接的输入会在报告中列出未归属的附件序号，链接仍可单独归档。

~~~bash
python3 scripts/import_rednote.py --format shares --source 单篇分享.txt --vault '我的 Vault' \\
  --enrich --body-file 正文.txt --attach 图片一.png --attach 图片二.png --dry-run
~~~

预览核对后去掉 --dry-run。--enrich 对已有笔记追加资料。使用同一目标目录、原帖 ID 或原来归档的短链。找不到原笔记时会报告冲突。已知一个短链和直链对应同一篇时，用首次归档的链接或提供本地短链映射。

正文缺失时可补入，已有正文和本次不同会报冲突。已引用的附件按内容哈希跳过，新附件会追加。原笔记的自定义属性和手写批注保留。补全时附件计数与笔记不一致会报冲突。写入前保存 Obsidian 中打开的编辑，并在补全期间避免同时编辑该文件。

## JSON 清单

JSON 文件必须是 {"version": 1, "items": [...]}。每条记录需有 url 或 24 位十六进制 id，其余字段均可省略。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| url | 字符串 | 小红书原帖或分享短链。保留链接主体，丢弃查询参数和片段 |
| id | 字符串 | 已知的笔记 ID。和直链同时给出时必须一致。和短链同时给出时按 ID 去重 |
| title、author、collection、body | 字符串 | 标题、原作者、收藏夹和正文 |
| tags | 字符串数组 | 例如 ["空间", "设计/灯光"]。含空格和 Obsidian 不接受的标点会被跳过 |
| media | 字符串数组 | 相对 JSON 文件所在目录的本地文件名，例如 ["media/灯光.png"] |

图片支持 JPG、PNG、GIF、WebP、AVIF、BMP。视频支持 MKV、MOV、MP4、OGV、WebM，能否播放取决于设备编解码器，见 [Obsidian 支持的文件格式](https://obsidian.md/help/file-formats)。附件需要是非空的本地文件，缺失时该条报错，修正后可以重试。正文保留常见标点，仅把能触发嵌入、活动内容或破坏正文边界的标记变成文本。附件通过 media 声明。正文中的 #标签默认显示为普通文本。使用 --keep-hashtags 时保留标签语法。

旧插件格式见 [旧库迁移](legacy-migration.md)。

## 文件名、索引与短链映射

使用 --filename-style id 时笔记路径是 `<Vault 根目录>/小红书收藏/笔记/<ID>.md`。新笔记默认采用 --filename-style title，生成「标题 - ID后六位.md」。遇到同名时使用完整 ID。下次导入在同一笔记目录按 sourceId 识别已有文件，包括改名后的文件。

导入默认维护静态索引.md 和收藏.base。--no-index 可跳过。编辑过的索引和收藏视图会被保留，脚本给出警告。Obsidian 中可直接打开索引，启用 Bases 时也可查看三个收藏视图。

--link-map 接受用户确认的 JSON 对照表。键是短链，值是原帖 ID。示例中的链接和 ID 是虚构的。

~~~json
{"https://xhslink.com/a/KornviaDemo": "000000000000000000000101"}
~~~

同一映射可把两种链接对应到已经存在的同一篇笔记。若短链和原帖 ID 各有一篇，报告冲突，请先人工核对。没有对应关系时，短链按其本身去重。少数需要额外访问参数的原帖可能要在小红书中重新打开。

## 报告、附件上限与撤销

- --dry-run 列出标题、目标路径、正文字符数、拟写入及跳过附件数和原因。缺少 .obsidian 时报告警告。若给出 --report-file，预览也可把 JSON 保存到指定的外部报告路径。
- 默认 stdout 为 JSON。--output table 输出人类可读表格，--report-file 可指定 JSON 路径。有实际写入时，默认报告自动保存在 Vault 的小红书收藏/.导入记录/，stdout 中的 report_path 给出相对路径。--progress 把处理进度写到 stderr，--json-errors 让命令行参数错误和致命输入错误也带可解析的 error 与 code。若同时开启进度，读取 stderr 最后一行的错误对象。
- --max-file-bytes 限制单个附件，--max-total-bytes 限制一次运行处理的去重后附件字节数。0 表示不限。超限记录失败，其他合规条目继续处理。
- 导入报告的 created_files 列出新建文件及 SHA-256。导入完成会在 Vault 内写一份对应的撤销记录，路径见 receipt_path。撤销要求原 Vault 及原 JSON 报告。复制报告也可以，需保留 receipt 部分。
- 先运行 --vault Vault --undo 导入报告.json --dry-run 核对路径。确定后去掉 --dry-run。撤销会恢复未被再次修改的 AI 区块和收件箱，并移除那次新建、未改动的文件，保留仍被引用的附件及手写过的笔记。撤销删除笔记后，如需刷新自动索引，会在报告里列出。已有笔记中的补全内容、附件引用和原来就存在的文件需要手工整理。
- 返回码 0 表示完成，1 表示失败、冲突、未归属资料或撤销保留项需要查看，2 表示命令或整体输入无效。
