#!/usr/bin/env python3
"""Validate a built OrcaSlicer plugin wheel without launching OrcaSlicer.

Usage:
    python tools/verify_wheel.py dist/*.whl

The checks mirror what the host enforces, so a broken package fails here instead
of in the Plugins dialog:

* ``PluginFsUtils.cpp`` -> ``read_wheel_plugin_metadata()``
    - exactly one ``.dist-info`` containing ``METADATA`` / ``WHEEL`` / ``RECORD``
      (``RECORD`` must not be empty)
    - ``METADATA``: ``Name`` and ``Version`` required; ``Summary``/``Author`` are
      the display fields; ``Requires-Dist`` becomes the plugin's dependencies
    - ``WHEEL``: a ``Wheel-Version`` header and a compatible tag (this plugin is
      pure Python, so ``*-none-any`` is required)
    - entry package = ``Import-Name`` -> single-line ``top_level.txt`` ->
      normalized ``Name``
* ``PythonInterpreter.cpp`` -> ``load_module_from_whl()``
    - the wheel is extracted and ``<entry package>`` is imported from it
* ``PluginLoader.cpp``
    - the module's ``@orca.plugin`` package class registers >= 1 capability and
      every capability resolves ``get_name()`` (no ``;``, which is reserved)

The ``orca`` module used for the import is a stub covering the import-time API
only; runtime host calls are not exercised.
"""

from __future__ import annotations

import glob
import importlib
import re
import sys
import tempfile
import types
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_MODULE = REPO_ROOT / "orca_vendor_source_plugin.py"

_plugin_classes: list[type] = []
_registered: list[type] = []


def _install_orca_stub() -> None:
    """Minimal stand-in for the embedded ``orca`` module (import-time API)."""
    orca = types.ModuleType("orca")

    class base:  # orca.base — the package base class
        pass

    def plugin(cls):
        cls.__orca_plugin__ = True
        _plugin_classes.append(cls)
        return cls

    def register_capability(cls):
        _registered.append(cls)

    class ScriptPluginCapabilityBase:
        pass

    script = types.ModuleType("orca.script")
    script.ScriptPluginCapabilityBase = ScriptPluginCapabilityBase
    orca.base = base
    orca.plugin = plugin
    orca.register_capability = register_capability
    orca.script = script
    sys.modules["orca"] = orca
    sys.modules["orca.script"] = script


def _parse_headers(text: str) -> dict[str, list[str]]:
    headers: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.strip():
            break  # a blank line ends the header block
        if line[0] in " \t" or ":" not in line:
            continue  # folded continuation or body text
        key, _, value = line.partition(":")
        headers.setdefault(key.strip().lower(), []).append(value.strip())
    return headers


def _normalize(name: str) -> str:
    # Mirrors PluginFsUtils.cpp::normalize_package_name() (PEP 503 normalization).
    return re.sub(r"[-_.]+", "_", name).lower()


def _first(headers: dict[str, list[str]], key: str) -> str:
    values = headers.get(key, [])
    return values[0] if values else ""


