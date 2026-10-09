"""Script casting and one-line character auditions, independent of full dialogue dubbing."""
import re

from . import characters, library, records, script_dub, synthesis

TRAITS = {
    '年轻': ('年轻', '少年', '少女', '青年'),
    '年长': ('年老', '老人', '老年', '奶奶', '爷爷'),
    '童声': ('儿童', '小孩', '童声', '萝莉'),
    '温柔': ('温柔', '温和', '柔和'),
    '低沉': ('低沉', '浑厚', '低音'),
    '沙哑': ('沙哑', '嘶哑', '烟嗓'),
    '成熟': ('成熟', '御姐', '大叔', '干练'),
    '机械': ('机械', '机器人', '电子音'),
}


def traits(text):
    return {key for key, words in TRAITS.items() if any(word in text for word in words)}


def match_voice(description, voices):
    gender = script_dub._infer_gender(description)
    wanted = traits(description)
    candidates = []
    for voice in voices:
        if not voice.get('samples') or voice.get('voice_source') == 'lora':
            continue
        if gender != 'unknown' and voice.get('gender') != gender:
            continue
        available = traits(voice.get('name', '') + voice.get('description', ''))
        if wanted and not wanted.issubset(available):
            continue
        if not wanted and gender == 'unknown':
            continue
        candidates.append((len(wanted & available), voice))
    if candidates:
        voice = max(candidates, key=lambda item: item[0])[1]
        return voice['voice_id'], '库内音色符合：' + '、'.join(([gender] if gender != 'unknown' else []) + sorted(wanted))
    return '', '未找到符合已知特征的可用音色，将设计新音色' if wanted or gender != 'unknown' else '人物声音信息不足，请检查或补充声音描述；默认设计新音色'


def preview(data, filename):
    paragraphs = script_dub.extract_script_bytes(data, filename)
    parsed = script_dub.parse_shot_script(paragraphs)
    hints = {r['name']: r['desc'] for r in parsed['cast_hints']}
    lines = parsed['lines']
    tagged = [p for p in paragraphs if re.match(r'^台词(?:【[^】]*】)?', p)]
    if tagged:
        lines = []
        for paragraph in tagged:
            content = re.sub(r'^台词(?:【[^】]*】)?[：:]?', '', paragraph).strip()
            # Only quoted spoken text is read; stage directions between quotes are not speech.
            speaker = None
            emotion = '平静'
            for match in re.finditer(r'([^“”]*?)“([^”]+)”', content):
                prefix, text = match.groups()
                marker = re.search(r'([^，,：:。！？\s]{1,20})(?:[，,]([^：:]*))?[：:]\s*$', prefix)
                if marker:
                    name, tone = marker.groups()
                    # "父亲面对中控台" keeps the already established speaker.
                    if speaker and (name.startswith(speaker) or name.startswith(('转向', '面对', '短暂', '停顿', '停一拍', '短停'))):
                        name = speaker
                    speaker, emotion = name, script_dub._tone_to_emotion(tone or '')
                if speaker:
                    lines.append({'role': speaker, 'text': text, 'emotion': emotion})
            if '“' not in content and not content.startswith('无'):
                for name, tone, text in script_dub._parse_dialogue_block(content, list(hints)):
                    lines.append({'role': name, 'text': text, 'emotion': script_dub._tone_to_emotion(tone)})
        # Preserve useful age information from the explicit character list.
        for p in paragraphs:
            if p.startswith('人物：'):
                for name in dict.fromkeys(line['role'] for line in lines):
                    if '少年' + name in p:
                        hints.setdefault(name, '少年，年轻男性')
    elif not lines:
        for paragraph in paragraphs:
            if paragraph.startswith(('核心人物', '人物介绍', '角色介绍', '标题', '场景', '场次')):
                continue
            match = re.match(r'^([^：:（）()\s]{1,20})(?:[（(]([^）)]*)[）)])?[：:]\s*(.+)$', paragraph)
            if match:
                name, tone, text = match.groups()
                if name in ('画面', '音效', '动作', '情绪', '时间', '地点'):
                    continue
                lines.append({'role': name, 'text': text, 'emotion': script_dub._tone_to_emotion(tone or '')})
    if not lines and not hints:
        raise ValueError('未识别到角色。请使用“角色名（情绪）：台词”的 TXT、DOCX 或 Markdown，或分镜剧本格式。')
    names = list(dict.fromkeys([line['role'] for line in lines] + list(hints)))
    if len(names) > 100:
        raise ValueError('一次最多处理 100 个角色，请拆分剧本。')
    voices = library.get_library()['voices']
    rows = []
    for name in names:
        own = [line for line in lines if line['role'] == name]
        representative = next((line for line in own if 8 <= len(line['text']) <= 150), own[0] if own else None)
        text = representative['text'] if representative else f'你好，我是{name}，这是我的声音。'
        sentence = re.match(r'.*?[。！？!?](?:[”"])?', text)
        text = sentence.group() if sentence else text[:300]
        desc = hints.get(name, '')
        voice_id, reason = match_voice(name + ' ' + desc, voices)
        rows.append({'name': name, 'description': desc,
                     'control': desc or '自然清晰的普通话，适合该角色的声音',
                     'text': text, 'emotion': representative['emotion'] if representative else '平静',
                     'voice_id': voice_id, 'reason': reason,
                     'line_note': '' if representative else '剧本无台词，使用试音句'})
    return {'title': parsed['title'], 'items': rows,
            'note': '依据人物名称、设定和声音关键词匹配；请核对角色列表。新音色需试听确认。'}


def generate(row):
    if row.voice_id:
        voice = library.get_voice(row.voice_id)
        if not voice or not voice.get('samples'):
            raise ValueError('所选音色不存在或缺少参考音频，请重新选择')
    wav, sr, meta = synthesis.generate(
        text=row.text, voice_id=row.voice_id, emotion=row.emotion,
        requested_mode='auto' if row.voice_id else 'design',
        control_instruction='' if row.voice_id else row.control)
    meta.update({'audition_role': row.name, 'audition_description': row.description})
    record = records.save_record(wav, sr, meta)
    voice_id = row.voice_id
    if not voice_id:
        voice = records.promote_to_voice(record['record_id'], row.name + ' · 试音',
                                         script_dub._infer_gender(row.description + row.name))
        voice_id = voice['voice_id']
    char = characters.create_character(row.name, voice_id, row.emotion, row.description)
    return {'record_id': record['record_id'], 'voice_id': voice_id, 'char_id': char['char_id'],
            'audio_url': f"/api/tts/records/{record['record_id']}/audio",
            'created_voice': not bool(row.voice_id)}
