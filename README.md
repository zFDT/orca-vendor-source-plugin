# Vendor Source — OrcaSlicer Vendor Profile Source Plugin

<div align="center">

**English** · [简体中文](README.zh-CN.md)

</div>

A pure-Python plugin for OrcaSlicer that pulls **machine (vendor) profiles** from any git repository or branch and merges them into your local OrcaSlicer. After a restart, the new machines appear in the printer dropdown — no nightly builds, no waiting for the next stable release.

It directly addresses [OrcaSlicer issue #15505](https://github.com/OrcaSlicer/OrcaSlicer/issues/15505): *a new printer has been merged into `main`/dev, but the stable release isn't out yet — you don't want to run nightly, yet you want to use the new machine.*

> This folder is meant to be an **independent git repository**, unrelated to the OrcaSlicer main tree. You can push it to GitHub / Gitee and distribute it on its own.

---

## Table of Contents

- [Vendor Source — OrcaSlicer Vendor Profile Source Plugin](#vendor-source--orcaslicer-vendor-profile-source-plugin)
  - [Table of Contents](#table-of-contents)
  - [1. What it is](#1-what-it-is)
  - [2. Two ways to use it](#2-two-ways-to-use-it)
    - [2.1 Track the official repo for pre-release machines](#21-track-the-official-repo-for-pre-release-machines)
    - [2.2 A minimal vendor repo (recommended for your own machines)](#22-a-minimal-vendor-repo-recommended-for-your-own-machines)
  - [3. Install the plugin](#3-install-the-plugin)
  - [4. Using the plugin](#4-using-the-plugin)
  - [5. Worked example — a real vendor repo (`examples/`)](#5-worked-example--a-real-vendor-repo-examples)
  - [6. Building your own machine repo](#6-building-your-own-machine-repo)
  - [7. Limits \& notes](#7-limits--notes)
  - [8. Repository layout](#8-repository-layout)

---

## 1. What it is

OrcaSlicer's machine resources are organized **per vendor** and live under `resources/profiles/` in the source tree:

```text
resources/profiles/
├── Bambulab.json          # vendor manifest (name, version, machine lists…)
├── Bambulab/
│   ├── machine/           # machine models + per-nozzle machine presets
│   ├── process/           # print profiles
│   └── filament/          # filament profiles
├── Voron.json
├── Voron/
└── …
```

The plugin lets you:

1. configure one or more **sources** — each is a git repo URL + branch + sub-path (default sub-path: `resources/profiles`);
2. fetch that repo with the pure-Python library [dulwich](https://github.com/jelmer/dulwich) — no system `git` required;
3. copy the vendors found under the sub-path into your local `<data dir>/system/`;
4. prompt you to restart — on the next launch OrcaSlicer scans `system/` and the new machines show up in the dropdown.

**Zero changes to the host app.** The plugin only uses read-only APIs OrcaSlicer already exposes (`preset_bundle()`, `plugin.storage()`, `ui.create_window()`, …). Merging happens entirely in user data; nothing is patched in the application itself.

---

## 2. Two ways to use it

Both typical scenarios are just a "source" pointing at a different git repo.

### 2.1 Track the official repo for pre-release machines

This is the scenario from issue #15505: the machine you want is already in OrcaSlicer's `main` branch, but the latest stable build doesn't include it yet.

| Field | Value |
|---|---|
| Name | `upstream` |
| Git URL | `https://github.com/OrcaSlicer/OrcaSlicer.git` |
| Branch | `main` |
| Sub-path | `resources/profiles` (default) |

Sync, restart, and the machines that recently landed in `main` are available on your stable build.

> ⚠️ **Caveat**: a source points at the *whole* profiles tree. Syncing the official repo therefore (re)installs **every** built-in vendor with the version from that branch. Within the same release line this is usually fine — you're essentially running the dev profile set on a stable binary. If the branch is older than your build, or you only care about one machine, prefer scenario 2.2.

### 2.2 A minimal vendor repo (recommended for your own machines)

Ship **only** your vendor(s) in a tiny git repo:

```text
my-machines/                 # any name
└── resources/profiles/
    ├── MyVendor.json        # vendor manifest (required)
    └── MyVendor/            # machine/ + optional process/, filament/
```

Point a source at it and sync — now only *your* vendor is ever touched, built-ins stay untouched, and there is no version-collision risk when `MyVendor` is a brand-new vendor name. This repo ships a complete, real example — see [§5](#5-worked-example--a-real-vendor-repo-examples).

---

## 3. Install the plugin

1. Start OrcaSlicer and open the **Plugins** dialog (top menu **Plugins**, or **File → Plugins**).
2. Choose **Install from file** and pick `orca_vendor_source_plugin.py` from this repo.
3. Find **Vendor Source** in the plugin list and enable its **Script** capability.
4. A **Vendor Source** action now appears in the **Speed Dial**; click it to open the manager window.

> On first load, OrcaSlicer auto-installs the only dependency (`dulwich`) using its bundled `uv` — this needs network and takes a few seconds.

**Sharing the plugin**: users only ever need that single `orca_vendor_source_plugin.py` file. To host this repo yourself, `git init && git add -A && git commit -m "…"` and push to GitHub / Gitee — keep `examples/`, it doubles as a live demo (see [§5](#5-worked-example--a-real-vendor-repo-examples)).

Requires an OrcaSlicer build that includes the script-plugin host (i.e. the Plugins dialog).

---

## 4. Using the plugin

1. Click **Vendor Source** in the Speed Dial to open the manager window.
2. **Add a source**:
   - *Name* — anything, e.g. `my-machines`;
   - *Git URL* — e.g. `https://github.com/<you>/my-machines.git` (HTTPS recommended);
   - *Branch* — e.g. `main`;
   - *Sub-path* — `resources/profiles` by default; change it only if your repo keeps profiles elsewhere (like this repo's `examples/…`, see below).
3. Click **Sync** (or **Sync all**).
4. The first write into the system directory triggers OrcaSlicer's **file-access authorization** dialog — please allow it.
5. **Restart OrcaSlicer**. The new machines now appear in the printer dropdown.

**Deleting a source automatically rolls back** the vendors it synced: the plugin removes the `<Vendor>.json` / `<Vendor>/` / `.opc` files it installed (you confirm with a second click in the list first). If a vendor is **still used by another source**, deleting one source leaves it in place. Sources added by an older plugin version or never synced have no install record — nothing is rolled back, so clean up `<data dir>/system/` manually in that case. Rolled-back machines disappear from the dropdown **after a restart**.

---

## 5. Worked example — a real vendor repo (`examples/`)

To make the format concrete and testable, this repo ships the **complete real vendor** *Peopoly* under [`examples/peopoly-vendor-demo/`](examples/peopoly-vendor-demo/) — a single machine (Peopoly Magneto X) with 0.4 / 0.6 / 0.8 nozzle presets and its full preset tree. The vendor data is copied **unchanged** from the public OrcaSlicer repository (AGPL-3.0) — no proprietary data, a great study sample of a real vendor's anatomy:

```text
examples/peopoly-vendor-demo/
└── resources/profiles/
    ├── Peopoly.json            # vendor manifest (version 02.04.00.01)
    └── Peopoly/
        ├── machine/            # machine model + per-nozzle presets + fdm_*_common
        ├── process/            # print profiles (+ fdm_process_*_common)
        └── filament/           # filament profiles (+ fdm_filament_*_common)
```

> Big official vendors (e.g. Bambu Lab's `BBL`, ~2,900 files) follow the same anatomy, just at a much larger scale — a flat `machine/` directory with shared bases like `fdm_bbl_*_common` chained via `inherits`. The example deliberately picks a small-but-complete vendor so the structure is easy to grasp.

Try it in either of two ways.

**A. Point a source at this repository itself**

| Field | Value |
|---|---|
| Name | `vendor-demo` |
| Git URL | *this repo's URL once pushed* |
| Branch | `main` |
| Sub-path | `examples/peopoly-vendor-demo/resources/profiles` |

Sync → restart → Peopoly Magneto X shows up (Peopoly is already built into most builds, so this effectively re-installs the same vendor — harmless; to see the "brand-new machine" flow end-to-end, point a source at a repo that carries a machine *not* in your build).

**B. Test locally without pushing anything**

dulwich can clone from a local path, so turn the demo folder into a git repo and use its path as the URL:

```bash
cd examples/peopoly-vendor-demo
git init
git add -A
git commit -m "add Peopoly vendor demo"
```

Then add a source with *Git URL* = the absolute path to `examples/peopoly-vendor-demo` (or a `file://` URL to it), branch `main`, sub-path `resources/profiles`.

Real manifest excerpt — every `sub_path` is **relative to the vendor folder** (`Peopoly/`):

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

## 6. Building your own machine repo

**Don't write profiles from scratch.** OrcaSlicer JSON presets are highly interdependent — files chain through `inherits`, and every file referenced from the manifest must exist. The reliable recipe:

1. **Copy a structurally similar vendor** — pick a vendor whose machines resemble yours from the official `resources/profiles/`, or start from the shipped example `examples/peopoly-vendor-demo/resources/profiles/Peopoly/`.
2. **Rename** the vendor folder and every file inside it, replacing brand/model names.
3. **Edit the manifest** `<Vendor>.json`:
   - `name` → your vendor name;
   - `version` → if your `name` collides with a **built-in** vendor, it must be **higher** than the built-in one, or the built-in may win on the next launch (a brand-new vendor name has no such constraint);
   - `machine_model_list` / `machine_list` / `process_list` / `filament_list` → keep every `sub_path` consistent with the actual files.
4. **Adjust machine definitions**: `name`, printable area/height, nozzle diameters, bed model/texture (asset files referenced next to the machine preset), `default_materials`, etc.
5. **Commit & push**, then share the URL:

```bash
cd my-machines
git init
git add -A
git commit -m "add MyVendor machines"
git remote add origin https://github.com/<you>/my-machines.git
git push -u origin main
```

Others add the repo as a source (see [§4](#4-using-the-plugin)), sync, restart — done.

---

## 7. Limits & notes

- **A restart is required** after syncing before new machines appear.
- Only JSON-form vendors (the dev-tree form) are processed — not the release `.opc` cache bundles.
- When a synced vendor has the **same name** as a built-in, its `version` must be **higher**, or the built-in may win on the next launch (see [§6](#6-building-your-own-machine-repo)).
- Deleting a source **automatically rolls back** the vendors it synced (needs a second-click confirm; effective after restart), except sources with no install record (added by an older version / never synced) — see [§4](#4-using-the-plugin).
- The first write to the system directory triggers a file-access **authorization prompt** — please allow it.
- Prefer **HTTPS** git URLs. SSH URLs need extra key handling (dulwich's SSH support pulls in more dependencies).
- Syncing a whole official profiles tree overwrites **all** built-in vendors with that branch's versions — point sources at minimal vendor repos unless that is exactly what you want (see [§2](#2-two-ways-to-use-it)).

---

## 8. Repository layout

```text
orca-vendor-source-plugin/
├── orca_vendor_source_plugin.py      # the plugin (the only runtime file users need)
├── README.md                         # this file (English)
├── README.zh-CN.md                   # 简体中文版
├── examples/
│   └── peopoly-vendor-demo/         # complete public vendor (Peopoly), runnable example
│       └── resources/profiles/      #   → point a source here
│           ├── Peopoly.json
│           └── Peopoly/
└── .gitignore
```
