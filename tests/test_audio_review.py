import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import json
import numpy as np
import soundfile as sf
from server import audio_review, library


class AudioReviewTests(unittest.TestCase):
    def test_signals(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/'test.wav'
            sr = 16000
            sf.write(p, np.zeros(sr*3), sr)
            self.assertTrue(audio_review.signal_check(p)['issues'])
            sf.write(p, np.ones(sr*3), sr)
            self.assertIn('疑似削波失真', audio_review.signal_check(p)['issues'])
            tone = .2*np.sin(2*np.pi*220*np.arange(sr)/sr)
            sf.write(p, np.concatenate([tone, np.zeros(sr*2), tone]), sr)
            self.assertGreater(audio_review.signal_check(p)['metrics']['longest_pause_sec'], 1.5)

    def test_model_failure_is_not_pass_and_manual_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            d = root/'voices/test'
            d.mkdir(parents=True)
            sf.write(d/'a.wav', .1*np.sin(np.arange(48000)), 16000)
            p = d/'voice.json'
            p.write_text(json.dumps(dict(voice_id='test', name='test', review_status='approved',
                review_notes='人工审核备注', samples=[dict(file='a.wav')])), encoding='utf-8')
            with patch.object(library, 'ASSETS_ROOT', root), patch.object(library, 'get_library'), patch.object(audio_review, 'listening_check', side_effect=RuntimeError('model unavailable')):
                result = audio_review.review_voice('test')
                self.assertEqual(result['status'], 'incomplete')
                self.assertTrue(result['errors'])
                self.assertEqual(json.loads(p.read_text(encoding='utf-8'))['review_status'], 'approved')
                with self.assertRaises(FileNotFoundError): audio_review.review_voice('../outside')
