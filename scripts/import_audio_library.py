# -*- coding: utf-8 -*-
"""
声影 · 音频素材库导入脚本
将组员交付的「音频库」按 assets/ 规范归档：
  - v1.1（修复版）→ assets/voices/<voice_id>/samples/（文件名规范化）
  - v1.0（原始版）→ assets/archive/voices_v1.0/（原样保留，可追溯）
  - 为每个音色生成 voice.json，并生成总索引 assets/index.json
"""
import json
import re
import shutil
from pathlib import Path

SRC_ROOT = Path(r"C:\Users\林泽羽\Desktop\音频库\音频库")
DST_ROOT = Path(r"D:\YH\ShengYing\assets")

# 音色目录名 -> (voice_id, 性别, 描述)
VOICES = {
    "低沉男声":   ("v_low_male",       "male",    "低沉、厚重的青年男声"),
    "大叔音":     ("v_uncle",          "male",    "成熟中年男声"),
    "少年音":     ("v_boy",            "male",    "清亮少年男声"),
    "御姐音":     ("v_yujie",          "female",  "成熟冷艳女声"),
    "温柔女声":   ("v_gentle_female",  "female",  "温柔平和女声"),
    "烟嗓音":     ("v_raspy",          "unknown", "沙哑烟嗓（性别待确认）"),
    "老人音（男）": ("v_old_male",     "male",    "苍老男声"),
    "老人音（女）": ("v_old_female",   "female",  "苍老女声"),
    "萝莉音":     ("v_loli",           "female",  "年幼女童声"),
}

EMOTIONS = ["开心", "悲伤", "愤怒", "惊讶", "平静", "紧张", "温柔", "严肃", "调皮", "疲惫"]

def normalize_name(fname: str):
    """规范化样本文件名：01_开心.wav；返回 (规范名, 情绪, 备注list)"""
    stem = Path(fname).stem  # e.g. 06_紧张改bug / 08_严肃（老年）
    notes = []
    m = re.match(r"^(\d+)_?(.*)$", stem)
    if not m:
        return None, None, ["文件名无法解析"]
    num, rest = m.group(1), m.group(2)
    emotion = None
    for e in EMOTIONS:
        if rest.startswith(e):
            emotion = e
            tail = rest[len(e):]
            break
    else:
        return None, None, [f"情绪无法识别: {rest}"]
    if "改" in tail:
        notes.append("v1.1 修复重录版")
    if "bug" in tail:
        notes.append("交付方仍标记 bug，待人工复核")
    extra = re.findall(r"（(.+?)）", tail)
    for x in extra:
        notes.append(f"特殊标注: {x}")
    return f"{num}_{emotion}.wav", emotion, notes

def main():
    index = {"library": "声影音频素材库", "version": "v1.1", "voices": [], "sfx": [], "ambience": []}
    # 1) v1.0 原样归档
    src_v10 = SRC_ROOT / "音频库v1.0"
    dst_v10 = DST_ROOT / "archive" / "voices_v1.0"
    if src_v10.exists():
        shutil.copytree(src_v10, dst_v10, dirs_exist_ok=True)
        print(f"[archive] v1.0 -> {dst_v10}")
    # 2) v1.1 规范化入库
    src_v11 = SRC_ROOT / "音频库v1.1"
    for folder, (vid, gender, desc) in VOICES.items():
        src_dir = src_v11 / folder
        if not src_dir.exists():
            print(f"[skip] {folder} 不存在于 v1.1")
            continue
        dst_dir = DST_ROOT / "voices" / vid / "samples"
        dst_dir.mkdir(parents=True, exist_ok=True)
        samples, issues = [], []
        seen = {}
        for f in sorted(src_dir.glob("*.wav")):
            canon, emotion, notes = normalize_name(f.name)
            if canon is None:
                issues.append({"file": f.name, "issue": notes[0]})
                shutil.copy2(f, dst_dir / f.name)
                continue
            if canon in seen:  # 同名重复（如 08_严肃 与 08_严肃改）
                issues.append({"file": f.name, "issue": f"与 {seen[canon]} 规范化后重名，保留两份，需人工取舍"})
                canon = canon.replace(".wav", "_dup.wav")
                if "v1.1 修复重录版" in notes:
                    notes.append("推荐优先采用本文件")
                else:
                    notes.append("疑似旧版残留，建议复核后移除")
            else:
                seen[canon] = f.name
            shutil.copy2(f, dst_dir / canon)
            entry = {"file": f"samples/{canon}", "emotion": emotion, "source_file": f.name}
            if notes:
                entry["notes"] = notes
                issues.append({"file": f.name, "issue": "; ".join(notes)})
            samples.append(entry)
        voice_json = {
            "voice_id": vid,
            "name": folder,
            "mode": "reference_samples",   # 当前为实录音色样本库；VoxCPM 内置后可转为 clone 参考
            "gender": gender,
            "description": desc,
            "language": "zh",
            "emotions": sorted({s["emotion"] for s in samples}, key=EMOTIONS.index),
            "samples": samples,
            "bound_characters": [],
            "license": "original",          # 组员原创录制
            "version": "v1.1",
            "created_at": "2026-08-03",
        }
        if issues:
            voice_json["known_issues"] = issues
        (DST_ROOT / "voices" / vid / "voice.json").write_text(
            json.dumps(voice_json, ensure_ascii=False, indent=2), encoding="utf-8")
        index["voices"].append({"voice_id": vid, "name": folder, "gender": gender,
                                "sample_count": len(samples), "path": f"voices/{vid}/"})
        print(f"[voice] {folder} ({vid}): {len(samples)} 样本" + (f", {len(issues)} 条备注" if issues else ""))
    # 3) sfx / ambience 占位目录
    for d in ("sfx", "ambience"):
        (DST_ROOT / d).mkdir(parents=True, exist_ok=True)
        (DST_ROOT / d / ".gitkeep").write_text("", encoding="utf-8")
    (DST_ROOT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[index] -> {DST_ROOT / 'index.json'}")

if __name__ == "__main__":
    main()
