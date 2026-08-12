# -*- coding: utf-8 -*-
"""TTS 适配层：VoxCPM2 内置封装（常驻单例，懒加载）。

设计要点：
- 模型只加载一次，后续合成复用（摊掉 ~15s 加载开销）。
- seed 由服务层统一管理：调用方不传则随机生成并返回，
  保证每条生成记录都可复现（音色固化机制的基础）。
"""
from __future__ import annotations

import random
import threading
from pathlib import Path

import numpy as np

MODEL_DIR = str(Path(__file__).resolve().parent.parent / "models" / "VoxCPM2")

_lock = threading.Lock()
_model = None


def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from voxcpm import VoxCPM
                _model = VoxCPM.from_pretrained(MODEL_DIR, load_denoiser=False)
    return _model


def synthesize(
    text: str,
    reference_wav_path: str | None = None,
    seed: int | None = None,
    cfg_value: float = 2.0,
    inference_timesteps: int = 10,
) -> tuple[np.ndarray, int, int]:
    """合成语音，返回 (音频波形, 采样率, 实际使用的 seed)。

    模式自动判定：
    - text 以 "(描述)" 开头          → 音色设计
    - 提供 reference_wav_path        → 可控克隆
    - 两者皆无                        → 基础 TTS（语境感知）
    """
    if seed is None:
        seed = random.randint(0, 2**31 - 1)
    model = get_model()
    kwargs = dict(
        text=text,
        cfg_value=cfg_value,
        inference_timesteps=inference_timesteps,
        seed=seed,
    )
    if reference_wav_path:
        kwargs["reference_wav_path"] = reference_wav_path
    with _lock:  # GPU 推理串行化，本地单用户足够
        wav = model.generate(**kwargs)
    return wav, model.tts_model.sample_rate, seed


def detect_mode(text: str, reference_wav_path: str | None) -> str:
    if reference_wav_path:
        return "clone"
    if text.lstrip().startswith("(") or text.lstrip().startswith("（"):
        return "design"
    return "basic"
