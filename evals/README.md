# RedNote Vault Sync 行为评估

评估材料使用虚构链接、笔记和项目自制图片。每个用例独立运行，在临时目录中核对实际文件、命令输出和给用户的回复。

~~~bash
python3 evals/prepare.py /tmp/rednote-v130-eval
~~~

可用 --release 指向一个解包后的历史发行版本。以全新的目录再运行一次，即可获得第二组同样的输入。Windows 将 python3 改为 py -3，并选择一个未使用的临时路径。

1. 给评估 agent 对应用例的 prompt.md 和生成的输入目录。有 Skill 时给它当前发行版的 Skill，无 Skill 时只提供输入。不要提供 expected.md。
2. 以 expected.md 的全部条件评分。按用例记通过或未通过，并区分实跑和只读判断。
3. 独立给评估者 triggers.md 中的 20 条文本，逐条判断宿主是否应该选此 Skill。仅看描述得出的路由判断记为模拟，宿主自动选择需另行观测。
4. 将版本、环境、条件、结果和缺口写入 results/。
