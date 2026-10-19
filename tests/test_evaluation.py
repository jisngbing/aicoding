from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agent.core import evaluation as ev


def judgement(value=4, **changes):
    data = {name: {"value": value, "reason": "可从本轮证据验证"} for name in ev.WEIGHTS}
    return ev.Judgement.model_validate({**data, "violations": [], **changes})


def test_weighted_score_and_missing_evidence():
    assert ev.quality(judgement())['quality_total'] == 100
    partial = ev.quality(judgement(correctness={"value": None, "reason": "缺少实时天气来源"}))
    assert partial['quality_total'] is None
    assert partial['coverage'] == .6
    assert partial['quality_label'] == 'needs_verification'
    assert ev.quality(judgement(violations=['未经许可发布评论']))['quality_label'] == 'needs_review'
    assert ev.quality(judgement(), False)['quality_label'] == 'needs_review'


@pytest.mark.parametrize('value', [-1, 5, 1.5, True, '4'])
def test_invalid_scores_rejected(value):
    with pytest.raises(ValidationError):
        judgement(value)


def test_rule_unknowns_and_recovered_errors():
    assert ev.execution_checks([], True)['tests_passed'] is None
    messages = [{'type': 'ai', 'tool_calls': [{'id': 't1', 'args': {'command': 'pytest tests'}},
                                            {'id': 't2', 'args': {'command': 'pytest tests; echo ok'}}]},
                {'type': 'tool', 'tool_call_id': 't1', 'content': 'exit_code=1\nfailed'},
                {'type': 'tool', 'tool_call_id': 't2', 'content': 'exit_code=0\nok'},
                {'type': 'tool', 'content': 'a search result'}]
    result = ev.execution_checks(messages, True)
    assert result['execution_success'] is True
    assert result['tool_success_ratio'] == .5
    assert result['tool_result_coverage'] == 2/3
    assert result['tests_passed'] is False


def test_sample_is_stable_and_bounded():
    assert not ev.sampled('q', 0)
    assert ev.sampled('q', 1)
    assert ev.sampled('q', .2) == ev.sampled('q', .2)
    assert 150 < sum(ev.sampled(str(i), .2) for i in range(1000)) < 250


def test_snapshot_excludes_previous_turn_and_marks_truncation(monkeypatch):
    monkeypatch.setattr(ev, 'clean', lambda x: x)
    result = ev.snapshot([{'type': 'human', 'content': 'old'}, {'type': 'ai', 'content': 'oldanswer'},
                          {'type': 'human', 'content': 'new'}, {'type': 'tool', 'content': 'x'*40000}])
    assert 'oldanswer' not in result['messages_json']
    assert result['truncated']


def test_enqueue_once_and_failure_never_breaks_answer(monkeypatch):
    mongo = Mock()
    monkeypatch.setattr(ev, 'enabled', lambda: True)
    monkeypatch.setattr(ev, 'get_env', lambda *args: '1')
    monkeypatch.setattr(ev, 'clean', lambda x: x)
    monkeypatch.setattr(ev, 'collection', lambda: mongo)
    ctx = {'question_id': 'q', 'thread_id': 't', 'question': 'question', 'evaluation_answer': 'answer'}
    ev.enqueue(ctx, success=True)
    query, update = mongo.update_one.call_args.args
    assert query == {'_id': 'q:1.0'}
    assert update['$setOnInsert']['state'] == 'pending'
    mongo.update_one.side_effect = RuntimeError('offline')
    ev.enqueue(ctx, success=True)


def test_old_turn_tool_failure_does_not_pollute_current_rules(monkeypatch):
    mongo = Mock()
    monkeypatch.setattr(ev, 'enabled', lambda: True)
    monkeypatch.setattr(ev, 'get_env', lambda *args: '1')
    monkeypatch.setattr(ev, 'clean', lambda x: x)
    monkeypatch.setattr(ev, 'collection', lambda: mongo)
    ev.enqueue({'question_id': 'q', 'thread_id': 't', 'evaluation_answer': 'answer',
                'evaluation_messages': [{'type': 'human', 'content': 'old'},
                                        {'type': 'tool', 'content': 'exit_code=1'},
                                        {'type': 'human', 'content': 'new'},
                                        {'type': 'ai', 'content': 'answer'}]}, success=True)
    assert mongo.update_one.call_args.args[1]['$setOnInsert']['checks']['tool_count'] == 0


