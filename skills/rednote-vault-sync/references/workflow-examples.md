# 端到端示例

命令从 Skill 根目录运行。先准备已有的 Vault 和 UTF-8 输入文件。Windows 上将 python3 改为 py -3，并按实际终端习惯引号路径。

## 1. 批量归档分享链接

用户说「把分享.txt 里的收藏存进 Obsidian，先看看会生成什么。」

~~~bash
python3 scripts/import_rednote.py --format shares --source 分享.txt --vault Vault \
  --filename-style title --index --dry-run --output table
~~~

假设预览有三条新收藏，回复「拟新增 3 篇。目标在 Vault 的小红书收藏目录。」收到写入授权后运行同一命令，去掉 --dry-run，加 --report-file import.json。

## 2. 给同一篇补正文和图片

用户说「这两张图和正文属于昨天存的窗边阅读角，补进原来的笔记。」

~~~bash
python3 scripts/import_rednote.py --format shares --source 阅读角分享.txt --vault Vault \
  --body-file 正文.txt --attach 阅读角.png --attach 灯光.png --enrich --dry-run
~~~

核对拟补全路径，用户已授权写入时，去掉 --dry-run 并加 --report-file enrich.json。成功后可回复「补全 1 篇，追加 2 张图，已有批注保留。」如果报告冲突，说明冲突原因并保留原文件。

## 3. 撤销一批误导入

用户说「撤回刚才 import.json 记录的那批归档，先让我看看会删哪些。」

~~~bash
python3 scripts/import_rednote.py --vault Vault --undo import.json --dry-run --output table
~~~

回复拟删除的路径和被保留的项目。用户授权后去掉 --dry-run。例「已撤销 3 个文件。你编辑过的笔记和仍被引用的附件继续保留。」
