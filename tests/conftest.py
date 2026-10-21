import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import os
import tempfile

_test_dir = tempfile.TemporaryDirectory()
os.environ['QURATED_DB_PATH'] = str(Path(_test_dir.name) / 'test.db')
os.environ['ANTHROPIC_API_KEY'] = 'test-key-not-used'
os.environ['SESSION_SECRET'] = 'test-secret-not-for-production'
