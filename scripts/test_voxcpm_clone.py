# -*- coding: utf-8 -*-
"""VoxCPM2 本机验证：基础 TTS 与极致克隆。

用法：
    .venv/Scripts/python scripts/test_voxcpm_clone.py selftest
    .venv/Scripts/python scripts/test_voxcpm_clone.py ultimate --reference ref.wav --prompt-text "参考音频原文"

无 NVIDIA GPU 时自动使用 CPU；selftest 会先生成一条合成参考音，再用它验证
完整的 prompt_wav_path + prompt_text + reference_wav_path 极致克隆链路。
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import soundfile as sf
import torch
from voxcpm import VoxCPM

MODEL_DIR = Path("models/VoxCPM2")
OUT_DIR = Path("outputs/voxcpm_test")
REFERENCE_TEXT = "你好，这是一段用于验证声音克隆功能的参考语音。"
CLONE_TEXT = "声影工坊的极致声音克隆链路已经成功运行。"


def generate(model, output: Path, text: str, steps: int, **kwargs) -> Path:
    started = time.time()
    torch.manual_seed(20260815)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(20260815)
    wav = model.generate(
        text=text,
        cfg_value=2.0,
        inference_timesteps=steps,
        **kwargs,
    )
    sf.write(str(output), wav, model.tts_model.sample_rate)
    duration = len(wav) / model.tts_model.sample_rate
    elapsed = time.time() - started
    print(f"[out] {output} | {duration:.2f}s | 推理 {elapsed:.1f}s | RTF {elapsed / duration:.2f}", flush=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("basic", "selftest", "ultimate"), nargs="?", default="selftest")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--prompt-text")
    parser.add_argument("--text", default=CLONE_TEXT)
    parser.add_argument("--steps", type=int, default=4)
    args = parser.parse_args()

    if not MODEL_DIR.is_dir():
        parser.error(f"模型目录不存在: {MODEL_DIR}")
    if args.mode == "ultimate" and (not args.reference or not args.prompt_text):
        parser.error("ultimate 模式必须同时提供 --reference 和 --prompt-text")
    if args.reference and not args.reference.is_file():
        parser.error(f"参考音频不存在: {args.reference}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    device = os.environ.get("VOXCPM_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
    optimize = device.startswith("cuda")
    print(f"[load] {MODEL_DIR} | device={device} | optimize={optimize}", flush=True)
    started = time.time()
    model = VoxCPM.from_pretrained(
        str(MODEL_DIR),
        load_denoiser=False,
        device=device,
        optimize=optimize,
    )
    print(f"[load] 完成，耗时 {time.time() - started:.1f}s", flush=True)

    if args.mode == "basic":
        generate(model, OUT_DIR / "basic.wav", args.text, args.steps)
        return

    if args.mode == "selftest":
        reference = generate(model, OUT_DIR / "cpu_reference.wav", REFERENCE_TEXT, args.steps)
        prompt_text = REFERENCE_TEXT
    else:
        reference = args.reference.resolve()
        prompt_text = args.prompt_text.strip()

    generate(
        model,
        OUT_DIR / "ultimate_clone.wav",
        args.text,
        args.steps,
        prompt_wav_path=str(reference),
        prompt_text=prompt_text,
        reference_wav_path=str(reference),
    )


if __name__ == "__main__":
    main()
