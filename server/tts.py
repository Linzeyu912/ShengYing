# -*- coding: utf-8 -*-
"""TTS 适配层：VoxCPM2 内置封装（常驻单例，懒加载）。

设计要点：
- 模型只加载一次，后续合成复用（摊掉 ~15s 加载开销）。
- seed 由服务层统一管理：调用方不传则随机生成并返回，
  保证每条生成记录都可复现（音色固化机制的基础）。
"""
from __future__ import annotations

import random
import secrets
import threading
import os
import importlib.util
import importlib.metadata
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = os.environ.get("VOXCPM_MODEL_DIR", str(PROJECT_ROOT / "models" / "VoxCPM2"))
DEVICE = os.environ.get("VOXCPM_DEVICE", "auto")
_optimize_env = os.environ.get("VOXCPM_OPTIMIZE")
# torch.compile 对 CUDA 有帮助；CPU/auto 默认关闭，避免首次加载长时间编译。
OPTIMIZE = (
    _optimize_env.lower() not in ("0", "false", "no")
    if _optimize_env is not None
    else DEVICE.lower().startswith("cuda")
)
LOAD_DENOISER = os.environ.get("VOXCPM_LOAD_DENOISER", "0").lower() in ("1", "true", "yes")

_lock = threading.Lock()
_model = None


def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                if not Path(MODEL_DIR).is_dir():
                    raise RuntimeError(
                        f"VoxCPM2 模型目录不存在: {MODEL_DIR}；请先按 README 下载模型权重")
                from voxcpm import VoxCPM
                _model = VoxCPM.from_pretrained(
                    MODEL_DIR,
                    load_denoiser=LOAD_DENOISER,
                    device=DEVICE,
                    optimize=OPTIMIZE,
                )
    return _model


def synthesize(
    text: str,
    reference_wav_path: str | None = None,
    prompt_wav_path: str | None = None,
    prompt_text: str | None = None,
    seed: int | None = None,
    cfg_value: float = 2.0,
    inference_timesteps: int = 10,
    normalize: bool = False,
    denoise: bool = False,
) -> tuple[np.ndarray, int, int]:
    """合成语音，返回 (音频波形, 采样率, 实际使用的 seed)。

    模式自动判定：
    - text 以 "(描述)" 开头          → 音色设计
    - prompt_wav_path + prompt_text  → 极致克隆
    - 提供 reference_wav_path        → 可控克隆
    - 两者皆无                        → 基础 TTS（语境感知）
    """
    text = text.strip()
    if not text:
        raise ValueError("合成文本不能为空")
    if bool(prompt_wav_path) != bool(prompt_text and prompt_text.strip()):
        raise ValueError("极致克隆必须同时提供参考音频和精确转录文本")
    for label, path in (("参考音频", reference_wav_path), ("提示音频", prompt_wav_path)):
        if path and not Path(path).is_file():
            raise FileNotFoundError(f"{label}不存在: {path}")
    if denoise and not LOAD_DENOISER:
        raise ValueError("未加载降噪模型；请设置 VOXCPM_LOAD_DENOISER=1 后重启")
    if seed is None:
        seed = secrets.randbelow(2**31)
    model = get_model()
    kwargs = dict(
        text=text,
        cfg_value=cfg_value,
        inference_timesteps=inference_timesteps,
        normalize=normalize,
        denoise=denoise,
    )
    if reference_wav_path:
        kwargs["reference_wav_path"] = reference_wav_path
    if prompt_wav_path:
        kwargs["prompt_wav_path"] = prompt_wav_path
        kwargs["prompt_text"] = prompt_text.strip()
    with _lock:  # 推理和随机数状态一起串行化，保证记录的 seed 可复现
        random.seed(seed)
        np.random.seed(seed % (2**32))
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        wav = model.generate(**kwargs)
    return wav, model.tts_model.sample_rate, seed


def detect_mode(
    text: str,
    reference_wav_path: str | None = None,
    prompt_wav_path: str | None = None,
    prompt_text: str | None = None,
) -> str:
    if prompt_wav_path and prompt_text:
        return "ultimate_clone"
    if reference_wav_path:
        return "controllable_clone"
    if text.lstrip().startswith("(") or text.lstrip().startswith("（"):
        return "design"
    return "basic"


def runtime_status() -> dict:
    """返回轻量环境诊断；不加载模型权重。"""
    model_dir = Path(MODEL_DIR)
    status = {
        "ready": False,
        "model_dir": str(model_dir),
        "model_present": model_dir.is_dir(),
        "voxcpm_installed": importlib.util.find_spec("voxcpm") is not None,
        "device": DEVICE,
        "effective_device": DEVICE,
        "optimize": OPTIMIZE,
        "denoiser_enabled": LOAD_DENOISER,
        "model_loaded": _model is not None,
        "cuda_available": False,
        "cuda_device": None,
        "voxcpm_version": None,
    }
    if status["voxcpm_installed"]:
        try:
            status["voxcpm_version"] = importlib.metadata.version("voxcpm")
        except importlib.metadata.PackageNotFoundError:
            status["voxcpm_version"] = "source-checkout"
    if importlib.util.find_spec("torch") is not None:
        try:
            import torch
            status["cuda_available"] = torch.cuda.is_available()
            if DEVICE == "auto":
                status["effective_device"] = "cuda" if status["cuda_available"] else "cpu"
            if status["cuda_available"]:
                status["cuda_device"] = torch.cuda.get_device_name(0)
            status["torch_version"] = torch.__version__
        except Exception as exc:
            status["torch_error"] = str(exc)
    status["ready"] = status["model_present"] and status["voxcpm_installed"]
    return status


def model_identity() -> dict:
    status = runtime_status()
    return {
        "name": "OpenBMB/VoxCPM2",
        "local_dir": MODEL_DIR,
        "voxcpm_version": status.get("voxcpm_version"),
    }
