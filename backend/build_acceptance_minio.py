"""Build pinned community MinIO from official source (binaries were retired)."""
import os
import subprocess
import tarfile
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent/'.integration-tools'
TAG='RELEASE.2025-10-15T17-29-55Z'


def main():
    target=ROOT/'minio-source'
    if not target.exists():
        archive=ROOT/'minio-source.tar.gz'
        with requests.get(f'https://codeload.github.com/minio/minio/tar.gz/refs/tags/{TAG}',stream=True,timeout=120) as response:
            response.raise_for_status()
            with archive.open('wb') as stream:
                for block in response.iter_content(1024*1024):stream.write(block)
        with tarfile.open(archive) as source:
            members=source.getmembers();prefix=members[0].name.split('/')[0]
            for member in members:
                resolved=(ROOT/member.name).resolve()
                if not resolved.is_relative_to(ROOT.resolve()) or member.issym() or member.islnk():
                    raise ValueError('Unexpected source archive member')
            source.extractall(ROOT)
        (ROOT/prefix).rename(target)
    candidates=list((ROOT/'runtime').glob('bin/go.exe'))
    if not candidates:
        candidates=list((ROOT/'mamba/pkgs').glob('**/go-*/bin/go.exe'))
    if not candidates:raise RuntimeError('Run setup_integration_runtime.py first')
    go=candidates[0]
    goroot=go.parents[1]/'go' if (go.parents[1]/'go/src').exists() else go.parents[1]
    env={**os.environ,'GOROOT':str(goroot),'GOPATH':str(ROOT/'go-cache'),
         'GOCACHE':str(ROOT/'go-build'),'GOPROXY':'https://goproxy.cn,https://proxy.golang.org,direct',
         'GOSUMDB':'sum.golang.org https://goproxy.cn/sumdb/sum.golang.org','CGO_ENABLED':'0'}
    subprocess.run([str(go),'build','-o',str(ROOT/'minio.exe'),'.'],cwd=target,env=env,check=True)
    subprocess.run([str(ROOT/'minio.exe'),'--version'],check=True)


if __name__=='__main__':main()
