"""Download and SHA-256 verify a pinned, official offline Whisper snapshot."""
import hashlib
import json
from pathlib import Path
from huggingface_hub import HfApi,hf_hub_download

REPOSITORY='Systran/faster-whisper-tiny'
REVISION='d90ca5fe260221311c53c58e660288d3deb8d356'
FILES=('model.bin','config.json','tokenizer.json','vocabulary.txt')


def main():
    target=Path(__file__).resolve().parent/'models/faster-whisper-tiny'
    info=HfApi().model_info(REPOSITORY,revision=REVISION,files_metadata=True)
    assert info.sha==REVISION
    hashes={}
    for entry in info.siblings:
        if entry.rfilename not in FILES:continue
        path=Path(hf_hub_download(REPOSITORY,entry.rfilename,revision=REVISION,local_dir=target))
        value=path.read_bytes()
        digest=hashlib.sha256(value).hexdigest()
        assert (digest==entry.lfs.sha256 if entry.lfs else hashlib.sha1(f'blob {len(value)}\0'.encode()+value).hexdigest()==entry.blob_id)
        hashes[entry.rfilename]=digest
    assert len(hashes)==len(FILES)
    (target/'manifest.json').write_text(json.dumps({'model':REPOSITORY,'revision':REVISION,'sha256':hashes},indent=2),encoding='utf-8')
    print('Pinned local Whisper verified',flush=True)


if __name__=='__main__':main()
