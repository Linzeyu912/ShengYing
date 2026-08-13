# 群像 → 声影 · 资产包协议（草案 v0.1）

> 2026-08-13 · 状态：待双方评审
> 定位：群像（故事资产生产）与声影（视听制作）之间的**结构化交接格式**。
> 原则：机器可读、可追溯、幂等可重导、字段向后兼容（新增字段不破坏旧版本解析）。

---

## 1. 包结构

一个资产包是一个目录（可 zip 打包传输）：

```text
qunxiang_package/
├── manifest.json          # 包清单：版本、项目信息、实体索引
├── characters/            # 角色卡，一角色一文件
│   ├── chen_mo.json
│   └── xiao_ling.json
├── scenes/                # 场景卡，一场景一文件
│   └── alley_night.json
└── script/                # 剧本：按剧集组织
    └── ep01.json
```

### manifest.json

```json
{
  "package_version": "0.1",
  "exported_at": "2026-08-13T10:00:00+08:00",
  "source": { "tool": "qunxiang", "tool_version": "x.y.z", "project_ref": "夜雨" },
  "project": { "title": "夜雨", "description": "短剧示例", "genre": "悬疑" },
  "episodes": [{ "id": "ep01", "number": 1, "title": "巷口相遇", "script": "script/ep01.json" }],
  "characters": ["characters/chen_mo.json", "characters/xiao_ling.json"],
  "scenes": ["scenes/alley_night.json"]
}
```

## 2. 角色卡（characters/*.json）

```json
{
  "id": "char_chen_mo",
  "name": "陈默",
  "gender": "male",
  "age": "老年",
  "personality": ["沉默", "固执", "重情"],
  "appearance": "清瘦，灰白短发，常年一件旧雨衣",
  "voice_hint": {
    "description": "低沉沙哑的老年男声，语速慢",
    "reference_style": "低沉男声",
    "gender": "male",
    "age_group": "old"
  },
  "notes": "主角，全剧情绪起伏集中在第三集"
}
```

**映射到声影**：`characters/<char_id>/character.json`

| 群像字段 | 声影字段 | 说明 |
| --- | --- | --- |
| `name` / `appearance`+`personality` | `name` / `description` | 直接映射/合并 |
| `voice_hint.reference_style` | 音色绑定建议 | 导入时按名称模糊匹配音色库，列出候选由人确认 |
| `voice_hint.description` | 音色设计 Prompt 备选 | 库内无匹配时，可用作 VoxCPM 音色设计描述造新音色并固化 |
| `id` | 映射表 `qunxiang_id` | 写入角色卡，保证重导幂等 |

## 3. 场景卡（scenes/*.json）

```json
{
  "id": "scene_alley_night",
  "name": "雨夜巷口",
  "location": "老城巷子",
  "time": "深夜",
  "mood": "压抑、悬疑",
  "ambience_hint": { "tags": ["雨声", "夜", "城市远景"], "suggested_category": "天气" },
  "sfx_hints": [{ "tag": "雷声", "timing": "开场" }]
}
```

**映射到声影**：场景卡不直接生成音频，而是作为**场次的制作指引**：
- `ambience_hint.tags` → 混音时检索环境音库的候选标签
- `sfx_hints` → 音效插入建议（位置映射到行号由人确认）
- 场景卡存为 `scenes_meta/` 下的参考文件，供混音面板提示

## 4. 剧本（script/ep*.json）

```json
{
  "episode_id": "ep01",
  "number": 1,
  "title": "巷口相遇",
  "scenes": [
    {
      "scene_ref": "scene_alley_night",
      "name": "第一场·屋檐下",
      "lines": [
        { "character_ref": "char_chen_mo", "text": "雨下大了，进来躲躲吧。", "emotion": "平静" },
        { "character_ref": "char_xiao_ling", "text": "谢谢爷爷！", "emotion": "开心" }
      ]
    }
  ]
}
```

**映射到声影**：一个 `scenes[]` 元素 = 声影的一个"场次"（`scenes/<scene_id>/scene.json` 的台词骨架）。

- `character_ref` / `scene_ref` 通过映射表解析为本系统角色/场景
- `emotion` 取值对齐声影十情绪集：开心、悲伤、愤怒、惊讶、平静、紧张、温柔、严肃、调皮、疲惫；无法识别的值降级为"平静"并在导入报告中标记
- **导入只建骨架（场次 + 台词行 + 角色关联），不自动合成音频**——音频生成由人在工作台上逐场触发，保留审核环节

## 5. 导入流程

```text
上传/指定资产包目录
      │
      ▼
解析 manifest → 生成《导入预览报告》：
  · 将创建的项目/剧集/角色/场次清单
  · 音色绑定建议（库内匹配 / 需新建 / 无建议）
  · 异常项（未知情绪、缺失引用、重名冲突）
      │
      ▼ 人工确认
执行导入：
  1. 建项目 + 剧集
  2. 建角色（记录 qunxiang_id 映射，音色绑定留待确认）
  3. 建场景卡参考文件
  4. 建场次骨架（台词行）
      │
      ▼
工作台上：绑定音色 → 逐场批量生成 → 混音
```

**幂等**：以 `manifest.source.project_ref + qunxiang_id` 为键维护映射表（存 `projects/<pid>/import_map.json`）；重复导入同一包时执行更新而非重复创建。

## 6. 冲突处理规则

| 情况 | 策略 |
| --- | --- |
| 角色重名但 `qunxiang_id` 不同 | 视为新角色，名称后加序号 |
| 同一 `qunxiang_id` 重导 | 更新字段，保留已绑定的音色与已生成音频 |
| 情绪值不在十情绪集内 | 降级"平静" + 报告标记 |
| 剧本引用不存在的角色/场景 | 跳过该行 + 报告标记，不中断导入 |
| 包版本高于软件支持版本 | 拒绝导入并提示 |

## 7. 待评审问题

1. 群像侧能否按本协议导出？字段是否有出入（尤其 `voice_hint` 与情绪标签集）
2. 角色音色绑定的自动化程度：导入时自动匹配库内音色，还是一律人工确认？
3. 场景卡的 `ambience_hint` 是否需要更结构化的取值（受控标签集 vs 自由文本）
4. 多集项目是一次整包导入，还是支持单集增量包
5. 是否需要回传通道：声影的制作状态（已完成场次）回写给群像

## 8. 示例包

仓库内附最小示例：`examples/qunxiang_sample/`，可直接用于导入功能开发联调。
