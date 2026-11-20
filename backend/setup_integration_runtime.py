"""Install a workspace-local PostgreSQL/pgvector/Go acceptance runtime on Windows.

No system services, shell profiles, or existing databases are changed.
Sources: official micromamba distribution and the conda-forge package registry.
"""
import io
import os
import subprocess
import tarfile
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent / '.integration-tools'


def main():
    ROOT.mkdir(exist_ok=True)
    executable = ROOT / 'micromamba.exe'
    if not executable.exists():
        response = requests.get('https://micro.mamba.pm/api/micromamba/win-64/latest', timeout=120)
        response.raise_for_status()
        with tarfile.open(fileobj=io.BytesIO(response.content), mode='r:bz2') as archive:
            member = archive.extractfile('Library/bin/micromamba.exe')
            executable.write_bytes(member.read())
    environment = {**os.environ, 'MAMBA_ROOT_PREFIX': str(ROOT / 'mamba'),
                   'MAMBA_NO_BANNER': '1'}
    subprocess.run([str(executable), 'create', '-y', '--no-rc', '-r', str(ROOT / 'mamba'),
                    '-p', str(ROOT / 'runtime'), '-c', 'conda-forge',
                    'postgresql=16.15', 'pgvector=0.8.6', 'go=1.25.*=nocgo*'],
                   env=environment, check=True)


if __name__ == '__main__':
    main()
