# peopoly-vendor-demo — 完整真实厂商示例（可运行）

> [English](README.md) | 简体中文

一个完整、真实的 OrcaSlicer **厂商（vendor）**（**Peopoly Magneto X**），同时作为 [Vendor Source 插件](../..) 的可运行示例。

它的目录布局与一个「精简机型仓库」完全一致——一个 git 仓库，其 `resources/profiles/` 只包含一个厂商：

```text
peopoly-vendor-demo/
└── resources/profiles/
    ├── Peopoly.json               # 厂商清单
    └── Peopoly/
        ├── machine/               # 机器型号 + 各喷嘴 preset + common
        ├── process/               # 打印参数（+ common）
        └── filament/              # 耗材（+ common）
```

该厂商数据**原样复制**自**公开**的 OrcaSlicer 官方仓库（`resources/profiles/Peopoly.json` + `Peopoly/`，AGPL-3.0）——可作为真实厂商结构的案例分析，不含任何专有数据。

## 如何试跑

**方式 A——把本仓库自身作为源**

在插件里添加源：

| 字段 | 值 |
|---|---|
| 名称 | `vendor-demo` |
| Git URL | *（本仓库的地址）* |
| 分支 | `main` |
| 子路径 | `examples/peopoly-vendor-demo/resources/profiles` |

同步 → 重启 → Peopoly Magneto X 机器出现。

> Peopoly 已内置在大多数 OrcaSlicer 版本中，同步它只是重装同一厂商（无害）。想完整体验「新增机器」的流程，请把源指向一个含**你构建里没有**的机器的仓库（例如你自己的 fork）。

**方式 B——不推送，本地直接试**

```bash
cd examples/peopoly-vendor-demo
git init
git add -A
git commit -m "add Peopoly vendor demo"
```

然后添加源：Git URL 填本目录的**绝对路径**（或指向它的 `file://` URL），分支 `main`，子路径 `resources/profiles`。

> 细节、注意事项及如何搭建自己的机型仓库，见顶层 [README](../../README.zh-CN.md)。
