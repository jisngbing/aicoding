"""Repair only this application's generated scores, with a local backup first.

Langfuse 4.5 keeps an existing score's environment on ordinary upsert. This
explicit migration removes a misplaced API score and recreates the same ID,
subject, value and details in its trace's environment. It never invokes a judge.
"""
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.core import evaluation as ev
from agent.core.answer_feedback import collection as feedback_collection
from agent.core.observability import get_langfuse_client, shutdown_tracing


def main():
    client = get_langfuse_client()
    candidates = {}
    for doc in ev.collection().find({'trace_id': {'$ne': None}}):
        for name in ev.score_values(doc):
            score_id = hashlib.sha256(f'{doc["_id"]}:{name}'.encode()).hexdigest()
            candidates[score_id] = doc['trace_id']
    for doc in feedback_collection().find({'rating': {'$in': ['helpful', 'not_helpful']}, 'trace_id': {'$ne': None}}):
        candidates['feedback-' + doc['_id']] = doc['trace_id']
    repairs = []
    environments = {}
    for score_id, trace_id in candidates.items():
        rows = client.api.scores_v3.get_many_v3(id=score_id, fields='core,subject,details').data
        if not rows:
            continue
        score = rows[0]
        if trace_id not in environments:
            observations = client.api.observations.get_many(trace_id=trace_id, limit=1).data
            if not observations:
                continue
            environments[trace_id] = observations[0].environment
        target = environments[trace_id]
        if score.environment == target:
            continue
        assert score.subject.kind == 'trace' and score.subject.id == trace_id
        assert score.source == 'API'
        repairs.append({'score': score.model_dump(mode='json'), 'target_environment': target})
    directory = ROOT / 'artifacts' / 'evaluation'
    directory.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 1:
        backup = Path(sys.argv[1]).resolve()
        repairs = json.loads(backup.read_text(encoding='utf-8'))
    else:
        backup = directory / ('score-environment-backup-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '.json')
        backup.write_text(json.dumps(repairs, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Backed up {len(repairs)} misplaced scores before repair', flush=True)
    for repair in repairs:
        score = repair['score']
        # Fern SDK models serialize aliases by default, even in model_dump().
        score['data_type'] = score.get('data_type', score.get('dataType'))
        score['config_id'] = score.get('config_id', score.get('configId'))
    progress_path = backup.with_suffix('.progress.json')
    progress = json.loads(progress_path.read_text(encoding='utf-8')) if progress_path.exists() else {'queued': [], 'completed': []}
    # Enqueue the full batch before waiting: the server processes deletions asynchronously.
    for repair in repairs:
        score_id = repair['score']['id']
        if score_id in progress['queued'] or score_id in progress['completed']:
            continue
        rows = client.api.scores_v3.get_many_v3(id=score_id).data
        if rows and rows[0].environment == repair['target_environment']:
            progress['completed'].append(score_id)
        elif rows:
            client.api.legacy.score_v1.delete(score_id, request_options={'max_retries': 0, 'timeout_in_seconds': 10})
            progress['queued'].append(score_id)
        progress_path.write_text(json.dumps(progress), encoding='utf-8')
    print('Deletion batch queued; waiting for the server worker.', flush=True)
    for repair in repairs:
        score, target = repair['score'], repair['target_environment']
        if score['id'] in progress['completed']:
            continue
        for attempt in range(150):
            if not client.api.scores_v3.get_many_v3(id=score['id']).data:
                break
            time.sleep(2)
        else:
            raise RuntimeError('Deletion still pending; backup retained: ' + score['id'])
        arguments = {key: score[key] for key in ('id', 'name', 'data_type', 'comment', 'metadata')}
        arguments['value'] = int(score['value']) if score['data_type'] == 'BOOLEAN' else score['value']
        arguments['trace_id'] = score['subject']['id']
        arguments['environment'] = target
        if score.get('config_id'):
            arguments['config_id'] = score['config_id']
        client.api.scores.create(**arguments, request_options={'max_retries': 0, 'timeout_in_seconds': 10})
        for attempt in range(20):
            saved = client.api.scores_v3.get_many_v3(id=score['id'], fields='core,subject,details').data
            if saved and saved[0].environment == target:
                assert saved[0].value == score['value']
                assert saved[0].subject.id == score['subject']['id']
                break
            time.sleep(1)
        else:
            raise RuntimeError('Recreation verification pending; backup retained: ' + score['id'])
        progress['completed'].append(score['id'])
        progress_path.write_text(json.dumps(progress), encoding='utf-8')
        print(f'Repaired {score["name"]}: {score["id"]} -> {target}', flush=True)
    print('Score environment repair complete; values and IDs preserved.', flush=True)
    shutdown_tracing()


if __name__ == '__main__':
    main()
