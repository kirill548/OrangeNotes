"""Record checksums and provenance of native candidate packages, without publishing."""
import argparse
import hashlib
import json
import platform
import re
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path('dist'))
    parser.add_argument('--commit',required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'[a-fA-F0-9]{40}',args.commit):
        parser.error('A full Git commit SHA is required.')
    suffixes=('.zip','.tar.gz','.dmg','.AppImage','.deb','.flatpak')
    files=sorted(p for p in args.directory.glob('OrangeNotes-*') if p.is_file() and p.name.endswith(suffixes))
    if not files:
        raise ValueError('No native release candidate packages were produced.')
    records=[]
    for path in files:
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest.update(chunk)
        records.append({'file':path.name,'size':path.stat().st_size,'sha256':digest.hexdigest()})
    manifest={'version':'0.1.5','status':'candidate','source_commit':args.commit.lower(),
              'os':platform.system(),'architecture':platform.machine(),'packages':records,
              'live_llm_qualified':False,'release_published':False}
    (args.directory/'CANDIDATE_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (args.directory/'SHA256SUMS.txt').write_text(''.join(r['sha256']+'  '+r['file']+'\n' for r in records),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False))


if __name__=='__main__':
    main()