def verify(wheel: Path) -> list[str]:
    problems: list[str] = []

    try:
        archive = zipfile.ZipFile(wheel)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"not a readable zip/wheel: {exc}"]

    with archive:
        names = archive.namelist()

        dist_infos: set[str] = set()
        for name in names:
            index = name.find(".dist-info")
            if index < 0:
                continue
            rest = name[index + len(".dist-info") :]
            if rest == "" or rest.startswith("/"):
                dist_infos.add(name[: index + len(".dist-info")] + "/")
        if len(dist_infos) != 1:
            return [f"expected exactly one .dist-info directory, found {sorted(dist_infos) or 'none'}"]
        dist_info = dist_infos.pop()

        def read_member(member: str) -> str | None:
            try:
                return archive.read(dist_info + member).decode("utf-8")
            except KeyError:
                return None

        meta_text = read_member("METADATA")
        if meta_text is None:
            problems.append("METADATA is missing")
            return problems
        meta = _parse_headers(meta_text)
        name = _first(meta, "name")
        version = _first(meta, "version")
        if not name:
            problems.append("METADATA is missing the required Name field")
        if not version:
            problems.append("METADATA is missing the required Version field")

        # The wheel version must match the PEP 723 block of the file users install
        # directly, or the two distribution forms drift apart. The wheel takes its
        # version from pyproject.toml, so this also guards that pair.
        if REPO_MODULE.exists():
            match = re.search(
                r'^#\s*version\s*=\s*"([^"]+)"', REPO_MODULE.read_text(encoding="utf-8"), re.MULTILINE
            )
            source_version = match.group(1) if match else ""
            if source_version and version and source_version != version:
                problems.append(
                    f"wheel version {version} != PEP 723 version {source_version} in {REPO_MODULE.name}"
                )

        wheel_text = read_member("WHEEL")
        if wheel_text is None:
            problems.append("WHEEL is missing")
        else:
            if "Wheel-Version:" not in wheel_text:
                problems.append("WHEEL is missing the Wheel-Version header")
            tags = [value for key, values in _parse_headers(wheel_text).items() if key == "tag" for value in values]
            if not tags:
                problems.append("WHEEL declares no Tag: lines")
            elif not any("-none-any" in tag for tag in tags):
                problems.append(f"not a pure-Python wheel; tags: {', '.join(tags)}")

        record = read_member("RECORD")
        if record is None:
            problems.append("RECORD is missing")
        elif not record.strip():
            problems.append("RECORD is empty")

        top_level_text = read_member("top_level.txt")
        top_level = ""
        if top_level_text is not None:
            entries = [line.strip() for line in top_level_text.splitlines() if line.strip()]
            if len(entries) == 1:
                top_level = entries[0]
            elif len(entries) > 1:
                problems.append(f"top_level.txt lists several packages: {entries}")

        import_name = _first(meta, "import-name")  # core-metadata Import-Name, optional
        if import_name:
            entry = import_name
        elif top_level:
            entry = top_level
        elif name:
            entry = _normalize(name)
        else:
            return problems + ["cannot resolve the entry package (no Name in METADATA)"]

        module_member = next(
            (member for member in names if member in (f"{entry}.py", f"{entry}/__init__.py")), None
        )
        if module_member is None:
            problems.append(f"wheel has no importable module or package for entry '{entry}'")

    # The wheel must match the single source file in the repo, or it is a stale build.
    if module_member and REPO_MODULE.exists():
        with zipfile.ZipFile(wheel) as source_archive:
            if source_archive.read(module_member) != REPO_MODULE.read_bytes():
                problems.append(f"{module_member} differs from {REPO_MODULE.name} (rebuild the wheel)")

    # Load it the way PythonInterpreter::load_module_from_whl() does: extract,
    # put the extraction root on sys.path, import <entry>.
    if module_member:
        with tempfile.TemporaryDirectory() as tmp:
            extract_root = Path(tmp) / "__whl_extracted__" / entry
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(extract_root)
            _install_orca_stub()
            sys.modules.pop(entry, None)
            sys.path.insert(0, str(extract_root))
            try:
                module = importlib.import_module(entry)
            except Exception as exc:  # noqa: BLE001 - report any import failure as a problem
                problems.append(f"import {entry} failed: {exc!r}")
                module = None
            finally:
                sys.path.remove(str(extract_root))

            if module is not None:
                if len(_plugin_classes) != 1:
                    problems.append(
                        f"expected exactly one @orca.plugin package class, found {len(_plugin_classes)}"
                    )
                else:
                    package = _plugin_classes[0]()
                    package.register_capabilities()  # PluginLoader does exactly this
                    if not _registered:
                        problems.append("register_capabilities() registered no capability")
                    for capability_class in _registered:
                        capability_name = capability_class().get_name()
                        if not isinstance(capability_name, str) or not capability_name.strip():
                            problems.append(f"{capability_class.__name__}.get_name() is empty")
                        elif ";" in capability_name:
                            problems.append(f"{capability_name!r} contains ';', which is reserved")

    return problems


def main(argv: list[str]) -> int:
    patterns = argv[1:] or ["dist/*.whl"]
    wheels: list[Path] = []
    for pattern in patterns:
        matched = [Path(match) for match in glob.glob(pattern)]
        wheels.extend(matched or [Path(pattern)])
    if not wheels:
        print("no wheels given (usage: python tools/verify_wheel.py dist/*.whl)")
        return 2

    failed = False
    for wheel in wheels:
        problems = verify(wheel)
        if problems:
            failed = True
            print(f"FAIL {wheel}")
            for problem in problems:
                print(f"     - {problem}")
        else:
            print(f"OK   {wheel}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
