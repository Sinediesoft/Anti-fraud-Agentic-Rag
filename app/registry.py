"""註冊表 —— 像插座板（說明書 S7 第 1 點）。

開機時掃一遍 packages/modules/，讀每個 pack.yaml，呼叫 health() 確認它準備好了，
然後載入。MODULE=a_tbd 只載一個，MODULE=all 載全部。

底線開頭的資料夾（_template）掃描時自動略過，但可以指名載入。
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

from contracts import ENTRYPOINT_FACTORY, HealthReport, PackSpec, ScamModule

MODULES_ROOT = Path(__file__).resolve().parent.parent / "packages" / "modules"


@dataclass
class LoadedModule:
    """一個載入成功的模組。"""

    pack: PackSpec
    instance: ScamModule
    health: HealthReport
    path: Path

    @property
    def id(self) -> str:
        return self.pack.id

    @property
    def usable(self) -> bool:
        """health 有擋門的失敗就不能用，但只是降級的失敗還是可以載。"""
        return not self.health.blocking_failures


@dataclass
class LoadFailure:
    module_id: str
    reason: str


@dataclass
class Registry:
    loaded: list[LoadedModule]
    failures: list[LoadFailure]

    def get(self, module_id: str) -> LoadedModule | None:
        return next((m for m in self.loaded if m.id == module_id), None)

    @property
    def usable(self) -> list[LoadedModule]:
        return [m for m in self.loaded if m.usable]


def _ensure_packages_on_path(root: Path) -> None:
    packages_dir = str(root.parent)
    if packages_dir not in sys.path:
        sys.path.insert(0, packages_dir)


def discover(root: Path | None = None, *, include_hidden: bool = False) -> list[Path]:
    """找出所有模組資料夾。有 pack.yaml 才算數。"""
    root = root or MODULES_ROOT
    if not root.exists():
        return []
    found = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / "pack.yaml").exists():
            continue
        if child.name.startswith("_") and not include_hidden:
            continue
        found.append(child)
    return found


def load(selection: str = "all", root: Path | None = None) -> Registry:
    """載入模組。selection 是 "all" 或某個模組代號。"""
    root = root or MODULES_ROOT
    _ensure_packages_on_path(root)

    if selection == "all":
        candidates = discover(root)
    else:
        target = root / selection
        if not (target / "pack.yaml").exists():
            return Registry(loaded=[], failures=[LoadFailure(selection, "找不到 pack.yaml")])
        candidates = [target]

    loaded: list[LoadedModule] = []
    failures: list[LoadFailure] = []

    for path in candidates:
        try:
            pack = PackSpec.load(path / "pack.yaml")
        except Exception as exc:
            failures.append(LoadFailure(path.name, f"pack.yaml 不合規格：{exc}"))
            continue

        if pack.id != path.name:
            failures.append(LoadFailure(path.name, f"pack.yaml 的 id「{pack.id}」與資料夾名不一致"))
            continue

        try:
            mod = importlib.import_module(f"modules.{path.name}.module")
            factory = getattr(mod, ENTRYPOINT_FACTORY)
            instance = factory()
        except Exception as exc:
            failures.append(LoadFailure(pack.id, f"載入失敗：{exc}"))
            continue

        if not isinstance(instance, ScamModule):
            failures.append(LoadFailure(pack.id, "四個進入點沒有全部實作"))
            continue

        try:
            report = instance.health()
        except Exception as exc:
            failures.append(LoadFailure(pack.id, f"health() 爆炸：{exc}"))
            continue

        loaded.append(LoadedModule(pack=pack, instance=instance, health=report, path=path))

    return Registry(loaded=loaded, failures=failures)
