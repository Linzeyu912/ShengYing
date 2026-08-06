# -*- coding: utf-8 -*-
"""VoxCPM2 实测脚本：基础 TTS / 音色设计 / 可控克隆（用音色库样本）

用法：
    .venv/Scripts/python scripts/test_voxcpm_clone.py basic
    .venv/Scripts/python scripts/test_voxcpm_clone.py design
    .venv/Scripts/python scripts/test_voxcpm_clone.py clone
输出：outputs/voxcpm_test/
"""
import sys
import time
from pathlib import Path

import soundfile as sf
from voxcpm import VoxCPM

MODEL_DIR = "models/VoxCPM2"
OUT_DIR = Path("outputs/voxcpm_test")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 音色库参考样本：温柔女声 · 平静
REFERENCE = "assets/voices/v_gentle_female/samples/05_平静.wav"

CASES = {
    "basic": dict(
        text="声影素材库服务已经跑通，九种音色、九十一个样本全部入库。",
    ),
    "design": dict(
        text="(年长男性，声音沙哑低沉，语速缓慢)这件事啊，得从二十年前那个雨夜说起。",
    ),
    "clone": dict(
        text="你终于来了。我等这一天，已经等了很久很久。",
        reference_wav_path=REFERENCE,
    ),
}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "basic"
    kwargs = CASES[mode]
    t0 = time.time()
    print(f"[load] 加载模型 {MODEL_DIR} ...")
    model = VoxCPM.from_pretrained(MODEL_DIR, load_denoiser=False)
    print(f"[load] 完成，耗时 {time.time() - t0:.1f}s")

    t1 = time.time()
    print(f"[gen:{mode}] 参数: {kwargs}")
    wav = model.generate(cfg_value=2.0, inference_timesteps=10, **kwargs)
    dt = time.time() - t1
    sr = model.tts_model.sample_rate
    out = OUT_DIR / f"{mode}.wav"
    sf.write(str(out), wav, sr)
    dur = len(wav) / sr
    print(f"[gen:{mode}] 音频 {dur:.1f}s，合成耗时 {dt:.1f}s，RTF={dt / dur:.2f}")
    print(f"[out] {out}")


if __name__ == "__main__":
    main()
