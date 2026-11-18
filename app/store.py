import sqlite3, json
from datetime import datetime, timezone
from pathlib import Path
from app.errors import BusinessError
from app import boundary as boundary_policy

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS boundary_history (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL, boundary TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)")
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
    return result

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新',409)
    return get(path,run_id)

def get_boundary(path):
    with connect(path) as con:
        row=con.execute('SELECT v FROM kv WHERE k=?',('boundary',)).fetchone()
    if row is None:
        return boundary_policy.default_boundary()
    return json.loads(row['v'])

def save_boundary(path, boundary, reason):
    """事务化写入边界，并把旧版登记进回滚历史；内容未变或切换失败时不产生噪声。"""
    payload=json.dumps(boundary,ensure_ascii=False)
    with connect(path) as con:
        previous=con.execute('SELECT v FROM kv WHERE k=?',('boundary',)).fetchone()
        if previous is not None and previous['v']==payload:
            return boundary
        if previous is not None:
            old=json.loads(previous['v'])
        else:
            old=boundary_policy.default_boundary()  # 首次切换：把初始双轨边界补登为回滚基线
        con.execute('INSERT INTO boundary_history(version,boundary,reason,created_at) VALUES(?,?,?,?)',
                    (old['version'],json.dumps(old,ensure_ascii=False),reason,datetime.now(timezone.utc).isoformat()))
        con.execute('INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',('boundary',payload))
    return boundary

def rollback_boundary(path, reason='手动回退'):
    with connect(path) as con:
        last=con.execute('SELECT * FROM boundary_history ORDER BY id DESC LIMIT 1').fetchone()
        if last is None:
            raise BusinessError('NO_ROLLBACK_TARGET','没有可回退的历史边界',409)
        current=con.execute('SELECT v FROM kv WHERE k=?',('boundary',)).fetchone()
        restored=json.loads(last['boundary'])
        con.execute('UPDATE kv SET v=? WHERE k=?',(json.dumps(restored,ensure_ascii=False),'boundary'))
        if current is not None:
            cur=json.loads(current['v'])
            con.execute('INSERT INTO boundary_history(version,boundary,reason,created_at) VALUES(?,?,?,?)',
                        (cur['version'],current['v'],reason,datetime.now(timezone.utc).isoformat()))
        con.execute('DELETE FROM boundary_history WHERE id=?',(last['id'],))
    return restored

def boundary_history(path):
    with connect(path) as con:
        rows=con.execute('SELECT version,reason,created_at,boundary FROM boundary_history ORDER BY id DESC LIMIT 20').fetchall()
    return [{'version':r['version'],'reason':r['reason'],'created_at':r['created_at'],'boundary':json.loads(r['boundary'])} for r in rows]

def runs_by_lane(path, lane):
    """按批次泳道返回历史批次（旧批无 lane 标记时按来源数据集回溯归类）。"""
    out=[]
    with connect(path) as con:
        rows=con.execute('SELECT id,status,result FROM runs ORDER BY rowid').fetchall()
    for r in rows:
        result=json.loads(r['result'])
        decision=result.get('boundary_decision') or {}
        run_lane=decision.get('lane') or ('legacy' if result.get('source') in boundary_policy.SOURCES['legacy-intake']['datasets'] else 'v2')
        if run_lane==lane:
            out.append({'run_id':r['id'],'status':r['status'],'result':result})
    return out
