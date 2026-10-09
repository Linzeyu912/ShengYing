# -*- coding: utf-8 -*-
"""剧本 → 自动配音：解析分镜头剧本 docx，自动匹配音色，批量合成纯人声对白。

纯新增模块，不依赖修改现有 server/*。复用点：
- characters.create_character        建角色 + 绑定音色
- dialogue.run_batch                 批量合成台词（产出生成记录，可追溯）
- records.resolve_audio / mixer._load_wav  读音频 / 转单声道 48k
- library.get_library                音色库读取
"""
from __future__ import annotations

import json
import re
import zipfile
from html import unescape
from io import BytesIO
from pathlib import Path

import numpy as np
import soundfile as sf

from . import characters, dialogue, library, mixer, records

TARGET_SR = mixer.TARGET_SR

# 镜头内容里的标签（配音只关心「台词」「字幕」）
_CONTENT_TAGS = ["画面", "音效", "台词", "字幕", "运动", "动作", "情绪"]

# 语气词 → 标准情绪（(关键词, 情绪)，按此顺序命中，用于选克隆参考样本）
_TONE_TO_EMOTION = [
    ("紧张", "紧张"),
    ("轻蔑", "严肃"), ("嘲讽", "严肃"), ("严肃", "严肃"),
    ("愤怒", "愤怒"), ("生气", "愤怒"),
    ("笑", "开心"), ("开心", "开心"), ("高兴", "开心"),
    ("错愕", "惊讶"), ("惊讶", "惊讶"),
    ("哭", "悲伤"), ("鼻音", "悲伤"), ("哽咽", "悲伤"), ("难过", "悲伤"),
    ("温柔", "温柔"), ("放缓", "温柔"), ("温和", "温柔"),
    ("疲惫", "疲惫"),
    ("调皮", "调皮"),
    ("低声", "平静"), ("平淡", "平静"), ("平静", "平静"), ("自语", "平静"),
    ("头也不抬", "平静"), ("公事公办", "平静"),
]
_TONE_KEYWORDS = [kw for kw, _ in _TONE_TO_EMOTION]

# 匹配「角色：」标记： （角色，语气）： 或  角色（语气）？：
_MARKER_RE = re.compile(
    r'[（(]([^）)]+)[）)][：:]?'
    r'|'
    r'([^：:（）()。，、！？!?·\s]{1,6})(?:[（(]([^）)]*)[）)])?[：:]'
)


# ---------------- ① docx → 段落文本 ----------------

