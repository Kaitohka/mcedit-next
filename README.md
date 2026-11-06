# McEdit 重置

<img src="mcediticon.ico" width="64" align="right" alt="MCEdit icon">

基于 [MCEdit 2](https://github.com/mcedit/mcedit2) 的重置版本。它把原项目移植到 Python 3.12 和 Qt 6，用来打开、编辑和保存 Minecraft 1.21.1 的世界。

> 本项目处于测试阶段，编辑前请先备份世界。
>
> 非 Minecraft 官方产品，与 Mojang、Microsoft 无关。

## 功能

- **打开 1.21.1 世界。** 方块使用游戏本身的模型和贴图显示，支持 -64 到 320 的高度。
- **编辑并保存。** 支持选区、填充、替换、撤销，改完后直接存回存档。
- **显示模组方块。** 配套的 NeoForge 客户端模组会导出“方块包”。装上方块包后，模组方块按真实模型显示，不再是问号。具体见 [exporter/README.md](exporter/README.md)。
- **显示实体与生物。** 可以看到生物、箱子、命令方块等，模组实体会用单独的颜色框出。
- **中文界面与新主题。** 界面支持中文和英文，主题有现代深色、现代浅色和经典三种。

## 移植了什么

| | 原 MCEdit 2.0 alpha | 本项目 |
|---|---|---|
| 运行环境 | Python 2.7、PySide 1（Qt 4） | Python 3.12、PySide6（Qt 6） |
| 世界格式 | 到 Minecraft 1.12 | 另外支持 Minecraft 1.21、1.21.1 |
| 模组方块 | 未知方块显示为问号 | 通过方块包显示，方块包由 NeoForge 21.1.x 导出模组生成 |

1.13 以后的区块格式按方块状态调色板存储，高度范围也有变化，方块 ID 和实体 ID 改成了带命名空间的写法。本项目在读取层把这些转换成旧代码能处理的形式，所以大部分编辑功能可以沿用。另外，Qt 6 删除了 `QGLWidget`，绘图窗口已经改成 `QOpenGLWidget`。

## 下载与使用

1. 在 [Releases](../../releases) 下载最新的 Windows 压缩包，解压后运行 `mcedit2.exe`。
2. 首次启动时，MCEdit 需要 Minecraft 客户端来读取方块模型和贴图。它会在本机的 `.minecraft` 里查找；找不到时，会询问是否从 Mojang 下载 1.21.1 客户端（约 30 MB）。
3. 模组方块的显示方法，见 [exporter/README.md](exporter/README.md)。

压缩包不含 Minecraft 客户端文件，客户端文件归 Mojang 所有。导出模组生成的方块包含有各模组作者的文件，请只自己使用，不要公开发布。

## 参与

- **报告问题：** 提 [Issue](../../issues)，写清楚 Minecraft 版本、用了哪些模组、操作步骤，并附上 `MCEdit 2 Files/mcedit2.log`。
- **提交代码：** 提 [Pull Request](../../pulls)。开发环境的搭建、测试和打包方法，见 [DEVELOPMENT.md](DEVELOPMENT.md)。
- **文档与翻译：** 中文翻译在 `src/mcedit2/i18n/zh_CN.ts`，修改方法见 [DEVELOPMENT.md](DEVELOPMENT.md#translations)。

## 许可证

BSD 3-Clause，沿用原项目的 [LICENSE.md](LICENSE.md)。原项目版权归 David Rio Vierra 所有。

---

**English:** McEdit Reset is a fork of MCEdit 2, ported to Python 3.12 and Qt 6, that opens, edits and saves Minecraft 1.21.1 worlds and shows mod blocks through block packs from a NeoForge 1.21.1 companion mod. BSD 3-Clause licensed. Not an official Minecraft product; not approved by or associated with Mojang or Microsoft.
