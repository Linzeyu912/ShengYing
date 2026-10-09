import unittest
from types import SimpleNamespace
from unittest.mock import patch

from server import auditions


class AuditionTests(unittest.TestCase):
    def test_tagged_markdown_excludes_directions_and_screen_text(self):
        text = '# 剧本\n人物：少年小林、父亲。\n### 01｜00:00\n画面：下雨。\n屏幕文字：你好。\n台词【改编】父亲：“快走！”短暂停顿，转向小林：“把箱子给我。”小林：“好。”\n台词：无。'
        with patch.object(auditions.library, 'get_library', return_value={'voices': []}):
            plan = auditions.preview(text.encode(), 'test.md')
        self.assertEqual([row['name'] for row in plan['items']], ['父亲', '小林'])
        self.assertEqual(plan['items'][0]['text'], '快走！')

    def test_one_line_per_role_and_missing_dialogue(self):
        text = '测试\n核心人物：小林（温柔女性）、老张（年老男性）、小周（年轻男性）\n小林：今天我们一起回家吧。路上小心。\n老张：好的。\n小林：再见。'
        with patch.object(auditions.library, 'get_library', return_value={'voices': []}):
            plan = auditions.preview(text.encode(), 'test.txt')
        self.assertEqual(len(plan['items']), 3)
        self.assertEqual(plan['items'][0]['text'], '今天我们一起回家吧。')
        self.assertTrue(plan['items'][2]['line_note'])

    def test_matching_requires_traits_and_audio(self):
        voices = [{'voice_id':'v1','name':'温柔女声','gender':'female','samples':[{}]},
                  {'voice_id':'v2','name':'机械男声','gender':'male','samples':[]}]
        self.assertEqual(auditions.match_voice('温柔女性', voices)[0], 'v1')
        self.assertEqual(auditions.match_voice('机械男性', voices)[0], '')
        self.assertEqual(auditions.match_voice('某人', voices)[0], '')

    def test_design_saves_voice_and_character(self):
        row = SimpleNamespace(name='角色', description='机械男性', text='你好。',
                              voice_id='', emotion='平静', control='机械男声')
        with patch.object(auditions.synthesis, 'generate', return_value=([0],24000,{})) as generate, \
             patch.object(auditions.records, 'save_record', return_value={'record_id':'r1'}), \
             patch.object(auditions.records, 'promote_to_voice', return_value={'voice_id':'new'}) as promote, \
             patch.object(auditions.characters, 'create_character', return_value={'char_id':'c1'}):
            result = auditions.generate(row)
        self.assertEqual(generate.call_args.kwargs['requested_mode'], 'design')
        self.assertEqual(generate.call_count, 1)
        promote.assert_called_once()
        self.assertEqual(result['voice_id'], 'new')

    def test_existing_voice_does_not_create_new_voice(self):
        row = SimpleNamespace(name='角色', description='', text='你好。',
                              voice_id='v1', emotion='平静', control='自然')
        with patch.object(auditions.library, 'get_voice', return_value={'samples':[{}]}), \
             patch.object(auditions.synthesis, 'generate', return_value=([0],24000,{})) as generate, \
             patch.object(auditions.records, 'save_record', return_value={'record_id':'r1'}), \
             patch.object(auditions.records, 'promote_to_voice') as promote, \
             patch.object(auditions.characters, 'create_character', return_value={'char_id':'c1'}):
            result = auditions.generate(row)
        self.assertEqual(generate.call_args.kwargs['voice_id'], 'v1')
        promote.assert_not_called()
        self.assertFalse(result['created_voice'])


if __name__ == '__main__':
    unittest.main()
