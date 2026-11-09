# RedNote Vault Sync

**Kornvia · MIT**

把你已经拿到的小红书分享文案、收藏清单和本地图片、视频整理成 Obsidian 笔记。可以先存链接，以后再给同一篇补正文和附件。

![从分享文案、JSON 清单或旧库导入的三种路径。](docs/01-inputs.png)

## 安装与运行

将 [skills/rednote-vault-sync](skills/rednote-vault-sync/) 整个文件夹复制到项目的 .agents/skills/，然后新开 Codex 会话。可以直接对 Codex 说「用 $rednote-vault-sync 预览这些小红书分享文案和附件，目标是我的 Obsidian Vault」。

脚本运行需要 Python 3.9 或更新版本，使用标准库。下面的命令从仓库或交付包根目录运行。Windows 将 python3 改为 py -3。

## 复现示例 Vault

先创建一个空目录 demo-vault。没有 Obsidian 配置时，脚本会提示核对 Vault 路径。示例标题、链接和媒体为演示资料。

~~~bash
mkdir -p demo-vault
python3 skills/rednote-vault-sync/scripts/import_rednote.py --format json --source examples/manifest.json --vault demo-vault --filename-style title --index --dry-run --output table
python3 skills/rednote-vault-sync/scripts/import_rednote.py --format json --source examples/manifest.json --vault demo-vault --filename-style title --index --report-file demo-import.json
~~~

打开 demo-vault 或包内的示例Vault，进入「小红书收藏 → 索引」。点击示例笔记可以查看正文、属性、PNG 和 MP4。默认文件名仍可用原帖 ID。标题模式适合从文件列表浏览。

也可以用 examples/shares.txt 做链接归档，将 --format 改为 shares 并替换 --source 路径。已知附件属于单篇分享时可用 --attach 和 --body-file，已归档的原笔记使用 --enrich。

## 查看与撤销

~~~bash
python3 skills/rednote-vault-sync/scripts/import_rednote.py --vault demo-vault --undo demo-import.json --dry-run --output table
~~~

核对清单后去掉 --dry-run，可撤销该次新增且未编辑的文件。已有笔记的补全、编辑过的文件和仍被引用的附件保留。

[输入格式与全部参数](skills/rednote-vault-sync/references/import-format.md) · [工作流示例](skills/rednote-vault-sync/references/workflow-examples.md) · [更新记录](CHANGELOG.md)

## 测试

开发环境安装 pytest 后运行以下命令。脚本本身只依赖 Python 标准库。

~~~bash
python3 -m pytest -q
~~~
