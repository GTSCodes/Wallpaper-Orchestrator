"""C5 selection policies and smart-collection definitions.

These are platform-independent preferences. The catalogue owns images and tags;
this module stores only stable IDs and never moves or alters wallpaper files.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from uuid import uuid4


def _settings_path(filename: str) -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".config") / "WallpaperOrchestrator" / filename


def _ids(value, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ) or len(value) != len(set(value)):
        raise ValueError(f"{label} must contain distinct, non-empty IDs.")
    return tuple(value)


def _caps(value, label: str) -> dict[str, int]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not key.strip()
        or type(count) is not int or count < 0
        for key, count in value.items()
    ):
        raise ValueError(f"{label} must map stable IDs to nonnegative limits.")
    return dict(value)


@dataclass(frozen=True)
class ImageRule:
    """Strict constraints reject candidates; preferences only rank them.

    Include lists use ANY semantics; exclude lists reject ANY matching value.
    A collection preference never grants access to an unsubscribed collection.
    """

    mode: str = "require"  # require or prefer
    include_tags: tuple[str, ...] = ()
    exclude_tags: tuple[str, ...] = ()
    include_collections: tuple[str, ...] = ()
    exclude_collections: tuple[str, ...] = ()

    def __post_init__(self):
        if self.mode not in {"require", "prefer"}:
            raise ValueError("Rule mode must be Require or Prefer.")
        for key in ("include_tags", "exclude_tags", "include_collections", "exclude_collections"):
            _ids(getattr(self, key), key)
        if set(self.include_tags) & set(self.exclude_tags):
            raise ValueError("A tag cannot be included and excluded in one rule.")

    @classmethod
    def from_dict(cls, data: dict) -> "ImageRule":
        if not isinstance(data, dict):
            raise ValueError("Invalid selection rule.")
        allowed = {"mode", "include_tags", "exclude_tags", "include_collections", "exclude_collections"}
        if set(data) - allowed:
            raise ValueError("Unknown selection-rule field.")
        return cls(mode=data.get("mode", "require"), **{
            key: _ids(data.get(key, []), key)
            for key in allowed - {"mode"}
        })

    def to_dict(self) -> dict:
        return {"mode": self.mode, **{
            key: list(getattr(self, key))
            for key in ("include_tags", "exclude_tags", "include_collections", "exclude_collections")
        }}

    def matches(self, tags: set[str], collections: set[str]) -> bool:
        return (
            (not self.include_tags or bool(tags.intersection(self.include_tags)))
            and not tags.intersection(self.exclude_tags)
            and (not self.include_collections or bool(collections.intersection(self.include_collections)))
            and not collections.intersection(self.exclude_collections)
        )


@dataclass(frozen=True)
class MonitorGroup:
    id: str
    name: str
    monitor_ids: tuple[str, ...]
    rule: ImageRule = field(default_factory=ImageRule)
    collection_limits: dict[str, int] = field(default_factory=dict)
    tag_limits: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id or not self.name.strip():
            raise ValueError("Monitor group needs an ID and name.")
        _ids(self.monitor_ids, "monitor_ids")
        if not self.monitor_ids:
            raise ValueError("Monitor group must contain a display.")
        _caps(self.collection_limits, "collection_limits")
        _caps(self.tag_limits, "tag_limits")

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "monitor_ids": list(self.monitor_ids), "rule": self.rule.to_dict(),
                "collection_limits": self.collection_limits, "tag_limits": self.tag_limits}

    @classmethod
    def from_dict(cls, data: dict) -> "MonitorGroup":
        if not isinstance(data, dict):
            raise ValueError("Invalid monitor group.")
        return cls(data["id"], data["name"], _ids(data["monitor_ids"], "monitor_ids"),
                   ImageRule.from_dict(data.get("rule", {})),
                   _caps(data.get("collection_limits", {}), "collection_limits"),
                   _caps(data.get("tag_limits", {}), "tag_limits"))


@dataclass(frozen=True)
class SelectionPolicy:
    recent_window: int = 5
    recent_mode: str = "prefer"  # require forbids all recent images
    recent_scope: str = "monitor"  # monitor or all (whole desktop, by shuffle event)
    avoid_duplicates: bool = True
    collection_limits: dict[str, int] = field(default_factory=dict)
    tag_limits: dict[str, int] = field(default_factory=dict)
    monitor_rules: dict[str, ImageRule] = field(default_factory=dict)
    groups: tuple[MonitorGroup, ...] = ()

    def __post_init__(self):
        if type(self.recent_window) is not int or not 0 <= self.recent_window <= 100:
            raise ValueError("Recent history must be between 0 and 100.")
        if self.recent_mode not in {"prefer", "require"}:
            raise ValueError("Recent rule must be Prefer or Require.")
        if self.recent_scope not in {"monitor", "all"}:
            raise ValueError("Recent scope must be monitor or all.")
        if type(self.avoid_duplicates) is not bool:
            raise ValueError("Avoid duplicates must be boolean.")
        _caps(self.collection_limits, "collection_limits")
        _caps(self.tag_limits, "tag_limits")
        if not isinstance(self.monitor_rules, dict) or any(
            not isinstance(key, str) or not key.strip()
            or not isinstance(value, ImageRule)
            for key, value in self.monitor_rules.items()
        ):
            raise ValueError("Invalid monitor rules.")
        if not isinstance(self.groups, tuple) or any(
            not isinstance(group, MonitorGroup) for group in self.groups
        ) or len({group.id for group in self.groups}) != len(self.groups):
            raise ValueError("Invalid or duplicate monitor groups.")

    def rules_for(self, monitor_id: str) -> tuple[ImageRule, ...]:
        result = []
        if monitor_id in self.monitor_rules:
            result.append(self.monitor_rules[monitor_id])
        result.extend(group.rule for group in self.groups if monitor_id in group.monitor_ids)
        return tuple(result)

    def to_dict(self) -> dict:
        return {
            "recent_window": self.recent_window, "recent_mode": self.recent_mode,
            "recent_scope": self.recent_scope,
            "avoid_duplicates": self.avoid_duplicates,
            "collection_limits": self.collection_limits, "tag_limits": self.tag_limits,
            "monitor_rules": {key: rule.to_dict() for key, rule in self.monitor_rules.items()},
            "groups": [group.to_dict() for group in self.groups],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SelectionPolicy":
        if not isinstance(data, dict):
            raise ValueError("Invalid selection policy.")
        raw_rules = data.get("monitor_rules", {})
        if not isinstance(raw_rules, dict):
            raise ValueError("Invalid monitor rules.")
        raw_groups = data.get("groups", [])
        if not isinstance(raw_groups, list):
            raise ValueError("Invalid monitor groups.")
        return cls(
            recent_window=data.get("recent_window", 5),
            recent_mode=data.get("recent_mode", "prefer"),
            recent_scope=data.get("recent_scope", "monitor"),
            avoid_duplicates=data.get("avoid_duplicates", True),
            collection_limits=_caps(data.get("collection_limits", {}), "collection_limits"),
            tag_limits=_caps(data.get("tag_limits", {}), "tag_limits"),
            monitor_rules={key: ImageRule.from_dict(rule) for key, rule in raw_rules.items()},
            groups=tuple(MonitorGroup.from_dict(group) for group in raw_groups),
        )


@dataclass(frozen=True)
class SmartCollection:
    collection_id: str
    include_tags: tuple[str, ...]
    exclude_tags: tuple[str, ...] = ()
    match_all: bool = False

    def __post_init__(self):
        if not self.collection_id or not self.include_tags:
            raise ValueError("Smart collection needs an ID and included tags.")
        _ids(self.include_tags, "include_tags")
        _ids(self.exclude_tags, "exclude_tags")
        if type(self.match_all) is not bool:
            raise ValueError("match_all must be boolean.")

    def matches(self, tags: set[str]) -> bool:
        return (not bool(tags.intersection(self.exclude_tags)) and
                (set(self.include_tags).issubset(tags) if self.match_all
                 else bool(tags.intersection(self.include_tags))))

    def to_dict(self) -> dict:
        return {"collection_id": self.collection_id, "include_tags": list(self.include_tags),
                "exclude_tags": list(self.exclude_tags), "match_all": self.match_all}

    @classmethod
    def from_dict(cls, data: dict) -> "SmartCollection":
        if not isinstance(data, dict):
            raise ValueError("Invalid smart collection.")
        return cls(data["collection_id"], _ids(data["include_tags"], "include_tags"),
                   _ids(data.get("exclude_tags", []), "exclude_tags"),
                   data.get("match_all", False))


def _load_json(path: Path, kind: str) -> list | dict:
    if not path.exists():
        return [] if kind == "smart" else {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read {kind} rules: {error}") from error
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"Unsupported {kind} rules format; file preserved.")
    key = "collections" if kind == "smart" else "policy"
    result = data.get(key)
    if not isinstance(result, list if kind == "smart" else dict):
        raise ValueError(f"Invalid {kind} rules format.")
    return result


def _save_json(path: Path, kind: str, value: list | dict) -> None:
    if path.exists():
        _load_json(path, kind)  # Preserve corrupt or future-version files.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({"version": 1,
            "collections" if kind == "smart" else "policy": value}, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_selection_policy(path: Path | None = None) -> SelectionPolicy:
    return SelectionPolicy.from_dict(_load_json(path or _settings_path("selection-rules.json"), "selection"))


def save_selection_policy(policy: SelectionPolicy, path: Path | None = None) -> None:
    _save_json(path or _settings_path("selection-rules.json"), "selection", policy.to_dict())


def load_smart_collections(path: Path | None = None) -> dict[str, SmartCollection]:
    entries = [SmartCollection.from_dict(item) for item in
               _load_json(path or _settings_path("smart-collections.json"), "smart")]
    if len({item.collection_id for item in entries}) != len(entries):
        raise ValueError("Duplicate smart collection IDs.")
    return {item.collection_id: item for item in entries}


def save_smart_collections(specs: dict[str, SmartCollection], path: Path | None = None) -> None:
    if any(key != value.collection_id for key, value in specs.items()):
        raise ValueError("Smart collection IDs do not match.")
    _save_json(path or _settings_path("smart-collections.json"), "smart",
               [item.to_dict() for item in specs.values()])
