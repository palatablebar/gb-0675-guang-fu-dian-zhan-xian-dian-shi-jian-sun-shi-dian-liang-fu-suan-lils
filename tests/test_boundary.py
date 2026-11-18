"""来源切换边界治理测试：泳道隔离、只读重放、退出取证、切换回滚、规则漂移。"""
import base64
import pytest
from fastapi.testclient import TestClient
from app import main, store, governance
from app.engine import evaluate
from app.boundary import default_boundary, rule_fingerprint, SOURCES
from app.config import ROOT
from app.errors import BusinessError

LEGACY_BYTES = (ROOT / 'sample_data/legacy.csv').read_bytes()
CLEAN_TEXT = (ROOT / 'sample_data/clean.csv').read_text()


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.sqlite'))
    return TestClient(main.app)


def test_legacy_bytes_only_enter_legacy_lane():
    # 旧编码字节从新采集口（v2 数据集）进入必须阻断，新旧来源各自只能进入对应批次。
    boundary = default_boundary()
    with pytest.raises(BusinessError) as e:
        evaluate({'dataset': 'clean', 'raw_b64': base64.b64encode(LEGACY_BYTES).decode()}, boundary=boundary)
    assert e.value.code == 'ENCODING_MISMATCH'
    # 同一字节从旧采集口进入则正常受理。
    state = evaluate({'dataset': 'legacy'}, boundary=boundary)
    assert state['boundary_decision']['lane'] == 'legacy'
    assert state['boundary_decision']['encoding'] == 'gb18030'


def test_utf8_bytes_blocked_from_legacy_lane():
    # 新格式字节不得污染旧编码批。
    boundary = default_boundary()
    with pytest.raises(BusinessError) as e:
        evaluate({'dataset': 'legacy', 'raw_b64': base64.b64encode(CLEAN_TEXT.encode()).decode()}, boundary=boundary)
    assert e.value.code == 'ENCODING_MISMATCH'


