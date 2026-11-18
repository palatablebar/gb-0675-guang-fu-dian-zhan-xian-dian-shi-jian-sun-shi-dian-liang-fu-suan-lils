from .reader import read_source
FIELDS=['id', 'kind', 'device', 'at', 'power', 'unit', 'irradiance', 'status', 'start', 'end']
# 表头别名属于来源规则，boundary.rule_fingerprint 会登记，变化即触发退出重判。
HEADER_ALIASES={'编号':'id'}
def apply(state):read_source(state,FIELDS,HEADER_ALIASES,state.get('admitted_encoding'))
