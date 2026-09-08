# /// script
# requires-python = ">=3.12"
# dependencies = ["dulwich>=0.21.7"]
#
# [tool.orcaslicer.plugin]
# name = "Vendor Source"
# description = "Import machine/vendor profiles from git repositories into OrcaSlicer (restart required)."
# author = "OrcaSlicer Community"
# version = "0.1.0"
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
  4. 提示用户重启。重启后 `load_system_presets_from_json` 会遍历
     `system/` 目录并加载新厂商。

限制（v1）：
  - 同步后需要重启 OrcaSlicer 才生效；
  - 只处理 JSON 形式的 vendor（开发树形式），不处理 `.opc` 缓存形式；
  - 删除源只从列表移除，不会自动回滚已同步的 vendor；
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


# --------------------------------------------------------------------------- #
# 自包含 HTML 页面：只通过 window.orca 桥与插件通信，主题变量来自宿主。
# --------------------------------------------------------------------------- #
PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
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
  <h1>Vendor Source — 机器资源源</h1>
  <p class="muted">从 git 仓库（例如你 fork 的 OrcaSlicer 仓库或某个机型仓库）把尚未合入主程序的机器资源同步到本地。同步完成后需重启 OrcaSlicer。</p>

  <div class="card">
    <h2>添加资源源</h2>
    <div class="form">
      <label for="f-name">名称</label>
      <input id="f-name" placeholder="如 my-fork" />
      <label for="f-url">Git 仓库 URL</label>
      <input id="f-url" placeholder="https://github.com/you/OrcaSlicer.git" />
      <label for="f-branch">分支</label>
      <input id="f-branch" value="main" />
      <label for="f-sub">子路径</label>
      <input id="f-sub" value="resources/profiles" />
      <div class="full"><button id="btn-add">添加</button></div>
    </div>
  </div>

  <div class="card">
    <h2>已配置的源</h2>
    <div class="toolbar">
      <button id="btn-sync-all">全部同步</button>
      <span id="hint" class="muted" style="font-size:12px;"></span>
    </div>
    <table>
      <thead><tr><th>名称</th><th>仓库</th><th>分支</th><th>子路径</th><th></th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <div id="empty" class="empty">还没有配置任何源。</div>
  </div>

  <div id="restart" class="banner">✔ 同步完成。请重启 OrcaSlicer，新机器会出现在打印机下拉框中。</div>

  <div class="card">
    <h2>日志</h2>
    <div id="log" class="log"></div>
  </div>

<script>
'use strict';
const $ = id => document.getElementById(id);
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
        '<button class="small" onclick= remove-btn" data-name="' + esc(s.name) + '" title="删除源并回滚它同步过的厂商（需二次确认）> ' +
        '<button class="small secondary" onclick="removeOne(\'' + esc(s.name) + '\')">删除</button>' +
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
  };
  if (!source.name || !source.url) { logLine('⚠ 名称和仓库 URL 不能为空'); return; }
  orca.postMessage({ command:'add', source });
}syncOne(name) { orca.postMessage({ command:'sync', name }); }
function syncAll() { orca.postMessage({ command:'sync_all' }); }