def test_cutover_closes_legacy_intake_but_snapshot_replays(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    # 双轨期先建立一个历史旧批并完成复核收口。
    run = c.post('/api/runs', json={'dataset': 'legacy'}).json()
    rid = run['manifest']['run_id']
    c.post('/api/runs/' + rid + '/review', json={'revision': 1})
    reason = '旧采集口下周停用'
    ok = c.post('/api/boundary/switch', json={'phase': 'cutover', 'reason': reason})
    assert ok.status_code == 200 and ok.json()['switched'] is True
    # cutover 后旧入口对新文件关闭（423）。
    blocked = c.post('/api/runs', json={'dataset': 'legacy'})
    assert blocked.status_code == 423 and blocked.json()['code'] == 'LEGACY_INTAKE_CLOSED'
    # 新批不受影响，且新格式不能借旧口进入。
    assert c.post('/api/runs', json={'dataset': 'clean'}).status_code == 200
    # 历史旧批凭不可变快照只读重放，批号与结果不变，响应带只读重放标记。
    replay = c.post('/api/runs/' + rid + '/rerun')
    assert replay.status_code == 200
    body = replay.json()
    assert body['manifest']['run_id'] == rid
    assert body['boundary_decision']['replay'] is True
    assert body['views'] == run['views'] and body['balance'] == run['balance']


def test_switch_blocked_keeps_previous_boundary(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    # 存在待复核旧批，退出取证不通过：切换必须被阻断且边界保持 dual。
    c.post('/api/runs', json={'dataset': 'legacy'})
    res = c.post('/api/boundary/switch', json={'phase': 'cutover', 'reason': 'x'})
    assert res.status_code == 423
    codes = {b['code'] for b in res.json()['blockers']}
    assert 'OPEN_REVIEWS' in codes
    assert c.get('/api/boundary').json()['phase'] == 'dual'
    # 阻断期间旧入口仍可收口（旧版边界未被破坏）。
    assert c.post('/api/runs', json={'dataset': 'legacy'}).status_code == 200


def test_source_status_page_lists_exit_or_block(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    page = c.get('/boundary')
    assert page.status_code == 200 and '来源切换边界裁定' in page.text
    data = c.get('/api/boundary').json()
    ids = {s['source_id'] for s in data['sources']}
    assert ids == set(SOURCES)
    legacy = next(s for s in data['sources'] if s['source_id'] == 'legacy-intake')
    # 无历史旧批时待复核为 0，但自测与漂移通过后应为可退出。
    assert legacy['exitable'] is True and legacy['status'] == '可退出'
    v2 = next(s for s in data['sources'] if s['source_id'] == 'v2-intake')
    assert v2['status'] == '现役来源'


def test_rule_fingerprint_change_forces_rejudge(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'legacy'}).json()['manifest']['run_id']
    c.post('/api/runs/' + rid + '/review', json={'revision': 1})
    assert c.post('/api/boundary/switch', json={'phase': 'cutover', 'reason': '停'}).status_code == 200
    # 模拟来源规则变化：指纹漂移后旧口退出结论失效，页面出现阻断原因。
    monkeypatch.setattr('app.boundary.rule_fingerprint', lambda: 'drifted-fingerprint')
    data = c.get('/api/boundary').json()
    assert data['fingerprint_changed'] is True
    legacy = next(s for s in data['sources'] if s['source_id'] == 'legacy-intake')
    assert any(b['code'] == 'RULE_FINGERPRINT_CHANGED' for b in legacy['blockers'])
    # 规则漂移期间尝试再做切换必须被 RULE_FINGERPRINT_CHANGED 阻断，边界保持不变。
    res = c.post('/api/boundary/switch', json={'phase': 'dual', 'reason': '规则变了重判'})
    assert res.status_code == 423
    assert any(b['code'] == 'RULE_FINGERPRINT_CHANGED' for b in res.json()['blockers'])


def test_replay_drift_blocks_exit(tmp_path, monkeypatch):
    db = str(tmp_path / 'd.sqlite')
    monkeypatch.setattr(main, 'DB_PATH', db)
    run = store.save(db, evaluate({'dataset': 'legacy'}, boundary=default_boundary()))
    # 篡改已封存批次的视图，制造重放漂移证据。
    with store.connect(db) as con:
        import json
        row = con.execute('SELECT result FROM runs WHERE id=?', (run['manifest']['run_id'],)).fetchone()
        broken = json.loads(row['result'])
        broken['views']['loss']['metrics']['已确定损失_kWh'] = '999.0000'
        con.execute('UPDATE runs SET result=? WHERE id=?', (json.dumps(broken, ensure_ascii=False), run['manifest']['run_id']))
    overview = governance.overview(db)
    assert overview['evidence']['replay_drift'] == 1
    legacy = next(s for s in overview['sources'] if s['source_id'] == 'legacy-intake')
    assert any(b['code'] == 'REPLAY_DRIFT' for b in legacy['blockers'])


def test_rollback_restores_previous_boundary(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'legacy'}).json()['manifest']['run_id']
    c.post('/api/runs/' + rid + '/review', json={'revision': 1})
    c.post('/api/boundary/switch', json={'phase': 'cutover', 'reason': '停用'})
    back = c.post('/api/boundary/rollback', json={'reason': '切换失败恢复旧版边界'})
    assert back.status_code == 200 and back.json()['boundary']['phase'] == 'dual'
    assert c.get('/api/boundary').json()['phase'] == 'dual'
    # 回滚后旧入口恢复收口能力。
    assert c.post('/api/runs', json={'dataset': 'legacy'}).status_code == 200


def test_archive_keeps_original_bytes(tmp_path, monkeypatch):
    import io, zipfile
    c = client(tmp_path, monkeypatch)
    run = c.post('/api/runs', json={'dataset': 'legacy'}).json()
    z = zipfile.ZipFile(io.BytesIO(c.get('/api/export/' + run['manifest']['run_id']).content))
    # 旧原件字节级保留，且封存了来源裁决。
    assert z.read('source_original.bin') == LEGACY_BYTES
    decision = __import__('json').loads(z.read('boundary.json'))
    assert decision['lane'] == 'legacy'


def test_rerun_drift_rejected(tmp_path, monkeypatch):
    import json
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'legacy'}).json()['manifest']['run_id']
    with store.connect(main.DB_PATH) as con:
        row = con.execute('SELECT result FROM runs WHERE id=?', (rid,)).fetchone()
        broken = json.loads(row['result'])
        broken['views']['loss']['metrics']['已确定损失_kWh'] = '999.0000'
        con.execute('UPDATE runs SET result=? WHERE id=?', (json.dumps(broken, ensure_ascii=False), rid))
    res = c.post('/api/runs/' + rid + '/rerun')
    assert res.status_code == 422 and res.json()['code'] == 'REPLAY_DRIFT'
