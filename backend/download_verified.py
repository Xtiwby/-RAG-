"""HTTPS artifact download with resume and mandatory externally pinned SHA256."""
import argparse
import hashlib
import time
from pathlib import Path
import requests

def digest(path):
    with path.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()

def download(url,target,checksum):
    target=Path(target)
    if not url.startswith('https://') or len(checksum)!=64:
        raise ValueError('HTTPS and a pinned SHA256 are required')
    if target.exists():
        if digest(target)==checksum:return
        raise RuntimeError('已有目标文件哈希不同，未覆盖：'+str(target))
    target.parent.mkdir(parents=True,exist_ok=True)
    part=target.with_suffix(target.suffix+'.part')
    offset=part.stat().st_size if part.exists() else 0
    headers={'Accept-Encoding':'identity'}
    if offset:headers['Range']=f'bytes={offset}-'
    print('Download:',target.name,'resume bytes:',offset,flush=True)
    with requests.get(url,headers=headers,stream=True,timeout=(15,60)) as response:
        response.raise_for_status()
        append=offset and response.status_code==206
        if append and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
            raise RuntimeError('Invalid resume range')
        written=offset if append else 0
        tick=time.monotonic()
        with part.open('ab' if append else 'wb') as handle:
            for chunk in response.iter_content(1024*1024):
                handle.write(chunk);written+=len(chunk)
                if time.monotonic()-tick>10:
                    print(target.name,round(written/1024**2),'MiB',flush=True);tick=time.monotonic()
    if digest(part)!=checksum:
        raise RuntimeError('下载校验失败，未发布文件：'+str(target))
    part.replace(target)
    print('SHA256 verified:',target.name,flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('url');parser.add_argument('target');parser.add_argument('sha256')
    args=parser.parse_args();download(args.url,args.target,args.sha256)
