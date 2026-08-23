# -*- coding: utf-8 -*-
"""统一合成编排：把音色资产解析为 VoxCPM2 调用参数。"""
from __future__ import annotations

from . import library, tts

VALID_MODES = {"auto", "basic", "design", "controllable_clone", "ultimate_clone"}


def generate(
    text: str,
    voice_id: str = "",
    emotion: str = "",
    requested_mode: str = "auto",
    prompt_text_override: str = "",
    seed: int | None = None,
    cfg_value: float = 2.0,
    inference_timesteps: int = 10,
    normalize: bool = False,
    denoise: bool = False,
) -> tuple[object, int, dict]:
    if requested_mode not in VALID_MODES:
        raise ValueError(f"不支持的合成模式: {requested_mode}")
    if not 1.0 <= cfg_value <= 3.0:
        raise ValueError("cfg_value 应在 1.0–3.0 之间")
    if not 4 <= inference_timesteps <= 30:
        raise ValueError("inference_timesteps 应在 4–30 之间")

    ref = library.resolve_voice_reference(voice_id, emotion)
    if ref.get("voice_source") == "lora":
        raise ValueError(
            "该音色为 LoRA 微调来源，当前合成路径尚未接入（待引擎确定后实施）；"
            "请改用 reference_samples 或 design 来源的音色。")
    params = ref.get("generation_params") or {}
    source_text = params.get("text") or ""
    effective_text = text
    if source_text.startswith("(") and ")" in source_text:
        effective_text = source_text[:source_text.index(")") + 1] + text
    if params:
        seed = seed if seed is not None else params.get("seed")
        cfg_value = params.get("cfg_value", cfg_value)
        inference_timesteps = params.get("inference_timesteps", inference_timesteps)

    reference = ref.get("path")
    transcript = (prompt_text_override or ref.get("transcript") or
                  params.get("prompt_text") or "").strip()
    mode = requested_mode
    if mode == "auto":
        preferred = ref.get("clone_mode") or params.get("tts_mode") or ""
        if reference:
            mode = "ultimate_clone" if preferred == "ultimate_clone" and transcript else "controllable_clone"
        else:
            mode = "design" if effective_text.lstrip().startswith(("(", "（")) else "basic"

    if mode in ("controllable_clone", "ultimate_clone") and not reference:
        raise ValueError("克隆模式需要绑定一个包含参考音频的音色")
    if mode == "ultimate_clone" and not transcript:
        raise ValueError("极致克隆需要参考音频的精确转录文本")

    reference_arg = reference if mode in ("controllable_clone", "ultimate_clone") else None
    prompt_arg = reference if mode == "ultimate_clone" else None
    prompt_text_arg = transcript if mode == "ultimate_clone" else None
    wav, sr, used_seed = tts.synthesize(
        text=effective_text,
        reference_wav_path=reference_arg,
        prompt_wav_path=prompt_arg,
        prompt_text=prompt_text_arg,
        seed=seed,
        cfg_value=cfg_value,
        inference_timesteps=inference_timesteps,
        normalize=normalize,
        denoise=denoise,
    )
    meta = {
        "text": text,
        "mode": tts.detect_mode(effective_text, reference_arg, prompt_arg, prompt_text_arg),
        "voice_id": voice_id or None,
        "emotion": emotion or None,
        "seed": used_seed,
        "cfg_value": cfg_value,
        "inference_timesteps": inference_timesteps,
        "normalize": normalize,
        "denoise": denoise,
        "source_sample_id": ref.get("sample_id"),
        "reference_asset": ref.get("asset_path"),
        "prompt_text": prompt_text_arg,
        "model": tts.model_identity(),
    }
    return wav, sr, meta
