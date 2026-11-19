"""Index evidenced character identifiers without guessing from asset numbers."""
from collections import defaultdict
from dataclasses import dataclass
import re

AVATAR = re.compile(r"(?:^|/)(?:chara_icon_(?:log|plf)|chara_thumb_[lms])_(\d{5}|\d{7,8})(?=_|\.|$)", re.I)
CHARACTER_IMAGE = re.compile(
    r"(?:^|/)chara_(?:battle_header|deck|feet|frame|gacha|icon_(?:log|plf)|piece|result|thumb_[lms])_"
    r"(\d{5}|\d{7,8})(?=_|\.|$)", re.I)
VOICE = re.compile(r"^voice_(?:adv|battle|home|unit|system)_(\d{5})(?:_|$)", re.I)


def normalize_role(value):
    value = str(value)
    return value[:5] if re.fullmatch(r"[0-9]{5}(?:[0-9]{2,3})?", value) else ""


def implemented_role(value):
    return normalize_role(value).startswith("1")


@dataclass(frozen=True)
class RoleScope:
    roles: frozenset[str] = frozenset()
    include_other: bool = False

    @classmethod
    def from_ids(cls, ids, include_other=False):
        roles = frozenset(normalize_role(value) for value in ids)
        if "" in roles:
            raise ValueError("Invalid character ID")
        return cls(roles, include_other)

    def allows(self, role):
        if self.roles:
            return role in self.roles
        return not role or self.include_other or implemented_role(role)

    @property
    def filters(self):
        return bool(self.roles) or not self.include_other

    def manifest(self):
        return dict(characters=sorted(self.roles), include_other_roles=self.include_other)


def avatar_rank(avatar):
    title = avatar.get("title", "").casefold()
    return (0 if title.startswith("chara_icon_log_") else 1 if title.startswith("chara_thumb_") else 2,
            title, avatar.get("file", ""))


def merge_role_catalog(entries):
    merged = {}
    for raw, entry in sorted(entries.items()):
        role = normalize_role(raw)
        if not role:
            continue
        row = merged.setdefault(role, {"names": {}, "avatars": {}, "assets": 0})
        row["assets"] += entry.get("assets", 0)
        row["avatars"].update({item["file"]: item for item in entry.get("avatars", []) if item.get("file")})
        for language, name in entry.get("names", {}).items():
            row["names"].setdefault(language, name)
    for row in merged.values():
        row["avatars"] = sorted(row["avatars"].values(), key=avatar_rank)
    return merged


def image_id(row, pattern=CHARACTER_IMAGE):
    if row.get("kind") not in {"image", "atlas"}:
        return ""
    found = set()
    for name in [row["title"], *row.get("container", [])]:
        if match := pattern.search(name.replace("\\", "/")):
            found.add(normalize_role(match[1]))
    return next(iter(found)) if len(found) == 1 else ""


def avatar(row):
    return image_id(row, AVATAR)


def bundle_image(record):
    return {"kind": "image", "title": record.get("_assetName", ""), "container": [record.get("_path", "")]}


def identifier(title, category):
    if category == "audio" and (match := VOICE.match(title)):
        return normalize_role(match[1])
    if category in {"spine", "live2d"} and (match := re.match(r"^(\d{5}|\d{7,8})(?=_|\.|$)", title)):
        return normalize_role(match[1])
    return ""


def package_id(path):
    match = re.search(r"(?:^|/)live_data/(?:characters|card|sd|special_skill(?:_animation)?|character_anim)/(\d{5}|\d{7,8})(?=_|\.|/|$)",
                      path, re.I)
    return normalize_role(match[1]) if match else ""


def index(rows):
    portraits = defaultdict(list)
    for row in rows:
        if found := avatar(row):
            portraits[found].append({k: row[k] for k in ("file", "source", "title", "path_id", "serialized_file", "container") if k in row})
    return {number: {"avatars": sorted(items, key=avatar_rank),
                     "names": {}}
            for number, items in sorted(portraits.items())}