def test_worker_persists_judgement_before_export(monkeypatch):
    mongo = Mock()
    mongo.find_one_and_update.return_value = {'_id': 'q:1.0', 'attempts': 1}
    monkeypatch.setattr(ev, 'collection', lambda: mongo)
    monkeypatch.setattr(ev, 'judge', lambda _: {'quality_total': 100})
    ev.process_one()
    update = mongo.update_one.call_args.args[1]['$set']
    assert update['state'] == 'completed'
    assert update['export_state'] == 'pending'
    assert update['result']['quality_total'] == 100


def test_judge_failure_retries_then_dead_letters(monkeypatch):
    mongo = Mock()
    monkeypatch.setattr(ev, 'collection', lambda: mongo)
    monkeypatch.setattr(ev, 'judge', Mock(side_effect=RuntimeError('not stored')))
    for attempt, state in [(1, 'pending'), (3, 'failed')]:
        mongo.find_one_and_update.return_value = {'_id': 'q', 'attempts': attempt}
        ev.process_one()
        saved = mongo.update_one.call_args.args[1]['$set']
        assert saved['state'] == state
        assert saved['last_error_type'] == 'RuntimeError'


def test_negative_feedback_promotes_sampled_out_job(monkeypatch):
    from agent.core import answer_feedback
    mongo, feedback = Mock(), Mock()
    feedback.find.return_value.sort.return_value.limit.return_value = [{'_id': 'q', 'thread_id': 't'}]
    mongo.update_one.return_value.matched_count = 1
    monkeypatch.setattr(ev, 'collection', lambda: mongo)
    monkeypatch.setattr(answer_feedback, 'collection', lambda: feedback)
    ev.promote_negative_feedback()
    assert mongo.update_one.call_args.args[1]['$set']['state'] == 'pending'
    assert feedback.update_one.call_args.args[1]['$set']['evaluation_promoted'] == ev.VERSION


def test_missing_old_job_does_not_starve_new_negative_feedback(monkeypatch):
    from agent.core import answer_feedback
    mongo, feedback = Mock(), Mock()
    feedback.find.return_value.sort.return_value.limit.return_value = [{'_id': 'old', 'thread_id': 't'}]
    mongo.update_one.return_value.matched_count = 0
    monkeypatch.setattr(ev, 'collection', lambda: mongo)
    monkeypatch.setattr(answer_feedback, 'collection', lambda: feedback)
    ev.promote_negative_feedback()
    saved = feedback.update_one.call_args.args[1]['$set']
    assert 'evaluation_checked_at' in saved
    assert 'evaluation_promoted' not in saved


def test_export_retry_idempotent_without_judge(monkeypatch):
    from agent.core import observability
    mongo, client = Mock(), Mock()
    doc = {'_id': 'q:1.0', 'question_id': 'q', 'thread_id': 't', 'trace_id': 'a'*32, 'state': 'completed',
           'checks': ev.execution_checks([], True)}
    mongo.find.return_value.limit.return_value = [doc]
    monkeypatch.setattr(ev, 'collection', lambda: mongo)
    monkeypatch.setattr(ev, 'score_config_ids', lambda: {})
    monkeypatch.setattr(ev, 'get_env', lambda name, default='': 'development')
    monkeypatch.setattr(observability, 'get_langfuse_client', lambda: client)
    judge = Mock()
    monkeypatch.setattr(ev, 'judge', judge)
    client.api.scores.create.side_effect = RuntimeError('offline')
    ev.export_pending()
    first_id = client.api.scores.create.call_args.kwargs['id']
    assert mongo.update_one.call_args.args[1]['$set'].get('next_export_at')
    client.api.scores.create.side_effect = None
    ev.export_pending()
    assert client.api.scores.create.call_args.kwargs['id'] == first_id
    assert client.api.scores.create.call_args.kwargs['environment'] == 'development'
    assert mongo.update_one.call_args.args[1]['$set']['export_state'] == 'synced'
    judge.assert_not_called()
