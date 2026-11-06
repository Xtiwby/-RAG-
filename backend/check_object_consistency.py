"""Report dangling and unreferenced S3 originals; never deletes objects."""
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent/'.env')
from app import store as s,object_storage

if __name__=='__main__':
    if object_storage.backend()!='s3':
        raise SystemExit('需要 RAG_FILE_STORAGE=s3')
    records=s.rows("SELECT id,storage_backend,storage_bucket,object_key,object_version_id FROM documents WHERE storage_backend='s3'")
    records+=s.rows("SELECT id,storage_backend,storage_bucket,object_key,object_version_id FROM template_imports WHERE storage_backend='s3'")
    missing=[]
    for row in records:
        if not object_storage.exists(row):
            missing.append((row['storage_bucket'],row['object_key'],row['id']))
    print(f'数据库引用对象 {len(records)}，缺失 {len(missing)}')
    for item in missing:
        print('缺失：',item)
    known={(row['storage_bucket'],row['object_key']) for row in records}
    store=object_storage.client()
    orphans=[]
    for page in store.get_paginator('list_objects_v2').paginate(Bucket=object_storage.bucket()):
        for item in page.get('Contents',[]):
            if (object_storage.bucket(),item['Key']) not in known:
                orphans.append(item['Key'])
    print(f'未被数据库引用的当前对象 {len(orphans)}')
    for key in orphans[:100]:
        print('未引用：',key)
    if missing:
        raise SystemExit('存在缺失对象，不能切换原件存储')
