# Vendor Source — OrcaSlicer 机器资源源插件

<div align="center">

[English](README.md) · **简体中文**

</div>

一个 OrcaSlicer 的纯 Python 插件，用于把「尚未合入主程序的机器（vendor）profile」从任意 git 仓库/分支下载并合并到本地。重启后，新机器即可出现在打印机下拉框中——不用装 nightly，也不用等下一个稳定版。

解决的实际问题（对应 [OrcaSlicer Issue #15505](https://github.com/OrcaSlicer/OrcaSlicer/issues/15505)）：新打印机已经合入 `main`/dev 分支，但稳定版还没发布——你不想装 nightly，却想用上新机器。

> 本目录是一个**独立的 git 仓库**，与 OrcaSlicer 主仓库无关。你可以单独把它上传到 GitHub / Gitee 等平台分发。

---

## 目录

- [Vendor Source — OrcaSlicer 机器资源源插件](#vendor-source--orcaslicer-机器资源源插件)
  - [目录](#目录)
  - [1. 这是什么](#1-这是什么)
  - [2. 两种典型用法](#2-两种典型用法)
    - [2.1 把官方仓库当作源，提前用上 dev 里的新机器](#21-把官方仓库当作源提前用上-dev-里的新机器)
    - [2.2 精简机型仓库（自己做机器分发时的推荐方式）](#22-精简机型仓库自己做机器分发时的推荐方式)
  - [3. 安装插件](#3-安装插件)
  - [4. 使用方法](#4-使用方法)
  - [5. 真实示例——`examples/` 里的完整机型仓库](#5-真实示例examples-里的完整机型仓库)
  - [6. 如何创建自己的「机型仓库」](#6-如何创建自己的机型仓库)
  - [7. 限制与注意事项](#7-限制与注意事项)
  - [8. 仓库结构](#8-仓库结构)

---

## 1. 这是什么

OrcaSlicer 的机器资源以「厂商（vendor）」为单位，位于源码树的 `resources/profiles/` 下，例如：

```text
resources/profiles/
├── Bambulab.json          # 厂商清单（name、version、机器列表等）
├── Bambulab/
│   ├── machine/           # 机器型号 + 各喷嘴变体的机器 preset
│   ├── process/           # 打印参数
│   └── filament/          # 耗材
├── Voron.json
├── Voron/
└── …
```

本插件能让你：

1. 配置一个或多个「源」（每个源 = git 仓库 URL + 分支 + 子路径，默认子路径为 `resources/profiles`）；
2. 用纯 Python 库 [dulwich](https://github.com/jelmer/dulwich) 拉取该仓库——不需要安装系统 `git`；
3. 把子路径下找到的厂商 profile 拷贝进本地 OrcaSlicer 的 `<数据目录>/system/`；
4. 提示你重启——下次启动 OrcaSlicer 扫描 `system/`，新机器就出现在下拉框里。

**零主程序改动**：插件只用 OrcaSlicer 已暴露的只读 API（`preset_bundle()`、`plugin.storage()`、`ui.create_window()` 等），合并在用户数据目录内完成，不修改程序本身。

---

## 2. 两种典型用法

两种场景本质相同——只是「源」指向的 git 仓库不同。

### 2.1 把官方仓库当作源，提前用上 dev 里的新机器

对应 issue #15505 的原始场景：你想要的机器已经在 OrcaSlicer 的 `main` 分支，但最新稳定版还没带上。

| 字段 | 值 |
|---|---|
| 名称 | `upstream` |
| Git URL | `https://github.com/OrcaSlicer/OrcaSlicer.git` |
| 分支 | `main` |
| 子路径 | `resources/profiles`（默认） |

同步 → 重启后，`main` 分支里新合入的机器就能在稳定版上用了。

> ⚠️ **注意**：一个源指向的是**整棵** profiles 树。同步官方仓库意味着会用它那个分支的版本**重新安装所有内置厂商**。在同一发布线内通常没问题——相当于「在稳定版二进制上跑 dev 的 profile 集合」。如果那个分支比你的构建旧，或你只关心某一台机器，请改用 2.2。

### 2.2 精简机型仓库（自己做机器分发时的推荐方式）

只把**你自己的厂商**放进一个小 git 仓库：

```text
my-machines/                 # 任意仓库名
└── resources/profiles/
    ├── MyVendor.json        # 厂商清单（必需）
    └── MyVendor/            # machine/（必需）+ 可选 process/、filament/
```

源指向它即可。同步时**只会碰你的厂商**，内置厂商不受影响；如果 `MyVendor` 是全新厂商名，也没有版本冲突的顾虑。本仓库附带一个完整真实示例——见 [§5](#5-真实示例examples-里的完整机型仓库)。

---

## 3. 安装插件

插件提供**两种等价形式**，二选一安装，**不要同时装**：

| 形式 | 文件 | 说明 |
|---|---|---|
| Wheel | `dist/orca_vendor_source_plugin-<version>-py3-none-any.whl`（[目录](dist)） | 便于分发的构建产物：名称/版本/描述/依赖都来自 wheel，安装前宿主会校验包格式 |
| 单文件 | [`orca_vendor_source_plugin.py`](orca_vendor_source_plugin.py) | 源码形式：一个可读文件，无需打包 |

两者包含完全相同的模块、注册同一个 **Vendor Source** 能力；同时安装会加载两个插件并争用同一个 Python 模块名，请先删掉其中一个（删除 `<数据目录>/orca_plugins/` 下对应的文件夹）。

1. 打开 OrcaSlicer，进入「插件」对话框（顶部菜单 **Plugins** 或 **File → Plugins**）。
2. 打开 **Browse plugins** 下拉菜单，选择 **Install local plugin**，选中 `.whl`（或 `.py`）。
3. 在插件列表里找到该插件（**Vendor Source**），启用它的 Script 能力。
4. 在 **Speed Dial**（主界面快捷入口）里会出现 **Vendor Source** 动作，点击即可打开管理窗口。

> 首次加载时，OrcaSlicer 会用内置的 `uv` 自动安装唯一依赖 `dulwich`，需要联网，稍等几秒即可。

**分发插件**：直接给对方 `.whl` 即可——单个约 20 KB，对方无需任何构建步骤；它已提交在 `dist/` 下，且每次推送 `v*` 标签，CI 都会自动构建并挂到 [Releases](https://github.com/zFDT/orca-vendor-source-plugin/releases) 页面，用下载链接分发也行。对方只需 **Install local plugin** 选中它。

**重新打包**（wheel 是构建产物，不要直接改它）：

```bash
python -m pip install --upgrade build
python -m build --wheel                   # → dist/orca_vendor_source_plugin-<version>-py3-none-any.whl
python tools/verify_wheel.py dist/*.whl   # 与宿主同样的校验 + 桩模块加载测试
```

`pyproject.toml` 通过 `py-modules` 直接指向现有的 `orca_vendor_source_plugin.py`，因此该单文件始终是**唯一**事实来源，wheel 不复制任何代码。改版本号时要**同时**改两处（`pyproject.toml` 与 `.py` 顶部 PEP 723 的 `# version =`）；两者不一致时 `tools/verify_wheel.py`（以及 CI）会报错。

需要带脚本插件宿主（即带 Plugins 对话框）的 OrcaSlicer 版本。

---

## 4. 使用方法

1. 点击 Speed Dial 里的 **Vendor Source**，打开管理窗口。
2. **添加源**，填写：
   - 名称：随便起，如 `my-machines`；
   - Git URL：如 `https://github.com/你的账号/my-machines.git`（建议 HTTPS）；
   - 分支：如 `main`；
   - 子路径：默认 `resources/profiles`；只有当仓库把 profiles 放在别处时才改（例如本仓库的 `examples/…`，见下）。
   - 用户名 / 访问令牌（密码）：**可选**，仅私有仓库需要（见下）。
3. 点击 **同步**（或 **全部同步**）。
4. 首次同步写入系统目录时，OrcaSlicer 会弹出**文件访问授权**——请允许。
5. **重启 OrcaSlicer**，新机器会出现在「打印机」下拉框中。

**同步写了什么**：preset JSON 写入 `<数据目录>/system/`（OrcaSlicer 扫描 preset 的目录）；厂商目录里的二进制资源（`.stl` / `.svg` / `.png` 等——床模型、床贴图、喷嘴模型、机型缩略图）会**镜像**到 `<数据目录>/vendor/<厂商>/`，因为主程序解析这些资源时**优先**查该目录（找不到才回退到应用自带的 `resources/profiles/`）。这样只在仓库里的厂商也能保住床类资源。同步完请重启 OrcaSlicer。

> **关于本地路径**：`Git URL` 也可以填**本机某个 git 仓库的路径**（绝对路径或 `file://…`），用于本地测试而不必发布——但该目录**必须是 git 仓库**（先 `git init` + `git commit`，无需推送）。每次同步都会重新 clone，**只读取已提交的内容**：改了文件要先 `git commit`，再同步才会生效。普通（非 git）文件夹不是合法源，这是刻意的——git 源保证内容可复现、可追溯；如果你只是想本地改参数看效果，用 OrcaSlicer 开发版（直接读取源码树 `resources/profiles`）会更直接。

> **私有仓库**：添加表单里两个可选字段就是 clone 时要用的凭据——
> - **GitHub / Gitee 走 HTTPS**：用户名填你的账号，密码填一个只读的**个人访问令牌（PAT，细粒度 `Contents: Read`）**。dulwich 不会读 `~/.git-credentials`，也不能交互式询问密码，所以令牌必须填在这里。
> - **走 SSH**：URL 要写成 `ssh://` 形式，如 `ssh://git@github.com/你的账号/my-machines.git`（**不支持** `git@github.com:…` 这种 scp 写法）。clone 时调用系统自带的 `ssh`，密钥按平时的 `~/.ssh` 配置即可。
> - 凭据会**明文**保存在插件存储目录（`sources.json` 旁边）——请使用权限最小的专用令牌，并注意保护该目录。

**删除源会自动回滚**该源同步过的厂商：插件会删除它安装的 `<Vendor>.json` / `<Vendor>/` / `.opc` **以及镜像的 `<数据目录>/vendor/<厂商>/` 资源**（删除前需在列表中**再次点击确认**）。若某厂商**仍被其他源使用**，删除一个源不会误删它。对旧版本添加、从未同步过的源没有安装记录，删除时无法回滚，需手动清理 `<数据目录>/system/` 下的对应文件。回滚的文件在**重启后**才从下拉框消失。

管理窗口的语言跟随 OrcaSlicer 界面语言（English / 简体中文）。

---

## 5. 真实示例——`examples/` 里的完整机型仓库

为了把格式讲具体、可验证，本仓库在 [`examples/peopoly-vendor-demo/`](examples/peopoly-vendor-demo/) 下放了一个**完整真实的小型厂商**——Peopoly（单机型 Peopoly Magneto X + 0.4/0.6/0.8 喷嘴 preset）连同完整的 preset 树。该厂商数据**原样复制**自公开的 OrcaSlicer 官方仓库（AGPL-3.0），不含任何专有信息，可作为真实厂商结构的学习样板：

```text
examples/peopoly-vendor-demo/
└── resources/profiles/
    ├── Peopoly.json            # 厂商清单（version 02.04.00.01）
    └── Peopoly/
        ├── machine/            # 机器型号 + 各喷嘴 preset + fdm_*_common
        ├── process/            # 打印参数（+ fdm_process_*_common）
        └── filament/           # 耗材（+ fdm_filament_*_common）
```

> 大型官方厂商（如 Bambu Lab 的 `BBL`，约 2900 个文件）结构相同、只是规模大得多——`machine/` 目录平铺，靠 `fdm_bbl_*_common` 之类的共享基底经 `inherits` 串联。示例特意选一个小而全的厂商，方便一眼看懂结构。

可以用下面两种方式之一试跑。

**方式 A：把源指向本仓库自身**

| 字段 | 值 |
|---|---|
| 名称 | `vendor-demo` |
| Git URL | *推送后的本仓库地址* |
| 分支 | `main` |
| 子路径 | `examples/peopoly-vendor-demo/resources/profiles` |

同步 → 重启 → Peopoly Magneto X 出现在打印机下拉框（Peopoly 大多已内置，等同重装该厂商、无害；要体验「新增机器」的完整流程，请把源指向含**你构建里没有**的机器的仓库）。

**方式 B：不推送，本地直接试**

dulwich 支持从本地路径 clone，所以把示例目录初始化成 git 仓库，再用它的路径当 URL 即可：

```bash
cd examples/peopoly-vendor-demo
git init
git add -A
git commit -m "add Peopoly vendor demo"
```

然后添加源：Git URL 填 `examples/peopoly-vendor-demo` 的**绝对路径**（或指向它的 `file://` URL），分支 `main`，子路径 `resources/profiles`。

真实清单摘录——注意每个 `sub_path` 都**相对厂商目录**（`Peopoly/`）：

```json
{
    "name": "Peopoly",
    "version": "02.04.00.01",
    "description": "Peopoly configurations",
    "machine_model_list": [
        { "name": "Peopoly Magneto X", "sub_path": "machine/Peopoly Magneto X.json" }
    ],
    "machine_list": [
        { "name": "fdm_machine_common", "sub_path": "machine/fdm_machine_common.json" },
        { "name": "Peopoly Magneto X 0.4 nozzle", "sub_path": "machine/Peopoly Magneto X 0.4 nozzle.json" }
    ],
    "process_list":  [ "…" ],
    "filament_list": [ "…" ]
}
```

---

## 6. 如何创建自己的「机型仓库」

**不要凭空写 profile。** OrcaSlicer 的 JSON preset 高度互相依赖——文件之间靠 `inherits` 串联，清单里引用的每个文件都必须真实存在。可靠的做法：

1. **复制结构相近的厂商**——从官方 `resources/profiles/` 里挑一台与你的机器最像的厂商，或直接拿本仓库示例 `examples/peopoly-vendor-demo/resources/profiles/Peopoly/` 当起点。
2. **改名**——重命名厂商目录及内部所有文件，替换品牌/机型名。
3. **改厂商清单** `<Vendor>.json`：
   - `name` → 你的厂商名；
   - `version` → 如果你的 `name` 与**内置**厂商重名，版本号必须**高于**内置版本，否则重启后可能被内置版本覆盖（全新厂商名没有这个顾虑）；
   - `machine_model_list` / `machine_list` / `process_list` / `filament_list` → 保证每个 `sub_path` 与实际文件一致。
4. **调整机器定义**：`name`、打印范围/高度、喷嘴直径、床模型/纹理（`bed_model` / `bed_texture` 引用的是 preset 旁的资源文件）、`default_materials` 等。
5. **提交并推送**，把仓库地址分享出去：

```bash
cd my-machines
git init
git add -A
git commit -m "add MyVendor machines"
git remote add origin https://github.com/你的账号/my-machines.git
git push -u origin main
```

别人把该仓库添加为源（见 [§4](#4-使用方法)），同步 + 重启即可用上。

---

## 7. 限制与注意事项

- **同步后必须重启** OrcaSlicer 才生效。
- 只处理 JSON 形式的 vendor（开发树形式），不处理发布构建的 `.opc` 缓存。
- 同名厂商覆盖时，版本号需**高于**内置版本，否则重启后可能被内置版本覆盖（见 [§6](#6-如何创建自己的机型仓库)）。
- 删除源会**自动回滚**它同步过的厂商（删除前需二次确认，重启后生效）；但无安装记录（旧版本添加/从未同步）的源不会清理历史文件（见 [§4](#4-使用方法)）。
- 首次同步会触发文件访问授权弹窗，请允许。
- 同步前插件会**校验每个厂商**：JSON 不可解析、清单引用文件缺失这类硬伤会跳过该厂商并在日志给出原因；而当前 OrcaSlicer **不支持的缩略图格式会在安装副本里被自动剔除**（日志有说明，源仓库与主程序都不改动），让第三方厂商数据也能正常加载。
- **机器封面图**（`*_cover.png`——侧栏打印机图片与引导页）主程序**只从应用自带的 `resources/profiles/<厂商>/` 读取**（`Plater.cpp` / `WebGuideDialog.cpp` 均无数据目录回退），因此仅通过源仓库安装的厂商会显示占位图。这是**主程序侧**的能力缺口：本插件保持独立、只写应用数据目录；如需支持，应由主程序**整体实现**（让数据目录安装的厂商与内置厂商走同一套资源解析），而不是采用「插件 + 主程序补丁」的混合方案。
- 建议 git URL 使用 HTTPS；**SSH**（`ssh://…`）会调用系统自带的 `ssh` 客户端（密钥照常生效），但本机需要装有 `ssh`，且不支持 scp 写法的 URL。
- 把整棵官方 profiles 树作为源会**整体覆盖**内置厂商（用该分支的版本）——除非这正是你想要的，否则建议指向精简机型仓库（见 [§2](#2-两种典型用法)）。

---

## 8. 仓库结构

```text
orca-vendor-source-plugin/
├── orca_vendor_source_plugin.py       # 插件本体——唯一事实来源（同时也是单文件分发形式）
├── pyproject.toml                     # wheel 打包配置；setuptools 直接指向上面的 .py
├── dist/
│   └── orca_vendor_source_plugin-<version>-py3-none-any.whl  # 预构建 wheel（与 .py 代码完全相同，可直接安装）
├── tools/
│   └── verify_wheel.py                # 按宿主规则校验 wheel（CI 里也会跑）
├── .github/
│   └── workflows/build-wheel.yml      # 推送/PR 时构建+校验；打 v* 标签时把 wheel 挂到 Release
├── README.md                          # 英文说明（GitHub 默认展示）
├── README.zh-CN.md                    # 本文件（简体中文）
├── examples/
│   └── peopoly-vendor-demo/           # 公开的完整小厂商（Peopoly），可运行示例
│       └── resources/profiles/        #   → 把源指向这里
│           ├── Peopoly.json
│           └── Peopoly/
└── .gitignore
```
