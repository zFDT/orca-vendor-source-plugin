# /// script
# requires-python = ">=3.12"
# dependencies = ["dulwich>=0.21.7"]
#
# [tool.orcaslicer.plugin]
# name = "Vendor Source"
# description = "Import machine/vendor profiles from git repositories into OrcaSlicer (restart required)."
# author = "OrcaSlicer Community"
# version = "0.3.0"
# ///
"""Vendor Source — 从 git 仓库把「尚未合入主程序的机器资源」合并进本地 OrcaSlicer。

解决的问题：新打印机（如某个新机型）已经合入了 OrcaSlicer 的 dev 分支，
但稳定版还没发布，你不想装 nightly。本插件让你把任意一个 git 仓库 + 分支里
的 `resources/profiles/`（vendor profile）下载并合并到本地的
`<data_dir>/system/`，重启 OrcaSlicer 后，新机器就会出现在打印机下拉框里。

原理（零主程序改动）：
  1. 通过只读 API `orca.host.preset_bundle()` 读一个系统打印机预设的 `file`
     字段（绝对路径），反推出 `data_dir/system` 目录；
  2. 用纯 Python 库 dulwich 拉取 git 仓库到插件私有存储目录；
  3. 把仓库里的 `<Vendor>.json` + `<Vendor>/` 拷贝进 `system/`，
     并先删除同名 `<Vendor>.opc`（避免旧缓存 shadow 新 JSON）；
  4. 把 `<Vendor>/` 里的二进制资源（`.stl`/`.svg`/`.png` 等，如床模型、床贴图、
     喷嘴模型、机型缩略图）镜像到 `<data_dir>/vendor/<Vendor>/`——主程序解析
     这些资源时优先查该目录（PresetUtils::system_printer_bed_model 等），找不到
     才回退到应用安装目录的 `resources/profiles/`；只装进 `system/` 是找不到的；
  5. 提示用户重启。重启后 `load_system_presets_from_json` 会遍历
     `system/` 目录并加载新厂商。

限制：
  - 同步后需要重启 OrcaSlicer 才生效；
  - 只处理 JSON 形式的 vendor（开发树形式），不处理 `.opc` 缓存形式；
  - 删除源会自动回滚它同步过的厂商（含资源镜像）；无安装记录（旧版本添加/从未同步）
    的源无法回滚，需手动清理；
  - **机器封面图（`*_cover.png`，如侧栏的打印机图片与引导页）主程序目前只从应用自带的
    `resources/profiles/<厂商>/` 读取（Plater.cpp / WebGuideDialog.cpp 没有数据目录回退），
    插件无法补齐——需要主程序加回退路径，或把封面放回应用资源目录**；
  - 首次同步写入 system 目录会触发 OrcaSlicer 的文件访问授权弹窗，请允许。
"""
import json
import shutil
import threading
from pathlib import Path

import orca

try:
    from dulwich import porcelain
except Exception:  # pragma: no cover - 依赖未装上时的降级提示
    porcelain = None


DEFAULT_SUB_PATH = "resources/profiles"

# OrcaSlicer 支持的 gcode 缩略图格式（PrintConfig.hpp: enum GCodeThumbnailsFormat）。
# 不在白名单内的扩展名（如 PNG_TOP_FRONT、厂商自定义 AF_PICK_*）会让
# handle_legacy_composite 抛异常，导致整个 vendor 加载失败。
_SUPPORTED_THUMBNAIL_EXTS = {"png", "jpg", "qoi", "btt_tft", "colpic"}


