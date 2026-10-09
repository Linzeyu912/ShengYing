import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from server import records, library, qwen_design


class QwenDesignTests(unittest.TestCase):
    def test_missing_install_reports_not_ready(self):
        with patch.object(qwen_design, 'PYTHON', Path('missing-python')):
            self.assertFalse(qwen_design.status()['ready'])

    def test_promote_keeps_design_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(records, 'RECORDS_ROOT', root/'records'), patch.object(library, 'ASSETS_ROOT', root/'assets'), patch.object(library, 'get_library'):
                record = records.save_record(np.zeros(2400), 24000, dict(text='你好', model={'name':'Qwen/VoiceDesign'},
                    mode='design', gender='female', age_group='青年（20–30岁）', timbre_tags=['明亮'],
                    control_instruction='清亮的女声', emotion='开心'))
                voice = records.promote_to_voice(record['record_id'], '测试音色')
                self.assertEqual(voice['collection'], '音色库新千问三')
                self.assertEqual(voice['gender'], 'female')
                self.assertEqual(voice['timbre_tags'], ['明亮'])
                self.assertEqual(voice['review_status'], 'unreviewed')
                self.assertEqual(voice['samples'][0]['transcript'], '你好')
