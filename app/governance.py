"""来源边界治理的装配层：把存储、引擎自测与纯策略 boundary 接起来。"""
from datetime import datetime, timezone
from copy import deepcopy
from app import boundary as policy
from app import store
from app.engine import evaluate
from app.errors import BusinessError

SELF_CHECK_CASES = {
    # 来源 -> (样例数据集, 期望编码)
    'legacy-intake': ('legacy', 'gb18030'),
    'v2-intake': ('clean', 'utf-8'),
}


def self_check(source_id, boundary):
    """来源自测：本来源样例能按声明编码进入对应泳道，且计算能完整跑通。"""
    if source_id not in SELF_CHECK_CASES:
        return False
    dataset, expected_encoding = SELF_CHECK_CASES[source_id]
    probe_boundary = deepcopy(boundary)
    # 自测验证“来源规则本身”是否成立，不受当前 phase 开闭影响。
    probe_boundary['phase'] = 'dual'
    try:
        state = evaluate({'dataset': dataset}, boundary=probe_boundary)
    except BusinessError:
        return False
    decision = state.get('boundary_decision', {})
    return decision.get('source_id') == source_id and state['encoding'] == expected_encoding


def replay_legacy(path, boundary):
    """对全部历史旧批做只读重放，返回 (批次总数, 漂移数, 漂移明细)。"""
    legacy_runs = store.runs_by_lane(path, 'legacy')
    drift = []
    for item in legacy_runs:
        result = item['result']
        snapshot = result.get('input_snapshot')
        if snapshot is None:
            drift.append({'run_id': item['run_id'], 'reason': 'INPUT_SNAPSHOT_MISSING'})
            continue
        replayed = evaluate({'dataset': result['source']}, snapshot=snapshot, boundary=boundary, replay=True)
        # 旧批只读重放：批号（同输入+规则版本）与全部业务视图必须逐项一致。
        if replayed['manifest']['run_id'] != result['manifest']['run_id']:
            drift.append({'run_id': item['run_id'], 'reason': 'RUN_ID_CHANGED'})
        elif replayed['views'] != result.get('views') or replayed['balance'] != result.get('balance'):
            drift.append({'run_id': item['run_id'], 'reason': 'RESULT_CHANGED'})
    return len(legacy_runs), len(drift), drift


def collect_evidence(path, boundary):
    open_reviews = sum(1 for r in store.runs_by_lane(path, 'legacy') if r['status'] != '已复核')
    total_legacy, replay_drift, drift_detail = replay_legacy(path, boundary)
    return {
        'open_reviews': open_reviews,
        'replay_drift': replay_drift,
        'drift_detail': drift_detail,
        'legacy_total': total_legacy,
        'legacy_self_check': self_check('legacy-intake', boundary),
        'v2_self_check': self_check('v2-intake', boundary),
        'fingerprint_current': policy.rule_fingerprint(),
        'fingerprint_boundary': boundary['fingerprint'],
    }


def source_statuses(path, boundary):
    evidence = collect_evidence(path, boundary)
    statuses = []
    for source_id in policy.SOURCES:
        item = policy.evaluate_source(
            source_id, boundary,
            {'open_reviews': evidence['open_reviews'],
             'replay_drift': evidence['replay_drift'],
             'self_check': evidence['legacy_self_check'] if source_id == 'legacy-intake' else evidence['v2_self_check']},
        )
        statuses.append(item)
    return statuses, evidence


def overview(path):
    boundary = store.get_boundary(path)
    statuses, evidence = source_statuses(path, boundary)
    return {
        'boundary': boundary,
        'phase': boundary['phase'],
        'fingerprint_changed': boundary['fingerprint'] != policy.rule_fingerprint(),
        'sources': statuses,
        'evidence': evidence,
        'block_reasons': policy.BLOCK_REASONS,
        'history': store.boundary_history(path),
    }


def switch_phase(path, target_phase, reason):
    """切换边界：先全量探测，任一不过则保持旧版边界并返回阻断；通过才提交新版本。"""
    boundary = store.get_boundary(path)
    evidence = collect_evidence(path, boundary)
    probes = {
        'open_reviews': evidence['open_reviews'],
        'replay_drift': evidence['replay_drift'],
        'legacy_self_check': evidence['legacy_self_check'],
    }
    blockers = policy.validate_switch(target_phase, boundary, probes)
    if blockers:
        return {'switched': False, 'phase': boundary['phase'],
                'blockers': [{'code': c, 'detail': policy.BLOCK_REASONS.get(c, c)} for c in blockers],
                'evidence': evidence}
    next_boundary = deepcopy(boundary)
    next_boundary['phase'] = target_phase
    next_boundary['fingerprint'] = policy.rule_fingerprint()
    next_boundary['version'] = boundary['version'] + 1
    next_boundary['updated_at'] = datetime.now(timezone.utc).isoformat()
    next_boundary['last_switch'] = {'from': boundary['phase'], 'to': target_phase, 'reason': reason,
                                    'at': next_boundary['updated_at']}
    store.save_boundary(path, next_boundary, f"切换 {boundary['phase']} -> {target_phase}：{reason}")
    return {'switched': True, 'phase': target_phase, 'blockers': [], 'boundary': next_boundary}


def rollback(path, reason):
    return store.rollback_boundary(path, reason)
