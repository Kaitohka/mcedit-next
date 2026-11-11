# McEdit Next

<img src="mcediticon.ico" width="64" align="right" alt="MCEdit icon">

McEdit Next 是 [MCEdit 2](https://github.com/mcedit/mcedit2) 的重置版本。它把原项目移植到 Python 3.12 和 Qt 6，能打开、编辑和保存 Minecraft 1.7.10 到 26.3 每个正式版的世界。

> 本项目处于测试阶段，编辑前请先备份世界。
>
> 非 Minecraft 官方产品，与 Mojang、Microsoft 无关。

## 功能

- **打开 1.7.10 到 26.3 的世界。** 每个正式版都能打开，方块用该版本游戏自己的模型和贴图显示。老世界里游戏还没升级过的区块也能读。
- **编辑并保存。** 支持选区、填充、替换、撤销，改完后直接存回存档。每个区块按它原来的格式写回，每个版本都用该版本自己的游戏验证过：游戏加载 McEdit 保存的区块、重新保存后，写入的方块都保持不变。
- **显示模组方块。** 配套的客户端模组会导出“方块包”。装上方块包后，模组方块按游戏里的样子显示，不再是问号。一个 jar 支持 Forge、NeoForge、Fabric 共 124 个“版本 × 加载器”组合。具体见 [exporter/README.md](exporter/README.md)。
- **连接纹理和代码画的方块。** 玻璃连成一片这类连接纹理（CTM 模组、Fusion、OptiFine / Continuity）会照游戏的规则画。模型文件说不清的方块，如大型机器、箱子，直接用游戏画出的面。
- **显示实体与生物。** 可以看到生物、箱子、命令方块等，模组实体会用单独的颜色框出。
- **中文界面与新主题。** 界面支持中文和英文，主题有现代深色、现代浅色和经典三种。

## 移植了什么

| | 原 MCEdit 2.0 alpha | 本项目 |
|---|---|---|
| 运行环境 | Python 2.7、PySide 1（Qt 4） | Python 3.12、PySide6（Qt 6） |
| 世界格式 | 到 Minecraft 1.12 | 1.7.10 到 26.3 的每个正式版 |
| 模组方块 | 未知方块显示为问号 | 通过方块包显示，方块包由导出模组生成（Forge、NeoForge、Fabric，1.7.10 到 26.3） |

1.13 以后的区块格式按方块状态调色板存储，高度范围也有变化，方块 ID 和实体 ID 改成了带命名空间的写法；之后 1.16、1.18、26.1、26.3 又各改过一次存储方式。本项目在读取层把这些转换成旧代码能处理的形式，所以大部分编辑功能可以沿用。每个版本的方块列表由该版本的数据生成器导出，见 [PLAN_CROSS_VERSION.md](PLAN_CROSS_VERSION.md)。另外，Qt 6 删除了 `QGLWidget`，绘图窗口已经改成 `QOpenGLWidget`。

首发之后每次升级做了什么，见 [CHANGELOG.md](CHANGELOG.md)。

## 下载与使用

1. 在 [Releases](../../releases) 下载最新的 Windows 压缩包，解压后运行 `mcedit2.exe`。压缩包可能比源码旧，它包含哪些升级见 [CHANGELOG.md](CHANGELOG.md)；要用最新的功能，按 [DEVELOPMENT.md](DEVELOPMENT.md) 从源码运行。
2. 首次启动时，MCEdit 需要 Minecraft 客户端来读取方块模型和贴图。它会在本机的 `.minecraft` 里查找；找不到时，会询问是否从 Mojang 下载这个世界所用版本的客户端（约 20–60 MB）。
3. 模组方块的显示方法，见 [exporter/README.md](exporter/README.md)。

压缩包不含 Minecraft 客户端文件，客户端文件归 Mojang 所有。导出模组生成的方块包含有各模组作者的文件，请只自己使用，不要公开发布。

## 参与

- **报告问题：** 提 [Issue](../../issues)，写清楚 Minecraft 版本、用了哪些模组、操作步骤，并附上 `MCEdit 2 Files/mcedit2.log`。
- **提交代码：** 提 [Pull Request](../../pulls)。开发环境的搭建、测试和打包方法，见 [DEVELOPMENT.md](DEVELOPMENT.md)。
- **文档与翻译：** 中文翻译在 `src/mcedit2/i18n/zh_CN.ts`，修改方法见 [DEVELOPMENT.md](DEVELOPMENT.md#translations)。

## 许可证

BSD 3-Clause，沿用原项目的 [LICENSE.md](LICENSE.md)。原项目版权归 David Rio Vierra 所有。

---

**English:** McEdit Next is a fork of MCEdit 2, ported to Python 3.12 and Qt 6, that opens, edits and saves worlds from every Minecraft release from 1.7.10 to 26.3, and shows mod blocks, with connected textures, through block packs from a companion mod for Forge, NeoForge and Fabric. Changes since the first release are in [CHANGELOG.md](CHANGELOG.md). BSD 3-Clause licensed. Not an official Minecraft product; not approved by or associated with Mojang or Microsoft.
