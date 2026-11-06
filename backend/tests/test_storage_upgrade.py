"""Storage cutover tests use an in-memory S3 fake; no cloud credentials."""
import io
import hashlib
from pathlib import Path
from test_api import client,admin,application_lifespan,kb,upload,finish_upload,configured
from app import object_storage,store


class Body(io.BytesIO):
    def iter_chunks(self,chunk_size):
        while chunk:=self.read(chunk_size):
            yield chunk


class FakeS3:
    def __init__(self):
        self.data={}

    def upload_file(self,path,bucket,key,ExtraArgs=None):
        self.data[(bucket,key)]=(Path(path).read_bytes(),ExtraArgs['Metadata'])

    def put_object(self,Bucket,Key,Body,Metadata):
        self.data[(Bucket,Key)]=(Body,Metadata)
        return {'VersionId':'v1'}

    def delete_object(self,Bucket,Key,VersionId=None):
        self.data.pop((Bucket,Key),None)

    def head_object(self,Bucket,Key,VersionId=None):
        data,metadata=self.data[(Bucket,Key)]
        return {'ContentLength':len(data),'Metadata':metadata,'VersionId':'v1'}

    def download_fileobj(self,bucket,key,stream,ExtraArgs=None):
        stream.write(self.data[(bucket,key)][0])

    def get_object(self,Bucket,Key,VersionId=None,Range=None):
        data=self.data[(Bucket,Key)][0]
        if Range:
            first,last=Range[6:].split('-')
            start=int(first) if first else 0
            end=int(last) if last else len(data)-1
            data=data[start:end+1]
            return {'Body':Body(data),'ContentLength':len(data),'ContentRange':f'bytes {start}-{end}/*'}
        return {'Body':Body(data),'ContentLength':len(data)}


def test_s3_upload_parses_and_serves_original(admin,configured,monkeypatch):
    fake=FakeS3()
    monkeypatch.setenv('RAG_FILE_STORAGE','s3')
    monkeypatch.setenv('RAG_S3_BUCKET','test-private')
    monkeypatch.setattr(object_storage,'client',lambda:fake)
    kid=kb(admin)
    content=b'# indexed original\n\nThe original PDF-like source remains verifiable.'
    response=client.post('/api/documents',headers=admin,data={'knowledge_base_id':kid},
                         files={'file':('source.md',content,'text/markdown')})
    assert response.status_code==201,response.text
    doc=finish_upload(admin,response.json())
    row=store.one('SELECT * FROM documents WHERE id=?',(doc['id'],))
    assert row['status']=='ready'
    assert row['storage_backend']=='s3' and row['storage_bucket']=='test-private'
    assert row['object_key'] and row['object_version_id']=='v1' and not row['path']
    assert row['sha256']==hashlib.sha256(content).hexdigest()
    original=client.get(f"/api/documents/{doc['id']}/original",headers=admin)
    assert original.status_code==200 and original.content==content
    partial=client.get(f"/api/documents/{doc['id']}/original",headers={**admin,'Range':'bytes=0-3'})
    assert partial.status_code==206 and partial.content==content[:4]


def test_local_original_remains_readable_after_s3_cutover(admin,configured,monkeypatch):
    monkeypatch.setenv('RAG_FILE_STORAGE','local')
    kid=kb(admin)
    doc=upload(admin,kid,name='legacy.md',text='Legacy local original and searchable content.')
    monkeypatch.setenv('RAG_FILE_STORAGE','s3')
    assert client.get(f"/api/documents/{doc['id']}/original",headers=admin).status_code==200


def test_s3_template_bytes_are_verified(monkeypatch):
    fake=FakeS3()
    monkeypatch.setenv('RAG_FILE_STORAGE','s3')
    monkeypatch.setenv('RAG_S3_BUCKET','test-private')
    monkeypatch.setattr(object_storage,'client',lambda:fake)
    data=b'template contents'
    sha=hashlib.sha256(data).hexdigest()
    stored=object_storage.save_bytes(data,'templates/1/v1/template.docx',sha)
    assert stored['object_version_id']=='v1'
    assert fake.data[('test-private','templates/1/v1/template.docx')]==(data,{'sha256':sha})