# --------------------------------------------------------------------------- #
# 自包含 HTML 页面：只通过 window.orca 桥与插件通信，主题变量来自宿主。
# --------------------------------------------------------------------------- #
PAGE = r"""<!DOCTYPE html>
<html lang="{{html_lang}}">
<head>
<meta charset="utf-8">
<style>
  :root {
    --bg:        var(--orca-bg, #ffffff);
    --fg:        var(--orca-fg, #1f2429);
    --muted:     var(--orca-muted, #6b7580);
    --border:    var(--orca-border, #d9dee3);
    --accent:    var(--orca-accent, #009688);
    --accent-fg: var(--orca-accent-fg, #ffffff);
    --ui:        var(--orca-font, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif);
    --mono:      ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:     var(--orca-bg, #2b2d30);
      --fg:     var(--orca-fg, #e4e6e8);
      --muted:  var(--orca-muted, #9aa0a6);
      --border: var(--orca-border, #3d4043);
    }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.5 var(--ui); padding:16px; }
  h1 { font-size:16px; margin:0 0 4px; }
  p.muted { color:var(--muted); margin:0 0 14px; font-size:12px; }
  button { font:inherit; padding:5px 14px; cursor:pointer; border-radius:6px;
           background:var(--accent); color:var(--accent-fg);
           border:1px solid var(--accent); }
  button.secondary { background:transparent; color:var(--fg);
                     border-color:var(--border); }
  button.danger { background:#c62828; border-color:#c62828; color:#fff; }
  button.small { padding:2px 10px; font-size:12px; }
  button:disabled { opacity:.55; cursor:default; }
  input { font:inherit; color:var(--fg); background:var(--bg);
          border:1px solid var(--border); border-radius:6px; padding:4px 8px; }
  :focus-visible { outline:2px solid var(--accent); outline-offset:1px; }

  .card { border:1px solid var(--border); border-radius:8px;
          padding:12px 14px; margin-bottom:14px; }
  .card h2 { margin:0 0 10px; font-size:12px; letter-spacing:.06em;
             text-transform:uppercase; color:var(--muted); }
  .form { display:grid; grid-template-columns:auto 1fr; gap:8px 10px;
          align-items:center; }
  .form label { color:var(--muted); font-size:12px; white-space:nowrap; }
  .form input { min-width:0; }
  .form .full { grid-column:1 / -1; display:flex; justify-content:flex-end; }

  table { width:100%; border-collapse:collapse; font-size:12px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--border);
           vertical-align:middle; }
  th { color:var(--muted); font-size:11px; text-transform:uppercase;
       letter-spacing:.06em; }
  td.mono { font-family:var(--mono); word-break:break-all; }
  td.actions { white-space:nowrap; text-align:right; }
  .empty { color:var(--muted); padding:10px 4px; }

  .toolbar { display:flex; gap:8px; align-items:center; margin-bottom:10px; }

  .banner { border:1px solid var(--border); border-left:3px solid var(--accent);
            border-radius:6px; padding:9px 12px; margin-bottom:12px; font-size:12px;
            display:none; }
  .banner.show { display:block; }

  .log { font-family:var(--mono); font-size:12px; border:1px solid var(--border);
         border-radius:6px; padding:8px 10px; max-height:180px; overflow:auto;
         white-space:pre-wrap; }
</style>
</head>
<body>
  <h1>{{title_h}}</h1>
  <p class="muted">{{desc}}</p>

  <div class="card">
    <h2>{{card_add}}</h2>
    <div class="form">
      <label for="f-name">{{name}}</label>
      <input id="f-name" placeholder="{{ph_name}}" />
      <label for="f-url">{{url}}</label>
      <input id="f-url" placeholder="{{ph_url}}" />
      <label for="f-branch">{{branch}}</label>
      <input id="f-branch" value="main" />
      <label for="f-sub">{{subpath}}</label>
      <input id="f-sub" value="resources/profiles" />
      <label for="f-user">{{username}}</label>
      <input id="f-user" autocomplete="off" spellcheck="false" />
      <label for="f-token">{{token}}</label>
      <input id="f-token" type="password" autocomplete="off" />
      <div class="full"><button id="btn-add">{{add}}</button></div>
    </div>
  </div>

  <div class="card">
    <h2>{{card_sources}}</h2>
    <div class="toolbar">
      <button id="btn-sync-all">{{sync_all}}</button>
      <span id="hint" class="muted" style="font-size:12px;"></span>
    </div>
    <table>
      <thead><tr><th>{{th_name}}</th><th>{{th_repo}}</th><th>{{th_branch}}</th><th>{{th_sub}}</th><th>{{th_act}}</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <div id="empty" class="empty">{{empty}}</div>
  </div>

  <div id="restart" class="banner">{{restart}}</div>

  <div class="card">
    <h2>{{log_title}}</h2>
    <div id="log" class="log"></div>
  </div>

<script>
'use strict';
const $ = id => document.getElementById(id);
const T = {{js}};
const esc = s => String(s == null ? '' : s)
  .replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function logLine(text) {
  const el = $('log');
  el.textContent += text + '\n';
  el.scrollTop = el.scrollHeight;
}

function renderRows(sources) {
  const tbody = $('rows');
  tbody.innerHTML = sources.map(s =>
    '<tr>' +
      '<td>' + esc(s.name) + '</td>' +
      '<td class="mono">' + esc(s.url) + '</td>' +
      '<td>' + esc(s.branch || 'main') + '</td>' +
      '<td class="mono">' + esc(s.sub_path || 'resources/profiles') + '</td>' +
      '<td class="actions">' +
        '<button class="small" data-name="' + esc(s.name) + '" data-act="sync">' + T.sync + '</button> ' +
        '<button class="small secondary remove-btn" data-name="' + esc(s.name) + '" title="' + T.del_title + '">' + T.del + '</button>' +
      '</td>' +
    '</tr>').join('');
  $('empty').style.display = sources.length ? 'none' : '';
}

function refreshState() { orca.postMessage({ command:'state' }); }

function addSource() {
  const source = {
    name: $('f-name').value.trim(),
    url: $('f-url').value.trim(),
    branch: $('f-branch').value.trim() || 'main',
    sub_path: $('f-sub').value.trim() || 'resources/profiles',
    username: $('f-user').value.trim(),
    password: $('f-token').value.trim(),
  };
  if (!source.name || !source.url) { logLine(T.err_required); return; }
  orca.postMessage({ command:'add', source });
}

function syncOne(name) { orca.postMessage({ command:'sync', name }); }
function syncAll() { orca.postMessage({ command:'sync_all' }); }

// 删除源会回滚它同步过的厂商文件（破坏性操作），需要二次点击确认。
let pendingBtn = null, pendingTimer = null;
function askRemove(btn) {
  if (pendingBtn === btn) {
    clearTimeout(pendingTimer);
    pendingBtn.classList.remove('danger');
    pendingBtn.textContent = T.del;
    const name = pendingBtn.dataset.name;
    pendingBtn = null;
    orca.postMessage({ command: 'remove', name });
    return;
  }
  resetPendingRemove();
  pendingBtn = btn;
  btn.classList.add('danger');
  btn.textContent = T.confirm_del;
  pendingTimer = setTimeout(resetPendingRemove, 3000);
}
function resetPendingRemove() {
  if (pendingTimer) clearTimeout(pendingTimer);
  if (pendingBtn && pendingBtn.isConnected) {
    pendingBtn.classList.remove('danger');
    pendingBtn.textContent = T.del;
  }
  pendingBtn = null;
  pendingTimer = null;
}

// 列表按钮统一走事件委托：同步 / 删除（删除需二次点击确认）。
document.addEventListener('click', e => {
  const btn = e.target.closest('button[data-name]');
  if (!btn || !btn.isConnected) return;
  if (btn.dataset.act === 'sync') syncOne(btn.dataset.name);
  else if (btn.classList.contains('remove-btn')) askRemove(btn);
});

$('btn-add').addEventListener('click', addSource);
$('btn-sync-all').addEventListener('click', syncAll);

orca.onMessage(msg => {
  if (!msg || !msg.command) return;
  if (msg.command === 'state') {
    renderRows(msg.sources || []);
  } else if (msg.command === 'log') {
    logLine(msg.text);
  } else if (msg.command === 'done') {
    $('restart').classList.add('show');
  }
});

refreshState();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# 多语言：UI 跟随 OrcaSlicer 界面语言（zh/en 两套文案）。
# PAGE 内的 {{key}} 占位符 + JS 里的 {{js}}（JSON 文案对象）都在渲染时替换。
# --------------------------------------------------------------------------- #
_UI = {
    "zh": {
        "html": {
            "html_lang": "zh-CN",
            "title_h": "Vendor Source — 机器资源源",
            "desc": "从 git 仓库（例如你 fork 的 OrcaSlicer 仓库或某个机型仓库）把尚未合入主程序的机器资源同步到本地。同步完成后需重启 OrcaSlicer。",
            "card_add": "添加资源源",
            "name": "名称",
            "url": "Git 仓库 URL",
            "branch": "分支",
            "subpath": "子路径",
            "username": "用户名（可选）",
            "token": "访问令牌 / 密码（可选）",
            "ph_name": "如 my-fork",
            "ph_url": "https://github.com/you/OrcaSlicer.git",
            "add": "添加",
            "card_sources": "已配置的源",
            "sync_all": "全部同步",
            "th_name": "名称",
            "th_repo": "仓库",
            "th_branch": "分支",
            "th_sub": "子路径",
            "th_act": "操作",
            "empty": "还没有配置任何源。",
            "restart": "✔ 同步完成。请重启 OrcaSlicer，新机器会出现在打印机下拉框中。",
            "log_title": "日志",
        },
        "js": {
            "err_required": "⚠ 名称和 Git 仓库 URL 不能为空",
            "sync": "同步",
            "del": "删除",
            "del_title": "删除源并回滚它同步过的厂商（需二次点击确认）",
            "confirm_del": "再次点击确认删除",
        },
        "log": {
            "added": "已添加源：{name}",
            "dup": "错误：已存在同名源：{name}",
            "required": "错误：名称和仓库 URL 不能为空",
            "removed": "已移除源：{name}",
            "removed_vendors": "已删除该源同步的厂商（重启后从下拉框消失）：{list}",
            "kept_vendors": "以下厂商仍被其他源使用，未删除：{list}",
            "no_install": "该源没有安装记录（旧版本添加或从未同步过）—— 如曾同步，请手动清理 system/ 下的厂商文件",
            "no_system": "警告：无法定位 system 目录，未删除任何文件",
            "syncing": "正在同步 {name} …",
            "synced": "同步完成 {name}：{list}",
            "err_sync": "同步 {name} 出错：{exc}",
            "no_sources": "尚未配置任何源",
            "err_not_found": "错误：找不到源：{name}",
            "none_vendors": "无厂商",
            "none_hint": "子路径 \"{path}\" 下没找到可识别的厂商——请核对：分支是否正确、子路径是否直接包含 <Vendor>.json（顶层需含 machine_model_list / process_list / filament_list 之一），而不是多套了一层目录。",
            "val_skip": "厂商 {name} 数据校验未通过，已跳过安装（问题见上；请修复源仓库后重新同步）",
            "val_json": "{path}：不是合法 JSON（{err}）",
            "val_missing": "{path}：厂商清单引用了该文件，但仓库里不存在",
            "val_thumb": "{path}：不支持的缩略图格式 {ext}（本版本仅支持 PNG/JPG/QOI/BTT_TFT/ColPic）",
            "val_autofix": "已在安装副本中自动剔除 {n} 个文件里不支持的缩略图格式（源仓库与主程序都未改动）。如需真正支持这些格式，需在主程序 GCodeThumbnailsFormat 中扩展。",
            "assets_mirror": "已镜像 {n} 个厂商资源文件（STL/SVG/PNG 等）到数据目录 vendor/，重启后床模型/床贴图、喷嘴模型与机型缩略图可加载（封面图限制见 README）。",
            "err_prefix": "错误：",
        },
    },
    "en": {
        "html": {
            "html_lang": "en",
            "title_h": "Vendor Source — machine resource sources",
            "desc": "Pull machine/vendor profiles that are not in your release yet from any git repo (e.g. a fork of OrcaSlicer or a machine repo) and merge them into this local OrcaSlicer. Restart OrcaSlicer after syncing.",
            "card_add": "Add source",
            "name": "Name",
            "url": "Git repo URL",
            "branch": "Branch",
            "subpath": "Sub-path",
            "username": "Username (optional)",
            "token": "Access token / password (optional)",
            "ph_name": "e.g. my-fork",
            "ph_url": "https://github.com/you/OrcaSlicer.git",
            "add": "Add",
            "card_sources": "Configured sources",
            "sync_all": "Sync all",
            "th_name": "Name",
            "th_repo": "Repo",
            "th_branch": "Branch",
            "th_sub": "Sub-path",
            "th_act": "Actions",
            "empty": "No sources configured yet.",
            "restart": "✔ Sync complete. Restart OrcaSlicer — new machines will appear in the printer dropdown.",
            "log_title": "Log",
        },
        "js": {
            "err_required": "⚠ Name and Git repo URL are required",
            "sync": "Sync",
            "del": "Delete",
            "del_title": "Delete this source and roll back the vendors it synced (requires a second click)",
            "confirm_del": "Click again to confirm delete",
        },
        "log": {
            "added": "added source: {name}",
            "dup": "error: source already exists: {name}",
            "required": "error: name and url are required",
            "removed": "removed source: {name}",
            "removed_vendors": "deleted vendors synced by this source (gone after restart): {list}",
            "kept_vendors": "vendors still used by other sources, not deleted: {list}",
            "no_install": "this source has no install record (added by an older version or never synced) — if it ever synced, clean up system/ manually",
            "no_system": "warning: cannot locate system dir; nothing deleted",
            "syncing": "syncing {name} ...",
            "synced": "synced {name}: {list}",
            "err_sync": "error syncing {name}: {exc}",
            "no_sources": "no sources configured",
            "err_not_found": "error: source not found: {name}",
            "none_vendors": "no vendors",
            "none_hint": "no recognizable vendor under sub-path \"{path}\" — verify the branch is right and that this sub-path directly contains <Vendor>.json files (top-level keys machine_model_list / process_list / filament_list), not one folder deeper.",
            "val_skip": "vendor {name} failed validation and was skipped (see problems above; fix the source repo and re-sync)",
            "val_json": "{path}: not valid JSON ({err})",
            "val_missing": "{path}: referenced by the vendor manifest but missing from the repo",
            "val_thumb": "{path}: unsupported thumbnail format {ext} (this version only supports PNG/JPG/QOI/BTT_TFT/ColPic)",
            "val_autofix": "automatically removed unsupported thumbnail formats from {n} installed file(s) (source repo and main app untouched). Supporting these formats for real requires extending GCodeThumbnailsFormat in the main app.",
            "assets_mirror": "mirrored {n} vendor asset file(s) (STL/SVG/PNG...) into the data dir vendor/ folder; bed model/texture, hotend model and machine thumbnails load after a restart (see README for the cover-image limit).",
            "err_prefix": "error: ",
        },
    },
}


def _detect_lang():
    """跟随 OrcaSlicer 界面语言；拿不到或非中文就回退英文。"""
    try:
        code = orca.host.app_language() or ""
    except Exception:
        code = ""
    return "zh" if code.lower().startswith("zh") else "en"


def _render_page(lang):
    """把 PAGE 模板渲染成指定语言的完整 HTML。"""
    ui = _UI.get(lang) or _UI["en"]
    html = PAGE
    for key, value in ui["html"].items():
        html = html.replace("{{" + key + "}}", value)
    html = html.replace("{{js}}", json.dumps(ui["js"], ensure_ascii=False))
    if "{{" in html:
        raise RuntimeError("unreplaced page placeholders")
    return html


# --------------------------------------------------------------------------- #
# 插件能力
# --------------------------------------------------------------------------- #
class VendorSourcePanel(orca.script.ScriptPluginCapabilityBase):
    win = None

    def get_name(self):
        return "Vendor Source"

    def execute(self):
        if self.win is not None and self.win.is_open():
            self.win.close()

        # 在 UI 线程（插件回调）里缓存路径。宿主 API（storage/preset_bundle）
        # 依赖 thread_local 的插件上下文，不能在 worker 线程里调用。
        self._lang = _detect_lang()
        self._storage = Path(orca.host.plugin.storage())
        self._system = self._locate_system_dir()

        self.win = orca.host.ui.create_window(
            title="Vendor Source",
            html=_render_page(self._lang),
            width=880,
            height=640,
            on_message=self.on_message,
            on_close=self.on_close,
        )
        return orca.ExecutionResult.success("Vendor Source opened.")

    def on_close(self):
        pass

    # 在 UI 线程回调，按命令分派。慢操作（git clone）放到 worker 线程。
    def on_message(self, msg):
        msg = msg or {}
        command = msg.get("command")
        try:
            if command == "state":
                self._post_state()
            elif command == "add":
                self._add_source(msg.get("source") or {})
            elif command == "remove":
                self._remove_source(msg.get("name"))
            elif command == "sync":
                threading.Thread(target=self._sync_source, args=(msg.get("name"),), daemon=True).start()
            elif command == "sync_all":
                threading.Thread(target=self._sync_all, daemon=True).start()
        except Exception as exc:
            self._log(self._t("err_prefix") + str(exc))

    # -------------------------------------------------------------- helpers
    def _post(self, obj):
        if self.win is not None:
            self.win.post(obj)

    def _log(self, text):
        self._post({"command": "log", "text": text})

    # 取当前语言的日志文案；支持 {name}/{list}/{exc} 占位。
    def _t(self, key, **kw):
        table = _UI.get(self._lang, _UI["en"])["log"]
        text = table.get(key) or _UI["en"]["log"].get(key, key)
        return text.format(**kw) if kw else text

    def _sources_file(self):
        return self._storage / "sources.json"

    def _load_sources(self):
        try:
            data = json.loads(self._sources_file().read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_sources(self, sources):
        self._storage.mkdir(parents=True, exist_ok=True)
        self._sources_file().write_text(
            json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")

    def _post_state(self):
        self._post({"command": "state", "sources": self._load_sources()})

    def _add_source(self, source):
        name = (source.get("name") or "").strip()
        url = (source.get("url") or "").strip()
        if not name or not url:
            self._log(self._t("required"))
            return
        sources = self._load_sources()
        if any(s.get("name") == name for s in sources):
            self._log(self._t("dup", name=name))
            return
        entry = {
            "name": name,
            "url": url,
            "branch": (source.get("branch") or "").strip() or "main",
            "sub_path": (source.get("sub_path") or "").strip() or DEFAULT_SUB_PATH,
        }
        # 私有仓库认证（可选）：username + 访问令牌（GitHub 等平台用 PAT）。
        user = (source.get("username") or "").strip()
        password = (source.get("password") or "").strip()
        if user:
            entry["username"] = user
        if password:
            entry["password"] = password
        sources.append(entry)
        self._save_sources(sources)
        self._log(self._t("added", name=name))
        self._post_state()

    def _remove_source(self, name):
        sources = self._load_sources()
        target = next((s for s in sources if s.get("name") == name), None)
        remaining = [s for s in sources if s.get("name") != name]
        self._save_sources(remaining)

        # 顺带清理该源在 repos/ 下的克隆缓存（已删除的源不再需要）。
        try:
            cached = self._storage / "repos" / name
            if cached.exists():
                shutil.rmtree(str(cached))
        except Exception:
            pass

        removed, kept = [], []
        if target is not None and self._system is not None:
            still_used = set()
            for s in remaining:
                still_used.update(s.get("installed") or [])
            for vendor in (target.get("installed") or []):
                if vendor in still_used:
                    kept.append(vendor)
                elif self._uninstall_vendor(vendor):
                    removed.append(vendor)

        self._log(self._t("removed", name=name))
        if removed:
            self._log(self._t("removed_vendors", list=", ".join(removed)))
        if kept:
            self._log(self._t("kept_vendors", list=", ".join(kept)))
        if target is not None and not (target.get("installed") or []):
            self._log(self._t("no_install"))
        if self._system is None:
            self._log(self._t("no_system"))
        self._post_state()

    # 通过系统 printer preset 的绝对路径反推 data_dir/system
    def _locate_system_dir(self):
        bundle = orca.host.preset_bundle()
        for name in bundle.printers.preset_names():
            preset = bundle.printers.find_preset(name)
            if preset is None or not preset.is_system or not preset.file:
                continue
            parts = Path(preset.file).parts
            if "system" in parts:
                idx = parts.index("system")
                return Path(*parts[: idx + 1])
        return None

    # ---------------------------------------------------------- 厂商资源镜像
    # 主程序（PresetUtils::system_printer_bed_model / bed_texture / hotend_model，
    # PresetBundle::get_*_for_printer_model，ConfigWizard 的 <机型>_thumbnail.png）
    # 解析厂商二进制资源时的查找顺序：
    #   ① <data_dir>/vendor/<厂商>/<文件>   ② <应用>/resources/profiles/<厂商>/<文件>
    # 插件装的 preset 在 <data_dir>/system/（只被 preset 扫描读取），资源必须镜像
    # 到 ① 才能被主程序找到；封面（*_cover.png）主程序只查 ②，故镜像也无效。
    def _vendor_assets_root(self):
        if self._system is None:
            return None
        return self._system.parent / "vendor"

    def _mirror_assets(self, src_dir, vendor):
        """把 <厂商>/ 里的二进制资源（除 .json/.opc）镜像到 <data_dir>/vendor/<厂商>/。

        与源保持一致：源里没有资源文件（或整体缺失）时删除旧镜像。
        返回镜像的文件数（失败或无需镜像时 0）。
        """
        root = self._vendor_assets_root()
        if root is None:
            return 0
        dst = root / vendor
        files = []
        if src_dir.is_dir():
            files = [p for p in src_dir.rglob("*") if p.is_file()
                     and p.suffix.lower() not in (".json", ".opc")]
        try:
            if dst.exists():
                shutil.rmtree(str(dst))
            if files:
                shutil.copytree(str(src_dir), str(dst),
                                ignore=shutil.ignore_patterns("*.json", "*.opc"))
                # 过滤后剩下的空目录（如只装 json 的 machine/）没有意义，清掉
                for d in sorted(dst.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                    if d.is_dir() and not any(d.iterdir()):
                        d.rmdir()
        except Exception:
            return 0
        return len(files)

    # ------------------------------------------------------ 文件操作（worker 线程）
    def _clone(self, source, workdir):
        if porcelain is None:
            raise RuntimeError("dulwich is not available; reinstall the plugin")
        target = workdir / source["name"]
        if target.exists():
            shutil.rmtree(str(target))
        branch = (source.get("branch") or "main").encode("utf-8")
        # 私有仓库认证（可选）：username + password（GitHub 等平台密码填 PAT）。
        # dulwich 会把这两个参数透传给 HTTP/SSH 传输层（见 get_transport_and_path）。
        auth = {}
        if source.get("username"):
            auth["username"] = source["username"]
        if source.get("password"):
            auth["password"] = source["password"]
        try:
            cloned = porcelain.clone(source=source["url"], target=str(target),
                                     checkout=True, branch=branch, **auth)
        except TypeError:  # 兼容旧版 dulwich：分支改回 str，并去掉认证参数
            cloned = porcelain.clone(source=source["url"], target=str(target),
                                     checkout=True, branch=(source.get("branch") or "main"))
        # 立刻 close 掉 Repo：否则 pack 文件句柄一直占着，Windows 下再次同步时
        # shutil.rmtree 旧的 repos/<name> 会报 WinError 32（另一个程序正在使用此文件）。
        try:
            cloned.close()
        except Exception:
            pass
        return target

    @staticmethod
    def _is_vendor_json(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return isinstance(data, dict) and any(
                key in data for key in ("machine_model_list", "process_list", "filament_list"))
        except Exception:
            return False

    def _validate_vendor(self, profiles_dir, vendor):
        """同步前校验单个厂商。

        返回 (fatal, warnings)：
          - fatal   结构性硬伤（JSON 不可解析、清单引用缺失）→ 无法安全安装；
          - warnings 可自动降级的问题（不支持的缩略图格式）→ 安装时自动剔除。
        """
        fatal, warnings = [], []
        vdir = profiles_dir / vendor
        root = profiles_dir / (vendor + ".json")

        def rel(path):
            try:
                return str(Path(path).relative_to(profiles_dir)).replace("\\", "/")
            except Exception:
                return str(path)

        def check(path):
            path = Path(path)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                fatal.append(self._t("val_json", path=rel(path), err=str(exc)))
                return None
            if not isinstance(data, dict):
                fatal.append(self._t("val_json", path=rel(path), err="top-level is not a JSON object"))
                return None
            for spec in data.get("thumbnails") or []:
                if isinstance(spec, str) and "/" in spec:
                    ext = spec.rsplit("/", 1)[1].strip().lower()
                    if ext not in _SUPPORTED_THUMBNAIL_EXTS:
                        warnings.append(self._t("val_thumb", path=rel(path), ext=spec))
            return data

        check(root)
        if vdir.is_dir():
            # 1) 清单引用的子文件必须存在且可解析
            root_data = None
            try:
                root_data = json.loads(root.read_text(encoding="utf-8"))
            except Exception:
                pass
            if isinstance(root_data, dict):
                for key in ("machine_model_list", "process_list", "filament_list", "machine_list"):
                    for item in root_data.get(key) or []:
                        if isinstance(item, dict) and item.get("sub_path"):
                            sub = vdir / item["sub_path"]
                            if not sub.is_file():
                                fatal.append(self._t("val_missing", path=rel(sub)))
                            else:
                                check(sub)
            # 2) 目录内其余 *.json 也做解析 + 缩略图检查（覆盖未被清单列出、但会被读到的文件）
            for path in sorted(vdir.rglob("*.json")):
                check(path)
        return (list(dict.fromkeys(fatal)), list(dict.fromkeys(warnings)))

    # 把 <Vendor>.json 及 <Vendor>/ 下 JSON 里不受支持的缩略图条目剔除。
    # 只作用于已安装到 system/ 的副本——源仓库与主程序都不改动。
    def _sanitize_thumbnails(self, vendor):
        if self._system is None:
            return 0
        paths = [self._system / (vendor + ".json")]
        vdir = self._system / vendor
        if vdir.is_dir():
            paths += sorted(vdir.rglob("*.json"))
        changed = 0
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict) or not isinstance(data.get("thumbnails"), list):
                continue
            keep = []
            for spec in data["thumbnails"]:
                if not isinstance(spec, str) or "/" not in spec:
                    keep.append(spec)  # 非 "WxH/EXT" 形式，原样保留
                    continue
                if spec.rsplit("/", 1)[1].strip().lower() in _SUPPORTED_THUMBNAIL_EXTS:
                    keep.append(spec)
            if keep == data["thumbnails"]:
                continue
            if not keep:
                keep = ["512x512/PNG"]
            data["thumbnails"] = keep
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            changed += 1
        return changed

    def _install_from_dir(self, profiles_dir):
        profiles_dir = Path(profiles_dir)
        if not profiles_dir.is_dir():
            raise RuntimeError("profiles dir not found: " + str(profiles_dir))
        if self._system is None:
            raise RuntimeError("cannot locate system profile dir")
        copied = []
        for json_file in sorted(profiles_dir.glob("*.json")):
            if not self._is_vendor_json(json_file):
                continue
            vendor = json_file.stem
            fatal, warnings = self._validate_vendor(profiles_dir, vendor)
            for warn in warnings:
                self._log("  ! " + warn)
            if fatal:
                for problem in fatal:
                    self._log("  - " + problem)
                self._log(self._t("val_skip", name=vendor))
                continue
            old_opc = self._system / (vendor + ".opc")
            if old_opc.exists():
                old_opc.unlink()
            shutil.copyfile(str(json_file), str(self._system / (vendor + ".json")))
            src_dir = profiles_dir / vendor
            if src_dir.is_dir():
                dst_dir = self._system / vendor
                if dst_dir.exists():
                    shutil.rmtree(str(dst_dir))
                shutil.copytree(str(src_dir), str(dst_dir))
            # 只影响安装副本：剔除不支持的缩略图格式，避免 OrcaSlicer 整体加载失败
            if warnings:
                n = self._sanitize_thumbnails(vendor)
                if n:
                    self._log(self._t("val_autofix", n=n))
            # 二进制资源镜像到 data_dir/vendor/：主程序解析床模型/床贴图/喷嘴模型/
            # 缩略图时优先查该目录（system/ 不在查找链里，见 README「限制」）。
            n = self._mirror_assets(src_dir, vendor)
            if n:
                self._log(self._t("assets_mirror", n=n))
            copied.append(vendor)
        return copied

    def _sync_one(self, source):
        workdir = self._storage / "repos"
        workdir.mkdir(parents=True, exist_ok=True)
        repo = self._clone(source, workdir)
        profiles_dir = repo / (source.get("sub_path") or DEFAULT_SUB_PATH)
        return self._install_from_dir(profiles_dir)

    def _sync_source(self, name):
        source = next((s for s in self._load_sources() if s.get("name") == name), None)
        if source is None:
            self._log(self._t("err_not_found", name=name))
            return
        self._log(self._t("syncing", name=name))
        try:
            copied = self._sync_one(source)
            self._record_installed(name, copied)
            summary = ", ".join(copied) or self._t("none_vendors")
            self._log(self._t("synced", name=name, list=summary))
            if not copied:
                self._log(self._t("none_hint",
                                  path=source.get("sub_path") or DEFAULT_SUB_PATH))
        except Exception as exc:
            self._log(self._t("err_sync", name=name, exc=str(exc)))
        self._post({"command": "done"})

    def _sync_all(self):
        sources = self._load_sources()
        if not sources:
            self._log(self._t("no_sources"))
            return
        for source in sources:
            self._log(self._t("syncing", name=source["name"]))
            try:
                copied = self._sync_one(source)
                self._record_installed(source["name"], copied)
                summary = ", ".join(copied) or self._t("none_vendors")
                self._log(self._t("synced", name=source["name"], list=summary))
                if not copied:
                    self._log(self._t("none_hint",
                                      path=source.get("sub_path") or DEFAULT_SUB_PATH))
            except Exception as exc:
                self._log(self._t("err_sync", name=source["name"], exc=str(exc)))
        self._post({"command": "done"})

    # 记录某源上次同步安装的 vendor 列表（删除源时用于自动回滚）
    def _record_installed(self, name, vendors):
        sources = self._load_sources()
        for s in sources:
            if s.get("name") == name:
                s["installed"] = vendors
                break
        self._save_sources(sources)

    # 删除某 vendor 的安装痕迹：system/ 下的 .json / .opc / 目录 + data_dir/vendor/ 资源镜像
    # （幂等，返回是否删了东西）
    def _uninstall_vendor(self, vendor):
        if self._system is None:
            return False
        removed_any = False
        paths = [self._system / (vendor + ".json"),
                 self._system / (vendor + ".opc"),
                 self._system / vendor]
        root = self._vendor_assets_root()
        if root is not None:
            paths.append(root / vendor)
        for path in paths:
            try:
                if path.is_dir():
                    shutil.rmtree(str(path))
                elif path.exists():
                    path.unlink()
                else:
                    continue
                removed_any = True
            except Exception:
                pass
        return removed_any


@orca.plugin
class VendorSourcePlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(VendorSourcePanel)
