# peopoly-vendor-demo — A complete real vendor as a runnable example

> English | [简体中文](README.zh-CN.md)

A complete, real OrcaSlicer **vendor** (**Peopoly Magneto X**) that doubles as a runnable example for the [Vendor Source plugin](../..).

It is laid out exactly like a minimal "machine repo" — a git repo whose `resources/profiles/` contains just one vendor:

```text
peopoly-vendor-demo/
└── resources/profiles/
    ├── Peopoly.json               # vendor manifest
    └── Peopoly/
        ├── machine/               # machine model + per-nozzle machine presets + common
        ├── process/               # print profiles (+ common)
        └── filament/              # filament profiles (+ common)
```

This vendor data is copied unchanged from the **public** OrcaSlicer repository
(`resources/profiles/Peopoly.json` + `Peopoly/`, AGPL-3.0) — a good case study of how
a real vendor is structured, with no proprietary data.

Besides the profile JSONs, the vendor ships its binary assets — bed models
(`magnetox_model*.stl`), bed textures (`magnetox_model*_texture.svg`) and the machine
cover (`Peopoly Magneto X_cover.png`). On sync the plugin mirrors these into the app
data directory's `vendor/Peopoly/` folder, where the app looks for the bed
model/texture first (the cover image is still subject to the main-app read-path
limit, see the top-level README).

## How to try it

**Option A — use this repo itself as the source**

Add a source in the plugin:

| Field | Value |
|---|---|
| Name | `vendor-demo` |
| Git URL | *(this repo's URL)* |
| Branch | `main` |
| Sub-path | `examples/peopoly-vendor-demo/resources/profiles` |

Sync → restart → the Peopoly Magneto X machine appears.

> Peopoly is already built into most OrcaSlicer releases, so syncing it simply re-installs
> the same vendor (harmless). To see the "brand-new machine" flow end-to-end, point a
> source at a repo that carries a machine **not** in your build (e.g. your own fork).

**Option B — local test without pushing**

```bash
cd examples/peopoly-vendor-demo
git init
git add -A
git commit -m "add Peopoly vendor demo"
```

Then add a source with *Git URL* = the absolute path to this folder (or a `file://` URL), branch `main`, sub-path `resources/profiles`.

> See the top-level [README](../../README.md) for details, caveats, and how to build your own machine repo.
