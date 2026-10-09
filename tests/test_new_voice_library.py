import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from server import library


class NewVoiceLibraryTests(unittest.TestCase):
    def test_labels_and_search_and_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            voice = root / 'voices/test'
            (voice / 'samples').mkdir(parents=True)
            (voice / 'samples/ref.wav').write_bytes(b'RIFF')
            (voice / 'voice.json').write_text(json.dumps(dict(
                voice_id='test', name='test', collection='音色库（新）',
                gender='female', age_group='青年', timbre_tags=['烟嗓'],
                samples=[dict(file='samples/ref.wav', emotion='平静', transcript='你好')]
            )), encoding='utf-8')
            with patch.object(library, 'ASSETS_ROOT', root), patch.object(library, '_cache',
                    {'loaded_at': 0, 'voices': [], 'sfx': [], 'ambience': []}):
                item = library.get_library(force=True)['voices'][0]
                self.assertEqual(item['age_group'], '青年')
                self.assertEqual(item['timbre_tags'], ['烟嗓'])
                self.assertEqual(len(library.search_voices(q='烟嗓', gender='female')), 1)
                self.assertEqual(len(library.search_voices(q='青年')), 1)
                self.assertEqual(library.resolve_voice_reference('test')['transcript'], '你好')
                with self.assertRaises(ValueError):
                    library.save_voice_review('test', 'needs_review', '')
                reviewed = library.save_voice_review('test', 'needs_review', '发音待确认')
                self.assertEqual(reviewed['review_status'], 'needs_review')
                self.assertEqual(library.get_voice('test')['review_notes'], '发音待确认')
                reviewed = library.save_voice_review('test', 'approved', '人工已听')
                self.assertEqual(reviewed['review_status'], 'approved')
                with self.assertRaises(FileNotFoundError):
                    library.save_voice_review('../outside', 'approved', '')

    def test_legacy_labels_default(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            voice = root / 'voices/old'
            voice.mkdir(parents=True)
            (voice / 'voice.json').write_text('{"voice_id":"old","name":"old"}')
            with patch.object(library, 'ASSETS_ROOT', root):
                item = library._load_voices()[0]
                self.assertEqual(item['age_group'], '未标注')
                self.assertEqual(item['timbre_tags'], [])