def extract_script_bytes(data: bytes, filename: str) -> list[str]:
    """从上传内容读取 DOCX、TXT 或 Markdown，不需要先落盘。"""
    suffix = Path(filename).suffix.lower()
    if suffix in (".txt", ".md", ".markdown"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("gb18030")
        if suffix in (".md", ".markdown"):
            text = re.sub(r'^\s{0,3}#{1,6}\s+', '', text, flags=re.M)
            text = re.sub(r'^\s*(?:>\s*|[-+*]\s+)', '', text, flags=re.M)
            text = re.sub(r'\*\*(.+?)\*\*|__(.+?)__', lambda m: m.group(1) or m.group(2), text)
        return [line.strip() for line in text.splitlines() if line.strip()]
    if suffix != ".docx":
        raise ValueError(f"仅支持 .docx / .txt / .md / .markdown 文件: {filename}")
    try:
        xml = zipfile.ZipFile(BytesIO(data)).read("word/document.xml").decode("utf-8")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
        raise ValueError("DOCX 文件损坏或不是有效的 Word 文档") from exc
    out = []
    for para in re.findall(r'<w:p[ >].*?</w:p>', xml, re.S):
        texts = re.findall(r'<w:t[^>]*>(.*?)</w:t>', para, re.S)
        line = unescape("".join(texts)).strip()
        if line:
            out.append(line)
    return out


# 保留旧函数名，兼容现有调用和第三方脚本。
extract_script_text = extract_script_bytes


def extract_docx_text(path) -> list[str]:
    p = Path(path)
    return extract_script_bytes(p.read_bytes(), p.name)


# ---------------- ② 段落 → 结构化剧本 ----------------

def _parse_roles(text: str) -> list[dict]:
    """解析「核心人物：周言（新任女老板）、王姐（九年老仓库内勤）…」"""
    roles = []
    for m in re.finditer(r'([^、（）()]+?)[（(]([^）)]*)[）)]', text):
        name = m.group(1).strip()
        if name:
            roles.append({"name": name, "desc": (m.group(2) or "").strip()})
    return roles


def _split_by_tag(content: str) -> dict:
    """把镜头内容按固定标签切分为 {标签: 内容}。"""
    positions = []
    for tag in _CONTENT_TAGS:
        start = 0
        while True:
            idx = content.find(tag, start)
            if idx == -1:
                break
            positions.append((idx, tag))
            start = idx + len(tag)
    positions.sort()
    result = {}
    for i, (pos, tag) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(content)
        result[tag] = content[pos + len(tag):end].strip()
    return result


def _split_role_tone(s: str, known_roles: list[str]) -> tuple[str, str]:
    """把「周言，头也不抬 / 王姐紧张 / 周言低声自语」拆成 (角色, 语气)。"""
    s = s.strip()
    for sep in "，,、":
        if sep in s:
            role, tone = s.split(sep, 1)
            return role.strip(), tone.strip()
    for r in sorted(known_roles, key=len, reverse=True):
        if s.startswith(r):
            return r, s[len(r):].strip()
    for kw in sorted(_TONE_KEYWORDS, key=len, reverse=True):
        if s.endswith(kw):
            return s[:-len(kw)].strip(), kw
    return s, ""


def _normalize_role(role: str, known_roles: list[str]) -> str:
    """把台词角色名归并到核心人物名（男同事→年轻男同事 等），
    长度门槛避免「同事」这类 2 字泛称被误归并到「年轻男同事」。"""
    if role in known_roles:
        return role
    if len(role) >= 3:
        for r in known_roles:
            if role in r or r in role:
                return r
    return role


def _parse_dialogue_block(text: str, known_roles: list[str]) -> list[tuple[str, str, str]]:
    """解析台词内容，返回 [(角色, 语气, 文本)]。"""
    text = text.strip().lstrip("：:")
    if not text or text[0] == "无":
        return []
    result = []
    last_role, last_tone, last_end = None, "", 0
    for m in _MARKER_RE.finditer(text):
        seg = text[last_end:m.start()].strip()
        if last_role and seg:
            result.append((last_role, last_tone, seg))
        if m.group(1):  # （角色，语气）：
            role, tone = _split_role_tone(m.group(1), known_roles)
        else:           # 角色（语气）？：
            role = m.group(2).strip()
            tone = (m.group(3) or "").strip()
        last_role, last_tone = role, tone
        last_end = m.end()
    seg = text[last_end:].strip()
    if last_role and seg:
        result.append((last_role, last_tone, seg))
    return result


def _strip_caption(text: str) -> str:
    """去掉字幕开头的（居中白字逐行淡入）这类说明，只留文本。"""
    return re.sub(r'^[（(][^）)]*[）)]', "", text.strip()).strip()


def _extract_lines(content: str, known_roles: list[str]) -> list[tuple[str, str, str]]:
    """从镜头内容提取台词/字幕，返回 [(角色, 语气, 文本)]。"""
    if not content:
        return []
    segs = _split_by_tag(content)
    lines = []
    if "台词" in segs:
        lines.extend(_parse_dialogue_block(segs["台词"], known_roles))
    if "字幕" in segs:
        caption = _strip_caption(segs["字幕"])
        if caption:
            lines.append(("旁白", "", caption))
    return lines


def _tone_to_emotion(tone: str) -> str:
    if not tone:
        return "平静"
    for kw, emo in _TONE_TO_EMOTION:
        if kw in tone:
            return emo
    return "平静"


def parse_shot_script(paragraphs: list[str]) -> dict:
    title = paragraphs[0] if paragraphs else ""
    hints = []
    for p in paragraphs:
        m = re.search(r'核心人物[：:](.+)', p)
        if m:
            hints = _parse_roles(m.group(1))
            break

    shots = []
    i = 0
    while i < len(paragraphs):
        m = re.match(r'^镜头\s*(\d+)', paragraphs[i])
        if m:
            no = int(m.group(1))
            title_text = paragraphs[i]
            content = ""
            if i + 1 < len(paragraphs) and not re.match(r'^镜头\s*\d+', paragraphs[i + 1]):
                content = paragraphs[i + 1]
                i += 1
            shots.append({"no": no, "title": title_text, "content": content})
        i += 1

    known = [r["name"] for r in hints] + ["旁白"]
    lines = []
    for shot in shots:
        for role, tone, text in _extract_lines(shot["content"], known):
            role = _normalize_role(role, known)
            lines.append({"shot": shot["no"], "role": role,
                          "emotion": _tone_to_emotion(tone), "text": text})
    return {"title": title, "cast_hints": hints, "lines": lines, "shot_count": len(shots)}


# ---------------- ③ 自动音色匹配 ----------------

def _infer_gender(text: str) -> str:
    if any(k in text for k in ("女", "姐", "姨", "母", "妹", "娘")):
        return "female"
    if any(k in text for k in ("男", "哥", "叔", "爷", "兄", "弟", "父亲", "爸爸", "少年")):
        return "male"
    return "unknown"


def _find(voices: list[dict], voice_id: str):
    return voice_id if any(v["voice_id"] == voice_id for v in voices) else None


def _match_voice(name: str, desc: str, voices: list[dict]) -> tuple[str | None, str]:
    text = name + desc
    if "旁白" in text or "解说" in text:
        return _find(voices, "v_low_male"), "旁白→低沉男声"
    gender = _infer_gender(text)
    if "萝莉" in text:
        return _find(voices, "v_loli"), "萝莉→萝莉音"
    if any(k in text for k in ("御姐", "中音", "老板", "主管", "干练")) and gender == "female":
        return _find(voices, "v_yujie"), "成熟女→御姐音"
    if any(k in text for k in ("老仓库", "老人", "年老", "岁", "五十", "六十", "七十",
                               "大姐", "大妈", "阿姨")) and gender == "female":
        return _find(voices, "v_old_female"), "年长女→老人音女"
    if any(k in text for k in ("老仓库", "老人", "年老", "岁", "五十", "六十", "七十",
                               "大爷", "伯伯")) and gender == "male":
        return _find(voices, "v_old_male"), "年长男→老人音男"
    if any(k in text for k in ("温柔", "温和")) and gender == "female":
        return _find(voices, "v_gentle_female"), "温柔女→温柔女声"
    if any(k in text for k in ("年轻", "少年", "小陈")) and gender == "male":
        return _find(voices, "v_boy"), "年轻男→少年音"
    if any(k in text for k in ("低沉", "大叔", "沧桑")) and gender == "male":
        return _find(voices, "v_low_male"), "低沉男→低沉男声"
    if gender == "female":
        return _find(voices, "v_gentle_female"), "女性兜底→温柔女声"
    if gender == "male":
        return _find(voices, "v_low_male"), "男性兜底→低沉男声"
    return _find(voices, "v_low_male"), "无法判断，兜底低沉男声(建议人工确认)"


def auto_cast(roles: list[dict], voices: list[dict], override: dict | None = None) -> tuple[dict, list[dict]]:
    override = override or {}
    cast, report = {}, []
    for r in roles:
        name, desc = r["name"], r.get("desc", "")
        vid = override.get(name)
        note = "手动指定" if name in override else None
        if vid is not None and not _find(voices, vid):
            note = f"指定音色不存在：{vid}"
            vid = None
        if vid is None:
            vid, note = _match_voice(name, desc, voices)
        cast[name] = vid
        voice = next((v for v in voices if v["voice_id"] == vid), None)
        report.append({
            "role": name, "desc": desc, "voice_id": vid, "note": note,
            "voice_name": voice.get("name", "") if voice else "",
            "ready": bool(voice and voice.get("samples")),
        })
    return cast, report


def preview_script(script: dict, cast_override: dict | None = None) -> dict:
    """整理角色并给出自动音色方案，供 CLI 和网页确认。"""
    lines = script.get("lines", [])
    if not lines:
        raise ValueError("剧本未解析出任何台词，请确认是分镜头剧本格式")
    hint_map = {h["name"]: h.get("desc", "") for h in script.get("cast_hints", [])}
    role_order = []
    for line in lines:
        if line["role"] not in role_order:
            role_order.append(line["role"])
    roles = [{"name": name, "desc": hint_map.get(name, "")} for name in role_order]
    cast, report = auto_cast(roles, library.get_library()["voices"], cast_override)
    return {
        **script,
        "roles": roles,
        "cast": cast,
        "cast_report": report,
        "line_count": len(lines),
    }


def preview_script_bytes(data: bytes, filename: str, cast_override: dict | None = None) -> dict:
    paragraphs = extract_script_bytes(data, filename)
    return preview_script(parse_shot_script(paragraphs), cast_override)


# ---------------- ④ 编排 ----------------

def _concat(scene: dict, gap_ms: int) -> np.ndarray:
    """严格按场次台词顺序拼接；禁止悄悄跳过任何缺失音频。"""
    gap = np.zeros(int(TARGET_SR * gap_ms / 1000), dtype=np.float32)
    parts = []
    for index, line in enumerate(scene["lines"], start=1):
        record_id = line.get("record_id")
        if not record_id:
            raise ValueError(f"第 {index} 句没有生成记录，无法严格合并")
        ap = records.resolve_audio(record_id)
        if ap is None:
            raise ValueError(f"第 {index} 句音频缺失，无法严格合并: {record_id}")
        if parts:
            parts.append(gap)
        parts.append(mixer._load_wav(ap))
    return np.concatenate(parts) if parts else np.zeros(TARGET_SR, dtype=np.float32)


def _resolve_characters(roles: list[dict], cast: dict) -> tuple[dict, list[dict]]:
    """优先复用同名角色；音色变更时更新绑定，避免制造重复角色。"""
    existing = characters.list_characters()
    char_ids, report = {}, []
    for role in roles:
        name, desc = role["name"], role.get("desc", "")
        voice_id = cast[name]
        matches = [item for item in existing if item.get("name") == name]
        char = next((item for item in matches if item.get("voice_id") == voice_id), None)
        action = "复用"
        if char is None and matches:
            char = characters.update_character(
                matches[0]["char_id"],
                voice_id=voice_id,
                description=desc or matches[0].get("description", ""),
            )
            action = "更新绑定"
        elif char is None:
            char = characters.create_character(name, voice_id, "", desc)
            existing.append(char)
            action = "新建"
        char_ids[name] = char["char_id"]
        report.append({
            "role": name, "char_id": char["char_id"],
            "voice_id": voice_id, "action": action,
        })
    return char_ids, report


def dub_parsed_script(script: dict, cast_override: dict | None = None, out_path=None,
                      scene_name: str = "", gap_ms: int = 500,
                      episode_id: str = "", merge_audio: bool = True) -> dict:
    """对已经解析的剧本执行逐句合成，并按需生成严格顺序合并音频。"""
    if not 0 <= gap_ms <= 10000:
        raise ValueError("台词间隔必须在 0–10000 毫秒之间")
    plan = preview_script(script, cast_override)
    lines, roles = plan["lines"], plan["roles"]
    cast, report = plan["cast"], plan["cast_report"]

    missing = [r["name"] for r in roles if not cast.get(r["name"])]
    if missing:
        raise ValueError(f"以下角色未能自动匹配音色，请用 --cast 指定: {missing}")

    unready = [item["role"] for item in report if not item.get("ready")]
    if unready:
        raise ValueError(f"以下角色所选音色缺少参考音频，请先导入可用音色: {unready}")

    char_ids, character_report = _resolve_characters(roles, cast)

    dlines = [{"character_id": char_ids[l["role"]],
               "text": l["text"], "emotion": l["emotion"]} for l in lines]

    scene = dialogue.run_batch(
        scene_name or script["title"] or "剧本配音", dlines, episode_id)

    wav = _concat(scene, gap_ms) if merge_audio else None
    if out_path and wav is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), wav, TARGET_SR)

    return {
        "title": script["title"],
        "roles": roles,
        "cast": cast,
        "cast_report": report,
        "character_report": character_report,
        "line_count": len(lines),
        "scene_id": scene["scene_id"],
        "episode_id": episode_id or None,
        "merged": merge_audio,
        "merge_mode": "script_order_strict" if merge_audio else None,
        "gap_ms": gap_ms if merge_audio else None,
        "out_path": str(out_path) if out_path and merge_audio else None,
        "duration_sec": round(len(wav) / TARGET_SR, 2) if wav is not None else None,
    }


