# LoRA 音色交付清单

> 给音频组做 LoRA 音色的同学：训练完成后，按本清单打包交付，声影侧才能直接入库复用。
> 元数据规范见 [voice-json-schema.md](voice-json-schema.md)。

---

## 一、要交付的东西（文件）

| # | 文件 / 信息 | 是否必交 | 说明 |
| --- | --- | --- | --- |
| 1 | LoRA 权重文件 | ✅ | 最终选定的 checkpoint（不是全部，只要最终那个） |
| 2 | 基座模型标识 | ✅ | 引擎名 + 基座模型名/版本（如 `CosyVoice2-0.5B`） |
| 3 | 试听样例 | ✅ | 用该音色合成的 3~5 句 wav，覆盖主要情绪，放 `samples/` |
| 4 | 元数据 JSON | ✅ | 按下文模板填，随包一起交 |
| 5 | 训练数据来源说明 | ✅ | 数据从哪来、是否已授权 |
| 6 | 训练超参 | 推荐 | rank / lr / epochs / checkpoint 等，用于追溯 |

---

## 二、元数据 JSON（照抄改填）

把下面的 `<…>` 替换成实际值，`//` 注释删掉后保存为 `voice.json`：

```jsonc
{
  "voice_id": "v_<拼音或角色名>",          // 例：v_chenyu
  "name": "<音色名>",
  "gender": "<male | female | unknown>",
  "description": "<一句话描述，便于检索>",
  "language": "zh",
  "voice_source": "lora",
  "emotions": ["<已验证稳定的情绪，逐个列>"],
  "samples": [
    {
      "file": "samples/01_平静.wav",
      "emotion": "平静",
      "transcript": "<该句的精确文本>"
    }
  ],
  "lora": {
    "engine": "<cosyvoice2 | gptsovits | f5tts | sparktts | other>",
    "base_model": "<基座模型名+版本>",
    "weight_path": "lora/<权重文件名>",
    "weight_sha256": "<权重文件 sha256，可后补>",
    "train_data": {
      "source": "<训练数据来源一句话>",
      "total_duration_sec": <总时长秒数>,
      "sample_count": <样本条数>,
      "sample_rate": <采样率>,
      "consent_confirmed": <true | false>,
      "license": "<original | authorized>"
    },
    "train_params": {
      "rank": <rank>,
      "alpha": <alpha>,
      "learning_rate": <lr>,
      "epochs": <epochs>,
      "checkpoint": "<最终选定的 checkpoint 名>"
    },
    "inference": {
      "script": "<推理脚本路径，可选>",
      "notes": "<怎么加载这个权重合成，一句话>"
    },
    "emotion_coverage": ["<已验证稳定的情绪，与顶层 emotions 一致>"]
  },
  "bound_characters": [],
  "license": "<original | authorized>",
  "consent_confirmed": <true | false>,
  "version": "v1.0",
  "created_at": "<YYYY-MM-DD>",
  "known_issues": []
}
```

---

## 三、目录怎么放

交付时按这个结构打包（或直接放到对应 `voice_id` 目录）：

```text
v_<voice_id>/
├── voice.json
├── samples/
│   ├── 01_平静.wav
│   └── 02_开心.wav
└── lora/
    └── <权重文件名>
```

> 权重和音频**不入 git**（`.gitignore` 已覆盖 `assets/voices/*/lora/` 与 `samples/*.wav`），走团队网盘或 `scripts/import_audio_library.py` 导入。

---

## 四、交付前自检（三条硬标准）

1. **授权**：训练数据是否已拿到声音授权？（没有授权不能入库，这条是硬约束）
2. **过拟合检查**：用训练数据里**没有**的句子合成，听是否自然——若只会念训练过的句子，需减步数重训。
3. **完整可加载**：权重文件能单独被基座模型加载、合成成功，不依赖训练现场的其他中间文件。

---

## 五、交付后声影侧会做什么（透明化）

1. 校验元数据字段完整性（缺 `consent_confirmed` 或授权会打回）。
2. 按 `voice_source: "lora"` 入库，权重归档到 `assets/voices/<voice_id>/lora/`。
3. 该音色进入素材库检索/试听，可被角色绑定、跨剧集复用。
4. 合成时声影按 `lora` 分支加载权重出音，与克隆 / design 音色走同一套下游流程。
