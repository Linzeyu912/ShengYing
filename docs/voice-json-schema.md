# voice.json 音色元数据 Schema

> 状态：**草案 v2** · 2026-08-23 · 在 v1.1 基础上扩展，新增 `voice_source` 与 `lora` 分支，**向后兼容**。
> 关联：[assets/README.md](../assets/README.md) · [lora-delivery-checklist.md](lora-delivery-checklist.md)

本文定义单个音色目录 `assets/voices/<voice_id>/voice.json` 的元数据规范。目标是让三种「音色来源」收敛到同一份 schema 下，下游（角色绑定、对白合成、混音、导出）不感知音色是怎么来的。

---

## 1. 三种音色来源（voice_source）

`voice_source` 是音色的**权威来源标识**，枚举三个值：

| `voice_source` | 含义 | 复现手段 | 必填字段 |
| --- | --- | --- | --- |
| `reference_samples` | 参考样本（组员录制 / 上传的克隆音） | 样本文件 + 精确转录，做零样本克隆 | `samples` |
| `design` | 音色设计（自然语言描述 + seed 固化） | `generation_params` 复算 | `generation_params` |
| `lora` | LoRA 微调 | 加载 LoRA 权重 | `lora` |

对应关系：

```
reference_samples ──► samples[]          （现状 v1.1，不动）
design             ──► generation_params （代码已预留 resolve 逻辑）
lora               ──► lora{}            （本次新增）
```

**复现哲学差异（框架层要点）**：

- `design` 是 **参数即音色**：存 `seed`，靠复算复现，零额外存储。
- `lora` 是 **权重即音色**：存权重文件，直接加载，训练元数据只做追溯、不参与复现。
- `reference_samples` 是 **样本即音色**：存参考音频 + 转录，每次合成现克隆。

---

## 2. 完整字段总表

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `voice_id` | string | ✅ | 唯一 ID，规范命名 `v_<name>`；用户导入为 `v_user_<hex8>` |
| `name` | string | ✅ | 音色名称 |
| `gender` | enum | ✅ | `male` / `female` / `unknown` |
| `description` | string | — | 音色描述，用于检索 |
| `language` | string | — | 默认 `zh` |
| `voice_source` | enum | ✅ | `reference_samples` / `design` / `lora` |
| `mode` | string | 兼容 | 历史字段（现值为 `reference_samples`），逐步废弃，读时以 `voice_source` 为准 |
| `emotions` | string[] | ✅ | 情绪覆盖清单（10 种标准情绪） |
| `samples` | object[] | 见分支 | 参考样本（`reference_samples` 必填；其余来源可选，仅作试听展示） |
| `generation_params` | object | 见分支 | 仅 `design` |
| `lora` | object | 见分支 | 仅 `lora` |
| `bound_characters` | string[] | — | 绑定的角色 ID |
| `license` | string | ✅ | `original`（原创）/ `authorized`（已授权）/ `unknown` |
| `consent_confirmed` | bool | ✅ | 是否已确认声音授权（入库强制项） |
| `version` | string | — | 音色自身版本 |
| `created_at` | string | — | `YYYY-MM-DD` |
| `known_issues` | object[] | — | 已知问题清单 |

---

## 3. 分支字段

### 3.1 reference_samples（现状 v1.1，不新增）

`samples[]` 每个元素：

```jsonc
{
  "file": "samples/01_开心.wav",   // 相对 voice 目录
  "emotion": "开心",
  "transcript": "……",             // 精确转录；有则走极致克隆，无则可控克隆
  "clone_mode": "ultimate_clone",  // ultimate_clone | controllable_clone
  "sample_id": "s_…",             // 可选
  "sha256": "…",                  // 可选
  "source_file": "01_开心.wav",   // 溯源：交付原始文件名
  "notes": ["v1.1 修复重录版"]     // 可选
}
```

### 3.2 design（generation_params，代码已预留）

无样本时靠 `generation_params` 复现。字段对齐 README「生成记录与音色固化」机制：

