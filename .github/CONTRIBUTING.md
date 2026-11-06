# 参与贡献

## 报告问题

提 Issue 时请写清楚：

- MCEdit 的版本（窗口标题栏里有），以及 Minecraft 版本；
- 用了哪些模组，世界里有没有模组方块；
- 一步步的操作过程：点了哪些按钮、按了哪些键；
- 日志：`MCEdit 2 Files/mcedit2.log`。日志较长时，请作为附件上传。

不要上传含有他人作品的方块包或 Minecraft 客户端文件。

## 提交代码

开发环境、测试和打包方法见 [DEVELOPMENT.md](../DEVELOPMENT.md)。提交 Pull Request 前，请先跑一遍测试。

界面文字改动后，请运行 `scripts/update_translations.py`，并补上中文翻译。

## 翻译

中文翻译在 `src/mcedit2/i18n/zh_CN.ts`，修改方法见 [DEVELOPMENT.md](../DEVELOPMENT.md#translations)。
