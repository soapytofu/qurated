import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import os
import tempfile

_test_dir = tempfile.TemporaryDirectory()
os.environ['QURATED_DB_PATH'] = str(Path(_test_dir.name) / 'test.db')
os.environ['ANTHROPIC_API_KEY'] = 'test-key-not-used'
os.environ['SESSION_SECRET'] = 'test-secret-not-for-production'
os.environ['SEARCH_QUERY_EXPANSION'] = '0'  # Tests opt in with mock providers; never spend API credit.
os.environ['FEEDBACK_DB_PATH'] = str(Path(_test_dir.name) / 'feedback-mail.db')
os.environ['FEEDBACK_MAIL_URL'] = ''  # Tests must never send real email.
os.environ['FEEDBACK_MAIL_SECRET'] = ''
