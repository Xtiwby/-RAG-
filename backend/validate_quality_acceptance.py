"""Integrity and structure only, explicitly not model quality grading."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'test-data/quality-v010-synthetic'

def validate():
    manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
    labels=ROOT/manifest['labels_path']
    assert hashlib.sha256(labels.read_bytes()).hexdigest()==manifest['labels_sha256']
    cases=[json.loads(s) for s in labels.read_text(encoding='utf-8').splitlines()]
    assert len(cases)==100 and len({c['id'] for c in cases})==100
    assert sum(c['answerable'] for c in cases)==80
    assert sum(not c['answerable'] for c in cases)==20
    ids={entry['source_id'] for entry in manifest['corpus']}
    for entry in manifest['corpus']:
        path=ROOT/entry['path']
        assert path.resolve().is_relative_to((ROOT/'corpus').resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
        assert '虚构' in path.read_text(encoding='utf-8') or 'fictional' in path.read_text(encoding='utf-8')
    assert all(set(c['source_ids'])<=ids for c in cases)
    return {'cases':len(cases),'source_groups':len(ids),'answerable':80,'unanswerable':20,
        'integrity':'passed','labels_uploaded':False,'real_model_evaluated_cases':0,
        'note':'仅完成文件完整性/结构检查，不是100题模型质量通过'}

if __name__=='__main__':print(json.dumps(validate(),ensure_ascii=False,indent=2))
