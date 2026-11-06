"""Create the private bucket configured in backend/.env."""
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent/'.env')
from app import object_storage

if __name__=='__main__':
    if object_storage.backend()!='s3':
        raise SystemExit('先设置 RAG_FILE_STORAGE=s3')
    store=object_storage.client()
    name=object_storage.bucket()
    try:
        store.head_bucket(Bucket=name)
    except Exception as error:
        code=str(getattr(error,'response',{}).get('Error',{}).get('Code',''))
        if code not in {'404','NoSuchBucket','NotFound'}:
            raise
        store.create_bucket(Bucket=name)
    print('私有存储桶已就绪：'+name)
