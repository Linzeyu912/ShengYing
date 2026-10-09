"""Offline signal checks and NISQA-TTS naturalness screening, not human review."""
import hashlib
import json
import math
import subprocess
import sys
import threading
import time
from pathlib import Path
import numpy as np
import soundfile as sf
from . import library

ROOT = Path(__file__).resolve().parents[1]
LOCK = threading.Lock()
NATURALNESS_THRESHOLD = 3.0  # Provisional triage threshold, not a calibrated pass/fail standard.


def signal_check(path):
    wav, sr = sf.read(path, always_2d=True)
    if not wav.size or not np.isfinite(wav).all():
        return {'issues': ['音频为空或包含无效数值'], 'metrics': {}}
    peak = float(np.max(np.abs(wav)))
    rms = float(np.sqrt(np.mean(wav ** 2)))
    clipped = float(np.mean(np.abs(wav) >= .999))
    frame = max(1, int(sr * .02))
    # Channel-independent power avoids cancellation of stereo channels.
    power = np.mean(wav ** 2, axis=1)
    levels = np.array([np.sqrt(np.mean(power[i:i+frame])) for i in range(0, len(wav), frame)])
    quiet = levels < max(.001, float(levels.max()) * .03)
    active = np.flatnonzero(~quiet)
    interior = quiet[active[0]:active[-1]+1] if active.size else np.array([], dtype=bool)
    longest = run = 0
    for silent in interior:
        run = run + 1 if silent else 0
        longest = max(longest, run)
    issues = []
    if rms < .001: issues.append('接近静音或整体音量过低')
    elif rms < .01: issues.append('整体音量偏低，请试听确认')
    if clipped > .001: issues.append('疑似削波失真')
    if longest * .02 > 1.5: issues.append('句中有超过1.5秒的低能量停顿，可能是正常表演，请复核')
    leading = float(active[0] * .02) if active.size else len(wav)/sr
    trailing = float((len(quiet)-1-active[-1]) * .02) if active.size else len(wav)/sr
    if max(leading, trailing) > 1.5: issues.append('首尾静音超过1.5秒')
    return dict(issues=issues, metrics=dict(duration_sec=round(len(wav)/sr, 3),
        peak=peak, rms_dbfs=round(20*math.log10(max(rms, 1e-12)), 2),
        clipping_ratio=clipped, longest_pause_sec=round(longest*.02, 2),
        leading_silence_sec=leading, trailing_silence_sec=trailing))


def listening_check(path):
    if not (ROOT / 'models/NISQA/weights/nisqa_tts.tar').is_file():
        raise RuntimeError('本地 NISQA-TTS 模型未安装')
    result = subprocess.run([sys.executable, str(ROOT/'scripts/nisqa_local_predict.py'), str(path)],
        cwd=ROOT, capture_output=True, timeout=180, encoding='utf-8', errors='replace')
    line = next((s for s in result.stdout.splitlines() if s.startswith('RESULT_JSON=')), None)
    if result.returncode or line is None:
        raise RuntimeError('听感模型推理失败，请检查本地模型及依赖')
    score = json.loads(line.split('=', 1)[1])['naturalness']
    if not math.isfinite(score): raise RuntimeError('听感模型返回无效评分')
    return dict(model='NISQA-TTS v1.0', naturalness=round(score, 3), threshold=NATURALNESS_THRESHOLD,
        issues=['自然度预测偏低，建议重点试听机械感或不自然表达'] if score < NATURALNESS_THRESHOLD else [],
        license='CC BY-NC-SA 4.0（仅限非商业使用）',
        scope='自然度预测，非实际人工听音；不能定位错读、怪腔，不判断情绪；中文动画音色可能误判。阈值为初始筛选策略。')


def review_voice(voice_id):
    root = (library.ASSETS_ROOT/'voices').resolve()
    directory = (root/voice_id).resolve()
    if directory.parent != root or not (directory/'voice.json').is_file():
        raise FileNotFoundError('音色不存在')
    if not LOCK.acquire(blocking=False): raise ValueError('已有本地审核正在运行，请稍后重试')
    try:
        meta = json.loads((directory/'voice.json').read_text(encoding='utf-8'))
        samples, issues, errors = [], [], []
        for sample in meta.get('samples', []):
            path = (directory/sample['file']).resolve()
            row = dict(file=sample['file'])
            try:
                if directory not in path.parents: raise ValueError('样本路径越界')
                row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                row['signal'] = signal_check(path)
                issues.extend(row['signal']['issues'])
                row['listening'] = listening_check(path)
                issues.extend(row['listening']['issues'])
            except Exception as exc:
                row['error'] = str(exc)
                errors.append(str(exc))
            samples.append(row)
        if not samples: errors.append('没有可审核的音频样本')
        report = dict(status='needs_review' if issues else 'incomplete' if errors else 'no_findings',
            checked_at=time.strftime('%Y-%m-%d %H:%M:%S'), issues=issues, errors=errors, samples=samples,
            offline=True, version='1.0')
        # Re-read under shared lock so concurrent manual notes are never overwritten.
        with library._review_lock:
            path = directory/'voice.json'
            latest = json.loads(path.read_text(encoding='utf-8'))
            latest['auto_review'] = report
            temp = path.with_suffix('.auto.tmp')
            temp.write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(path)
            library.get_library(force=True)
        return report
    finally:
        LOCK.release()