def dub_script_bytes(data: bytes, filename: str, cast_override: dict | None = None,
                     out_path=None, scene_name: str = "", gap_ms: int = 500,
                     episode_id: str = "", merge_audio: bool = True) -> dict:
    """端到端处理网页上传内容，并保留调用方指定的独立输出文件。"""
    script = parse_shot_script(extract_script_bytes(data, filename))
    return dub_parsed_script(
        script, cast_override, out_path, scene_name, gap_ms, episode_id, merge_audio)


def merge_scene(scene_id: str, out_path, gap_ms: int = 500) -> dict:
    """把已经生成的逐句音频严格按场次中的剧本顺序合并。"""
    if not 0 <= gap_ms <= 10000:
        raise ValueError("台词间隔必须在 0–10000 毫秒之间")
    scene = dialogue.get_scene(scene_id)
    if scene is None:
        raise ValueError(f"场次不存在: {scene_id}")
    wav = _concat(scene, gap_ms)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), wav, TARGET_SR)
    return {
        "scene_id": scene_id,
        "scene_name": scene.get("name", ""),
        "line_count": len(scene.get("lines", [])),
        "merge_mode": "script_order_strict",
        "gap_ms": gap_ms,
        "duration_sec": round(len(wav) / TARGET_SR, 2),
        "out_path": str(out_path),
    }


def dub_script(docx_path, cast_override: dict | None = None, out_path=None,
               scene_name: str = "", gap_ms: int = 500,
               episode_id: str = "") -> dict:
    """端到端：解析剧本 → 匹配音色 → 批量合成 → 拼接纯人声。"""
    paras = extract_docx_text(docx_path)
    script = parse_shot_script(paras)
    return dub_parsed_script(
        script, cast_override, out_path, scene_name, gap_ms, episode_id)
