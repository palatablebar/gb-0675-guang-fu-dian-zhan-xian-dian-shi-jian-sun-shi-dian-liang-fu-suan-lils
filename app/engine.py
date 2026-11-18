from importlib import import_module
from pathlib import Path
from copy import deepcopy
import json, time, base64
from app.config import ROOT, MODULES, MAX_BYTES
from app.errors import BusinessError
from app.manifest import digest, make_manifest
from app import boundary as boundary_policy

def evaluate(payload=None, until=7, snapshot=None, boundary=None, replay=False):
    payload=payload or {}
    dataset=payload.get('dataset','dirty')
    if dataset not in {'dirty','clean','legacy'}: raise BusinessError('BAD_DATASET','样例名称无效')
    if not isinstance(payload.get('overrides',{}),dict): raise BusinessError('BAD_CONTEXT','参数须为对象')
    # csv_text 是文本通道（UTF-8 编码）；raw_b64 是原始字节通道，保留采集口原编码用于边界闸判定。
    if snapshot is not None:
        raw=base64.b64decode(snapshot['bytes_b64'],validate=True)
    elif 'raw_b64' in payload:
        raw=base64.b64decode(str(payload['raw_b64']),validate=True)
    elif 'csv_text' in payload:
        raw=str(payload['csv_text']).encode()
    else:
        raw=(ROOT/'sample_data'/f'{dataset}.csv').read_bytes()
    if len(raw)>MAX_BYTES: raise BusinessError('TOO_LARGE','文件超过2MB',413)
    # 切换边界闸：快照重放走只读豁免，新文件必须从登记来源进入专属泳道。
    rule=boundary or boundary_policy.default_boundary()
    is_replay=bool(replay or snapshot is not None)
    source_id, encoding = boundary_policy.admit(raw, dataset, rule, replay=is_replay)
    context=deepcopy(snapshot['context']) if snapshot is not None else json.loads((ROOT/'sample_data/context.json').read_text())
    context.update(deepcopy(payload.get('overrides',{})))
    start=time.perf_counter()
    state={'raw_bytes':raw,'context':context,'records':[],'quarantine':[], 'counts':{},'views':{},'source':dataset,
           'admitted_encoding':encoding,
           'boundary_decision':{'source_id':source_id,'lane':boundary_policy.SOURCES[source_id]['lane'],
                                 'encoding':encoding,'phase':rule['phase'],'replay':is_replay}}
    for index,key in enumerate(MODULES,1):
        if index>until: break
        import_module(f'app.modules.{key}.rules').apply(state)
    state['input_snapshot']={'bytes_b64':base64.b64encode(raw).decode(),'context':deepcopy(context)}
    state.pop('raw_bytes',None)
    state['manifest']=make_manifest(digest(raw,context),dataset,time.perf_counter()-start)
    return state
