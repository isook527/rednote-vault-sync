# 旧库迁移

用 --legacy-vault 指定旧库根目录，--source 指定其中的单篇 Markdown 或旧收藏子目录。可导入同一个 Vault 下的另一个目录，只要输出目录不在扫描范围内。

脚本识别 sourcePlugin: rednote-vault-sync、sourcePlatform: rednote 和 sourceId 属性，跳过媒体索引页。它读取旧版 Vault 相对的 ![[媒体路径]]，也支持旧笔记旁的本地媒体。它复制支持的图片和视频，统计旧笔记中的远程图片链接，并以文本保留不支持的本地嵌入。请保留旧 Vault 的原始目录结构。旧库的账户字段、其他自定义属性和插件设置不迁移。
