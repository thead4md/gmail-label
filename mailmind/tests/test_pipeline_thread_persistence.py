from mailmind.storage.database import Database
from mailmind.processing.pipeline import Pipeline
from mailmind.processing.scorer import PriorityScorer
from mailmind.processing.rules import RulesEngine
from mailmind.storage.models import Email
import tempfile
from pathlib import Path


def test_pipeline_persists_thread_context():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir)/'test.db')
        rules = RulesEngine()
        scorer = PriorityScorer()
        pipeline = Pipeline(db, rules, scorer, executor=None)
        e = Email(gmail_id='t1', sender='sam@example.com', subject='Re: meeting', snippet='', body_text='Can you confirm?')
        db.insert_email(e)
        pred = pipeline.process(e)
        # reload prediction and check thread_context_json present
        rows = db.execute_sql("SELECT * FROM predictions WHERE email_gmail_id = ?", (e.gmail_id,)).fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row['thread_context_json'] is not None

