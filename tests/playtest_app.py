"""Local-only synthetic UI harness. Run from the repo root:
PYTHONPATH=app:tests app/.venv/bin/uvicorn playtest_app:app --host 127.0.0.1 --port 8766
No live sign-in, account database, course data or model calls are used.
"""
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
import json

_temp = tempfile.TemporaryDirectory()
os.environ['ANTHROPIC_API_KEY'] = 'synthetic-playtest-key'
os.environ['SESSION_SECRET'] = 'synthetic-playtest-session'
os.environ['QURATED_DB_PATH'] = str(Path(_temp.name) / 'accounts.db')
from test_retrieval import database
import courses


def synthetic_init(self, *args, **kwargs):
    self.__dict__.update(database().__dict__)


# Intercept construction before server import so local private source files are never read.
courses.CourseDB.__init__ = synthetic_init
import server
from fastapi import FastAPI
from fastapi.responses import FileResponse

server.db = database()
for i, course in enumerate(server.db.courses.values()):
    course['title'] = ['Introduction to Computing', 'Ethics and Technology', 'Stories on Screen'][i % 3]
    course['catalog']['description'] = 'Synthetic course for interface testing. No real student data.'
    course['catalog']['offered'][0]['sections'] = [dict(instructors=['Example instructor'],
        meetings=[dict(days=['T', 'Th'], time='1:00pm-2:15pm')])]
server.db._build_index()
server._maybe_reload = lambda: None
server.app.dependency_overrides[server.current_user] = lambda: dict(
    email='playtest@example.invalid', name='Demo Student', picture=None)
server.needs_q_data = lambda *args: (True, 0)


def synthetic_stream(client, kwargs):
    payload = json.loads(kwargs['messages'][-1]['content'])
    yield 'text', json.dumps(dict(picks=[dict(code=c['code'], reason='Matches your interests.', quote='')
        for c in payload['candidates'][:3]], advice='These are synthetic examples. Open a card to compare details.'))
    yield 'final', SimpleNamespace(stop_reason='end_turn', usage=SimpleNamespace(
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0))


server.pipeline.hedged_stream = synthetic_stream
app = FastAPI()


@app.get('/')
def home():
    return FileResponse(server.STATIC / 'index.html')


app.mount('/', server.app)
