# -*- coding: utf-8 -*-
"""角色管理：角色卡 + 音色绑定。

characters/
└── <char_id>/character.json
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

CHARACTERS_ROOT = Path(__file__).resolve().parent.parent / "characters"


def _write(char: dict) -> dict:
    cdir = CHARACTERS_ROOT / char["char_id"]
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "character.json").write_text(
        json.dumps(char, ensure_ascii=False, indent=2), encoding="utf-8")
    return char


def create_character(name: str, voice_id: str = "", default_emotion: str = "",
                     description: str = "", qunxiang_id: str = "") -> dict:
    char = {
        "char_id": "c_" + uuid.uuid4().hex[:8],
        "name": name,
        "description": description,
        "voice_id": voice_id,            # 绑定的音色（assets/voices/<voice_id>）
        "default_emotion": default_emotion,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if qunxiang_id:
        char["qunxiang_id"] = qunxiang_id
    return _write(char)


def list_characters() -> list[dict]:
    if not CHARACTERS_ROOT.exists():
        return []
    out = []
    for cdir in sorted(CHARACTERS_ROOT.iterdir()):
        f = cdir / "character.json"
        if f.exists():
            out.append(json.loads(f.read_text(encoding="utf-8")))
    return out


def get_character(char_id: str) -> dict | None:
    f = CHARACTERS_ROOT / char_id / "character.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def update_character(char_id: str, **fields) -> dict | None:
    char = get_character(char_id)
    if char is None:
        return None
    for k in ("name", "description", "voice_id", "default_emotion", "qunxiang_id"):
        if k in fields and fields[k] is not None:
            char[k] = fields[k]
    return _write(char)


def delete_character(char_id: str) -> bool:
    import shutil
    cdir = CHARACTERS_ROOT / char_id
    if cdir.exists():
        shutil.rmtree(cdir)
        return True
    return False