```jsonc
"generation_params": {
  "seed": 42,                              // 音色身份证（复现关键）
  "prompt_text": "低沉磁性的青年男声",      // 音色设计描述
  "tts_mode": "design",                    // 生成模式
  "cfg_value": 3.0,                        // 可选
  "inference_timesteps": 30,               // 可选
  "reference_asset": "voices/v_xxx/samples/01_平静.wav",  // 可选：克隆参考资产
  "reference_wav_path": null               // 可选：兼容旧记录
}
```

### 3.3 lora（本次新增，重点）

```jsonc
"lora": {
  "engine": "cosyvoice2",                 // 引擎标识（见 3.4）
  "base_model": "CosyVoice2-0.5B",        // 基座模型名/版本
  "weight_path": "lora/model.safetensors",// 权重相对 voice 目录路径
  "weight_sha256": "…",                   // 权重校验（推荐，保证交付完整）
  "train_data": {
    "source": "授权干声 60min",            // 数据来源描述
    "total_duration_sec": 3600,            // 训练数据总时长
    "sample_count": 300,                   // 样本条数
    "sample_rate": 24000,                  // 采样率
    "consent_confirmed": true,             // 训练数据声音授权
    "license": "original"                  // original | authorized
  },
  "train_params": {                        // 训练超参，仅追溯
    "rank": 8,
    "alpha": 16,
    "learning_rate": 1e-4,
    "epochs": 100,
    "checkpoint": "step_1000"
  },
  "inference": {                           // 推理加载，可选
    "script": "scripts/lora_infer_cosyvoice.py",
    "notes": "加载基模型 + 该权重合成"
  },
  "emotion_coverage": ["平静", "开心"]      // 已验证稳定的情绪（LoRA 未必覆盖全部 10 种）
}
```

### 3.4 engine 取值约定

| `engine` | 引擎 | 权重典型后缀 |
| --- | --- | --- |
| `cosyvoice2` | CosyVoice 2 | `.pt` / `.safetensors` |
| `gptsovits` | GPT-SoVITS | `.pth` / `.ckpt` |
| `f5tts` | F5-TTS | `.pt` |
| `sparktts` | Spark-TTS | `.pt` |
| `other` | 其他，在 `inference.notes` 注明 | — |

> 引擎未定时先用 `other` 占位，`base_model` / `weight_path` 可留待确定后再填；schema 本身与引擎解耦。

---

## 4. 目录约定

LoRA 权重属大体积资产，**不入 git**，与 `samples/` 平级存放：

```text
assets/voices/<voice_id>/
├── voice.json          # 元数据（入 git）
├── samples/            # reference_samples 来源的样本（不入 git）
└── lora/               # lora 来源的权重（不入 git，见 .gitignore）
    └── model.safetensors
```

`weight_path` / `samples[].file` 一律使用**相对 voice 目录**的路径，不写绝对路径。

---

## 5. 向后兼容规则

- v1.1 现有 9 个音色文件**不改**；`voice_source` 缺失时按 `mode` 推断为 `reference_samples`。
- 代码读取一律用 `.get()` 带默认值，未知字段忽略、新增字段缺省不报错。
- `mode` 保留但标记 deprecated，新写入一律写 `voice_source`。

---

## 6. 代码接入点（待办，引擎确定后实施）

| 位置 | 改动 |
| --- | --- |
| `server/library.py` `_load_voices` | 透传 `voice_source`、`lora`、`generation_params` |
| `server/library.py` `resolve_voice_reference` | 按 `voice_source` 分派：`lora` 返回 `lora.weight_path` 而非样本路径 |
| `server/tts.py` / `synthesis.py` | 合成前根据 `voice_source` 走克隆 / design / lora 三条合成路径 |
| `server/records.py` | 生成记录里标注 `voice_source`，保留来源可追溯 |

> 这些改动等组员确认引擎后再落地，避免过早绑定实现。
