"""Acceptance against the disposable localhost test server, never a live system."""
import hashlib
import json
import time
from pathlib import Path
import httpx

OUT=Path(__file__).resolve().parents[1]/'test-results/remaining-2026-11-19'


def main():
    with httpx.Client(base_url='http://127.0.0.1:8019',timeout=30) as client:
        login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'})
        login.raise_for_status();client.headers['Authorization']='Bearer '+login.json()['token']
        current=client.get('/api/settings').json()
        config=current['settings']
        config['ocr']['provider']='local'
        config['asr'].update(provider='local',model='Systran/faster-whisper-tiny',language='en')
        saved=client.put('/api/settings',json={'settings':config,'expected_revision':current['revision']})
        saved.raise_for_status()
        response=client.post('/api/knowledge-bases',json={'name':'Isolated multimodal acceptance '+str(time.time_ns())})
        response.raise_for_status();kid=response.json()['id']
        records=[]
        for name,terms in [('scanned.pdf',['NX90217','21','被动散热']),('speech.wav',['cooling','power']),('speech.mp4',['cooling','power'])]:
            raw=(OUT/'media'/name).read_bytes();start=time.perf_counter()
            response=client.post('/api/documents',data={'knowledge_base_id':kid},files={'file':(name,raw)})
            response.raise_for_status();did=response.json()['id']
            for _ in range(1800):
                doc=client.get(f'/api/documents/{did}').json()
                if doc['status'] not in ('uploading','processing'):break
                time.sleep(.1)
            assert doc['status']=='ready',doc
            text=' '.join(c['text'] for c in doc['chunks'])
            assert all(term in text for term in terms),text
            assert client.get(f'/api/documents/{did}/original').content==raw
            records.append({'file':name,'status':doc['status'],'seconds':time.perf_counter()-start,
                            'text':text,'sha256':hashlib.sha256(raw).hexdigest()})
            if name=='scanned.pdf':
                duplicate=client.post('/api/documents',data={'knowledge_base_id':kid},files={'file':(name,raw)})
                assert duplicate.status_code==409 and duplicate.json()['detail']['code']=='duplicate_document'
                reuse=client.post('/api/documents',data={'knowledge_base_id':kid,'duplicate_policy':'reuse'},files={'file':(name,raw)})
                assert reuse.status_code==201 and reuse.json()['id']==did and reuse.json()['reused']
        result={'pass':True,'pdf_ocr_worker':True,'audio_video_worker':True,'duplicate_choices':True,'results':records}
        (OUT/'multimodal-api.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Real OCR/audio/video upload and worker acceptance PASS',flush=True)


if __name__=='__main__':main()
