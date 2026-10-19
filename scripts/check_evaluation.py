"""Explicit live smoke test: one real QA, judge, Mongo record, Langfuse readback.

Uses current source through the real API router without restarting PyCharm or
running unrelated background jobs. Creates a clearly labeled diagnostic thread.
"""
import hashlib
import json
import os
import sys
import time
import httpx
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent.api.dashboard_routes import dashboard_router
from agent.core import evaluation as ev
from agent.core.observability import get_langfuse_client, shutdown_tracing
from agent.env_utils import get_env


def main():
    app = FastAPI()
    app.include_router(dashboard_router)
    answer, thread_id, event = None, None, ''
    live = os.environ.get('EVALUATION_TEST_HTTP') == '1'
    transport = httpx.Client(base_url='http://127.0.0.1:12024', timeout=240, trust_env=False) if live else TestClient(app)
    with transport as http:
        with http.stream('POST', '/dashboard/api/threads/stream-message', json={
            'repo': get_env('DEFAULT_REPO_URL') or None,
            'content': '评测链路验证，只做问答：Python 字典是什么？请用一句话回答，不调用工具、不修改文件。',
        }) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith('event:'):
                    event = line[6:].strip()
                elif line.startswith('data:'):
                    data = json.loads(line[5:])
                    thread_id = thread_id or data.get('thread_id')
                    if event == 'answer_feedback':
                        answer = data
                    if event == 'error':
                        raise RuntimeError(data.get('message'))
        assert answer, 'No completed answer'
        qid = answer['question_id']
        for _ in range(20):
            doc = ev.collection().find_one({'question_id': qid})
            if doc:
                break
            time.sleep(.25)
        assert doc, 'Evaluation job missing'
        assert doc['trace_id'] == answer['trace_id']
        assert doc['question_id'] == qid and doc['thread_id'] == thread_id
        assert doc['checks']['execution_success']
        ev.collection().update_one({'_id': doc['_id']}, {'$set': {
            'state': 'pending', 'next_attempt_at': datetime.now(UTC), 'diagnostic': True}})
        print('QA API -> evaluation job -> MongoDB: PASS', flush=True)
        if live:
            for attempt in range(90):
                current = ev.collection().find_one({'_id': doc['_id']})
                if current['state'] in {'completed', 'failed'}:
                    break
                time.sleep(2)
        else:
            ev.process_one(qid)
        doc = ev.collection().find_one({'_id': doc['_id']})
        assert doc['state'] == 'completed', (doc['state'], doc.get('last_error_type'))
        assert doc['result']['prompt_version'] == int(get_env('EVAL_PROMPT_VERSION'))
        assert doc['result']['quality_total'] is not None
        ev.export_pending()
        doc = ev.collection().find_one({'_id': doc['_id']})
        assert doc['export_state'] == 'synced'
        result = http.get(f'/dashboard/api/threads/{thread_id}/questions/{qid}/evaluation')
        assert result.status_code == 200
        assert 'evidence' not in result.json() and 'question' not in result.json()
        assert http.get(f'/dashboard/api/threads/wrong-thread/questions/{qid}/evaluation').status_code == 404
        client = get_langfuse_client()
        score_id = hashlib.sha256(f'{doc["_id"]}:answer_quality'.encode()).hexdigest()
        for attempt in range(15):
            scores = client.api.scores_v3.get_many_v3(id=score_id, fields='core,subject,details').data
            if scores:
                break
            time.sleep(2)
        assert len(scores) == 1
        score = scores[0]
        assert score.subject.id == doc['trace_id']
        assert score.environment == get_env('LANGFUSE_TRACING_ENVIRONMENT', 'development')
        assert score.value == doc['result']['quality_total']
        assert score.metadata['question_id'] == qid
        # Repeat delivery proves stable IDs do not create an additional score.
        ev.collection().update_one({'_id': doc['_id']}, {'$set': {'export_state': 'pending'}})
        ev.export_pending()
        assert len(client.api.scores_v3.get_many_v3(id=score_id, fields='core,subject,details').data) == 1
        print(json.dumps({'question_id': qid, 'thread_id': thread_id, 'trace_id': doc['trace_id'],
                          'quality_total': score.value, 'prompt_version': doc['result']['prompt_version'],
                          'mongo': 'PASS', 'langfuse_readback': 'PASS', 'idempotent_export': 'PASS'}, ensure_ascii=False), flush=True)
    shutdown_tracing()


if __name__ == '__main__':
    main()
