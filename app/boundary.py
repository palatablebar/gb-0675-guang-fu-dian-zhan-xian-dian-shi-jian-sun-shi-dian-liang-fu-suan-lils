"""来源切换边界治理（纯策略层，不触碰数据库与业务规则）。

裁定要点：
- 每个来源（采集口）声明专属批次泳道与唯一编码；来源与泳道一一对应，互串即阻断。
- 边界 phase：dual（双轨并行，旧入口仍收口新批次）/ cutover（旧入口停用，仅历史快照可重放）。
- 旧批只能经不可变输入快照只读重放，重放不经过入口闸，批号与结果保持不变。
- 来源规则指纹一旦变化，退出结论立即失效并要求重新取证。
"""
import hashlib
import json

PHASES = ('dual', 'cutover')

# 来源注册表：source_id 与泳道、编码、样例、角色一一绑定。
SOURCES = {
    'legacy-intake': {
        'name': '旧采集口（GB18030 旧编码）',
        'lane': 'legacy',
        'encoding': 'gb18030',
        'datasets': ('legacy',),
        'role': 'legacy',
    },
    'v2-intake': {
        'name': '新采集口（UTF-8 新格式）',
        'lane': 'v2',
        'encoding': 'utf-8',
        'datasets': ('dirty', 'clean'),
        'role': 'current',
    },
}

# 原因码 -> 中文说明，页面直接展示。
BLOCK_REASONS = {
    'ENCODING_MISMATCH': '文件编码不属于该来源，禁止跨泳道进入',
    'LEGACY_INTAKE_CLOSED': '旧采集口已停用，新文件不再受理；历史批次请用只读重放',
    'RULE_FINGERPRINT_CHANGED': '来源规则已变化，原退出结论失效，须重新取证',
    'OPEN_REVIEWS': '仍存在待复核批次，未完成收口',
    'REPLAY_DRIFT': '历史批次重放结果与封存不一致',
    'SELF_CHECK_FAILED': '来源自测未通过',
    'UNKNOWN_SOURCE': '来源未登记',
}


def rule_fingerprint():
    """来源规则指纹：编码回退链与字段别名等采信规则变化即变化。

    故意只纳入“来源识别/采信”相关规则，业务计算规则版本仍由 manifest 的 RULE_VERSION 承载。
    """
    from app.modules.intake.reader import DECODE_FALLBACK
    from app.modules.intake.rules import FIELDS, HEADER_ALIASES
    material = json.dumps(
        {
            'decode_fallback': DECODE_FALLBACK,
            'fields': FIELDS,
            'aliases': HEADER_ALIASES,
            'sources': {k: {'lane': v['lane'], 'encoding': v['encoding']} for k, v in SOURCES.items()},
        },
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def default_boundary():
    """初始边界：双轨并行，旧采集口仍可收口；切换成功后才进入 cutover。"""
    return {
        'phase': 'dual',
        'fingerprint': rule_fingerprint(),
        'version': 1,
        'updated_at': None,
        'last_switch': None,
    }


def source_for_dataset(dataset):
    for source_id, meta in SOURCES.items():
        if dataset in meta['datasets']:
            return source_id
    return None


def detect_encoding(raw):
    """按来源注册表声明的编码顺序识别字节，返回编码名；无法识别返回 None。"""
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            raw.decode(encoding)
            return 'gb18030' if encoding == 'gb18030' else 'utf-8'
        except UnicodeDecodeError:
            continue
    return None


def admit(raw, dataset, boundary, *, replay=False):
    """入口闸：判定字节能否从某来源进入对应批次泳道。

    返回 (source_id, encoding)；拒绝时抛 BusinessError，原因码可直接展示。
    replay=True 表示历史批次经不可变快照只读重放，豁免入口开闭与跨口检查。
    """
    from app.errors import BusinessError
    if replay:
        encoding = detect_encoding(raw)
        if encoding is None:
            raise BusinessError('BAD_ENCODING', '文件编码无法识别')
        return source_for_dataset(dataset) or 'legacy-intake', encoding
    source_id = source_for_dataset(dataset)
    if source_id is None:
        raise BusinessError('UNKNOWN_SOURCE', BLOCK_REASONS['UNKNOWN_SOURCE'])
    meta = SOURCES[source_id]
    encoding = detect_encoding(raw)
    if encoding is None:
        raise BusinessError('BAD_ENCODING', '文件编码无法识别')
    # 新旧来源各自只能进入对应批次泳道：编码必须与来源声明一致。
    if encoding != meta['encoding']:
        raise BusinessError('ENCODING_MISMATCH', BLOCK_REASONS['ENCODING_MISMATCH'])
    # 旧采集口停用后不再受理任何新文件（快照重放走 replay 豁免分支）。
    if boundary['phase'] == 'cutover' and meta['role'] == 'legacy':
        raise BusinessError('LEGACY_INTAKE_CLOSED', BLOCK_REASONS['LEGACY_INTAKE_CLOSED'], 423)
    return source_id, encoding


def evaluate_source(source_id, boundary, evidence):
    """评估单个来源的可退出/阻断状态。

    evidence 由装配层提供：
      open_reviews: 待复核批次数
      replay_drift: 重放不一致的历史批次数
      self_check:   该来源自测是否通过
    旧采集口额外受 phase 与规则指纹约束；新采集口为现役来源，不评估退出。
    """
    meta = SOURCES[source_id]
    blockers = []
    if meta['role'] != 'legacy':
        return {
            'source_id': source_id,
            'name': meta['name'],
            'lane': meta['lane'],
            'encoding': meta['encoding'],
            'role': meta['role'],
            'exitable': False,
            'status': '现役来源',
            'blockers': [],
        }
    if boundary['fingerprint'] != rule_fingerprint():
        blockers.append('RULE_FINGERPRINT_CHANGED')
    if evidence.get('open_reviews', 0):
        blockers.append('OPEN_REVIEWS')
    if evidence.get('replay_drift', 0):
        blockers.append('REPLAY_DRIFT')
    if not evidence.get('self_check', False):
        blockers.append('SELF_CHECK_FAILED')
    exitable = not blockers
    if boundary['phase'] == 'cutover':
        status = '已停用收口' if exitable else '停用但未完成退出取证'
    else:
        status = '可退出' if exitable else '阻断'
    return {
        'source_id': source_id,
        'name': meta['name'],
        'lane': meta['lane'],
        'encoding': meta['encoding'],
        'role': meta['role'],
        'exitable': exitable,
        'status': status,
        'blockers': [{'code': code, 'detail': BLOCK_REASONS[code]} for code in blockers],
    }


def validate_switch(target_phase, boundary, probes):
    """切换前事务校验：全部探测通过才允许切换，否则返回阻断原因，边界保持旧版。"""
    if target_phase not in PHASES:
        return ['UNKNOWN_SOURCE']
    blockers = []
    # 来源规则一旦变化，任何切换动作都先阻断，要求重新取证。
    if boundary['fingerprint'] != rule_fingerprint():
        blockers.append('RULE_FINGERPRINT_CHANGED')
    if target_phase == boundary['phase']:
        return blockers
    if target_phase == 'cutover':
        # 停用旧入口前必须证明：无未决复核、历史重放零漂移、旧口自测通过。
        if probes.get('open_reviews', 0):
            blockers.append('OPEN_REVIEWS')
        if probes.get('replay_drift', 0):
            blockers.append('REPLAY_DRIFT')
        if not probes.get('legacy_self_check', False):
            blockers.append('SELF_CHECK_FAILED')
    return blockers
