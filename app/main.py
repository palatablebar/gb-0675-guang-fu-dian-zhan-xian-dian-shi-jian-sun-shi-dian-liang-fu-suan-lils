from contextlib import asynccontextmanager
from importlib import import_module
from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from app.config import ROOT, META, MODULES
from app import config
from app.errors import BusinessError
from app.engine import evaluate
from app import store, governance
from app.archive import serialize

# 路由统一经模块属性取 DB_PATH，测试 monkeypatch app.main.DB_PATH 可整体重定向。
DB_PATH = config.DB_PATH

@asynccontextmanager
async def lifespan(app):
    # 初始双轨边界落库，作为后续切换失败时的回滚基线。
    store.save_boundary(DB_PATH, store.get_boundary(DB_PATH), '初始双轨边界')
    store.save(DB_PATH,evaluate(boundary=store.get_boundary(DB_PATH)))
    yield
app=FastAPI(title=META['title'],lifespan=lifespan)
app.mount('/static',StaticFiles(directory=str(ROOT/'app/static')),name='static')
@app.exception_handler(BusinessError)
async def business_error(request:Request,error:BusinessError):
    return JSONResponse({'code':error.code,'detail':error.message},status_code=error.status)
@app.get('/')
def home():
    return RedirectResponse('/'+MODULES[0],status_code=303)
@app.get('/healthz')
def health():
    return {'status':'ok','project':META['short']}
@app.post('/api/runs')
def create(payload:dict=Body(default={})):
    boundary=store.get_boundary(DB_PATH)
    return store.save(DB_PATH,evaluate(payload,boundary=boundary))
@app.post('/api/runs/{run_id}/rerun')
def rerun(run_id:str):
    previous=store.get(DB_PATH,run_id)
    if 'input_snapshot' not in previous:
        raise BusinessError('INPUT_SNAPSHOT_MISSING','旧批缺少源快照，请重新导入')
    # 历史批只读重放：携带不可变快照并豁免入口闸，规则未变时批号与结果不变。
    replayed=evaluate({'dataset':previous['source']},snapshot=previous['input_snapshot'],boundary=store.get_boundary(DB_PATH),replay=True)
    # 漂移校验覆盖批号、全部业务视图与对账四账，而非仅输入摘要。
    if (replayed['manifest']['sha256']!=previous['manifest']['sha256']
            or replayed['views']!=previous.get('views')
            or replayed['balance']!=previous.get('balance')):
        raise BusinessError('REPLAY_DRIFT','重放结果与封存批次不一致')
    store.save(DB_PATH,replayed)
    return replayed
@app.get('/api/runs')
def runs():
    return store.history(DB_PATH)
@app.get('/api/runs/{run_id}')
def run(run_id:str):
    return store.get(DB_PATH,run_id)
@app.post('/api/runs/{run_id}/review')
def review(run_id:str,payload:dict=Body(...)):
    revision=payload.get('revision')
    if not isinstance(revision,int) or revision<1: raise BusinessError('BAD_REVISION','版本号须为正整数')
    return store.review(DB_PATH,run_id,revision)
@app.get('/api/export/{run_id}')
def export(run_id:str):
    result=store.get(DB_PATH,run_id)
    return Response(serialize(result),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{run_id}.zip"'})
# 来源切换边界治理：页面展示逐来源可退出/阻断，切换失败保持旧版边界，支持回退。
@app.get('/api/boundary')
def boundary_overview():
    return governance.overview(DB_PATH)
@app.post('/api/boundary/switch')
def boundary_switch(payload:dict=Body(...)):
    target=payload.get('phase')
    reason=str(payload.get('reason') or '').strip() or '未注明切换原因'
    result=governance.switch_phase(DB_PATH,target,reason)
    return JSONResponse(result,status_code=200 if result['switched'] else 423)
@app.post('/api/boundary/rollback')
def boundary_rollback(payload:dict=Body(default={})):
    return {'rolled_back':True,'boundary':governance.rollback(DB_PATH,str(payload.get('reason') or '手动回退'))}
for key in MODULES:
    app.include_router(import_module(f'app.modules.{key}.handler').router)
app.include_router(import_module('app.modules.boundary_page.handler').router)
