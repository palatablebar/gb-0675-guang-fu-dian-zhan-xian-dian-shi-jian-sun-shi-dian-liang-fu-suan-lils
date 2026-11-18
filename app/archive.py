import io, csv, json, zipfile, hashlib, base64
from app.errors import BusinessError

def serialize(result):
    if not result.get('manifest') or not result.get('views'):
        raise BusinessError('INCOMPLETE_RUN','批次尚未完成')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps({**result['manifest'],'revision':result.get('revision',1),'status':result.get('status','待复核')},ensure_ascii=False,indent=2))
        archive.writestr('evidence.json',json.dumps({'counts':result['counts'],'views':result['views'],'quarantine':result['quarantine']},ensure_ascii=False,indent=2))
        # 旧原件原样封存：保留采集口原始字节，禁止“先转换再归档”导致复核失去依据。
        snapshot=result.get('input_snapshot')
        if snapshot:
            archive.writestr('source_original.bin',base64.b64decode(snapshot['bytes_b64']))
            archive.writestr('source_context.json',json.dumps(snapshot['context'],ensure_ascii=False,indent=2))
        if result.get('boundary_decision'):
            archive.writestr('boundary.json',json.dumps(result['boundary_decision'],ensure_ascii=False,indent=2))
    return buffer.getvalue()
