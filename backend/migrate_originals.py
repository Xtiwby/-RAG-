"""Copy old originals to a private S3 bucket without deleting local copies.

Run with RAG_FILE_STORAGE=s3. Dry-run unless --apply is supplied.
"""
import argparse
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent/'.env')
from app import store as s, object_storage


def migrate(table, kind, apply):
    count=0
    for row in s.rows(f"SELECT * FROM {table} WHERE path<>'' AND storage_backend='local'"):
        path=Path(row['path'])
        if not path.is_file():
            raise RuntimeError(f'{table} #{row["id"]} 原文件缺失')
        digest=object_storage.file_sha256(path)
        if digest!=row['sha256']:
            raise RuntimeError(f'{table} #{row["id"]} 原文件 SHA-256 不符')
        count+=1
        if not apply:
            continue
        suffix=path.suffix.lstrip('.') or 'bin'
        key=object_storage.object_key(kind,row['id'],row.get('version','import'),suffix)
        stored=object_storage.save_file(path,key,digest)
        try:
            with s.connect() as c:
                changed=c.execute(f'''UPDATE {table} SET storage_backend=?,storage_bucket=?,object_key=?,object_version_id=?,path=''
                    WHERE id=? AND storage_backend='local' AND path=?''',
                    (stored['storage_backend'],stored['storage_bucket'],stored['object_key'],stored['object_version_id'],row['id'],row['path'])).rowcount
                if changed!=1:
                    raise RuntimeError(f'{table} #{row["id"]} 已被其他进程修改，请停机后重试')
                c.execute('''INSERT INTO audit_logs(actor,action,resource,detail,created_at,user_id,username,result,object_id,status_code,source)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                    ('存储迁移任务','迁移原文件','文档' if table=='documents' else '报告模板','',s.now(),None,'storage-migration','success',str(row['id']),None,'migration'))
        except Exception:
            try:object_storage.remove_uncommitted(stored['object_key'],stored['object_version_id'])
            except Exception:pass
            raise
    return count


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if object_storage.backend()!='s3':
        raise SystemExit('请先配置 RAG_FILE_STORAGE=s3 和私有存储桶')
    docs=migrate('documents','documents',args.apply)
    templates=migrate('template_imports','templates',args.apply)
    print(f'{"已迁移" if args.apply else "待迁移"}: 文档 {docs}，模板 {templates}；旧文件保留')