// 删除源会回滚它同步过的厂商文件（破坏性操作），需要二次点击确认。
let pendingBtn = null, pendingTimer = null;
function askRemove(btn) {
  if (pendingBtn === btn) {
    clearTimeout(pendingTimer);
    pendingBtn.classList.remove('danger');
    pendingBtn.textContent = '删除';
    const name = pendingBtn.dataset.name;
    pendingBtn = null;
    orca.postMessage({ command: 'remove', name });
    return;
  }
  resetPendingRemove();
  pendingBtn = btn;
  btn.classList.add('danger');
  btn.textContent = '再次点击确认删除';
  pendingTimer = setTimeout(resetPendingRemove, 3000);
}
function resetPendingRemove() {
  if (pendingTimer) clearTimeout(pendingTimer);
  if (pendingBtn && pendingBtn.isConnected) {
    pendingBtn.classList.remove('danger');
    pendingBtn.textContent = '删除';
  }
  pendingBtn = null;
  pendingTimer = null;
}
document.addEventListener('click', e => {
  const btn = e.target.closest('.remove-btn');
  if (btn) askRemove(btn);
}); }); }
function syncAll() { orca.postMessage({ command:'sync_all' }); }

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
        self._storage = Path(orca.host.plugin.storage())
        self._system = self._locate_system_dir()

        self.win = orca.host.ui.create_window(
            title="Vendor Source",
            html=PAGE,
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
            self._log("error: " + str(exc))

    # -------------------------------------------------------------- helpers
    def _post(self, obj):
        if self.win is not None:
            self.win.post(obj)

    def _log(self, text):
        self._post({"command": "log", "text": text})

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
            self._log("error: name and url are required")
            return
        sources = self._load_sources()
        if any(s.get("name") == name for s in sources):
            self._log("error: source already exists: " + name)
            return
        sources.append({
            "name": name,
            "url": url,
            "branch": (source.get("branch") or "").strip() or "main",
            "sub_path": (source.get("sub_path") or "").strip() or DEFAULT_SUB_PATH,
        })
        self._save_sources(sources)
        self._log("added source: " + name)
        self._post_state()

    def _remove_source(self, name):
        sources = self._load_sources()
        target = next((s for s in sources if s.get("name") == name), None)
        remaining = [s for s in sources if s.get("name") != name]
        self._save_sources(remaining)

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

        self._log("removed source: " + name)
        if removed:
            self._log("已删除该源同步的厂商（重启后从下拉框消失）: " + ", ".join(removed))
        if kept:
            self._log("以下厂商仍被其他源使用，未删除: " + ", ".join(kept))
        if target is not None and not (target.get("installed") or []):
            self._log("该源没有安装记录（旧版本添加或从未同步过）—— 如曾同步，请手动清理 system/ 下的厂商文件")
        if self._system is None:
            self._log("warning: 无法定位 system 目录，未删除任何文件")
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

    # ------------------------------------------------------ 文件操作（worker 线程）
    def _clone(self, source, workdir):
        if porcelain is None:
            raise RuntimeError("dulwich is not available; reinstall the plugin")
        target = workdir / source["name"]
        if target.exists():
            shutil.rmtree(str(target))
        branch = (source.get("branch") or "main").encode("utf-8")
        try:
            porcelain.clone(source=source["url"], target=str(target),
                            checkout=True, branch=branch)
        except TypeError:  # 兼容旧版 dulwich 的 str 参数
            porcelain.clone(source=source["url"], target=str(target),
                            checkout=True, branch=(source.get("branch") or "main"))
        return target

    @staticmethod
    def _is_vendor_json(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return isinstance(data, dict) and any(
                key in data for key in ("machine_model_list", "process_list", "filament_list"))
        except Exception:
            return False

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
            self._log("error: source not found: " + name)
            return
        self._log("syncing " + name + " ...")
        try:
            copied = self._sync_one(source)
            self._record_installed(name, copied)
            self._log("synced " + name + ": " + (", ".join(copied) or "no vendors"))
        except Exception as exc:
            self._log("error syncing " + name + ": " + str(exc))
        self._post({"command": "done"})

    def _sync_all(self):
        sources = self._load_sources()
        if not sources:
            self._log("no sources configured")
            return
        for source in sources:
            self._log("syncing " + source["name"] + " ...")
            try:
                copied = self._sync_one(source)
                self._record_installed(source["name"], copied)
                self._log("synced " + source["name"] + ": " + (", ".join(copied) or "no vendors"))
            except Exception as exc:
                self._log("error syncing " + source["name"] + ": " + str(exc))
        self._post({"command": "done"})

    # 记录某源上次同步安装的 vendor 列表（删除源时用于自动回滚）
    def _record_installed(self, name, vendors):
        sources = self._load_sources()
        for s in sources:
            if s.get("name") == name:
                s["installed"] = vendors
                break
        self._save_sources(sources)

    # 删除 system/ 下某 vendor 的 .json / .opc / 目录（幂等，返回是否删了东西）
    def _uninstall_vendor(self, vendor):
        if self._system is None:
            return False
        removed_any = False
        for path in (self._system / (vendor + ".json"),
                     self._system / (vendor + ".opc"),
                     self._system / vendor):
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
