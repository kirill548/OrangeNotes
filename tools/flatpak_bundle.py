"""Provision a commit lock online, then package exclusively from local dependencies."""
import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'packaging/flatpak/org.orangenotes.OrangeNotes.json'


def run(args):
    return subprocess.run(args,check=True,capture_output=True,text=True).stdout.strip()


def bundle_hash(folder):
    digest=hashlib.sha256()
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            if not path.resolve().is_relative_to(folder.resolve()):raise ValueError('Bundle symlink escapes source')
            digest.update(b'link\0'+path.relative_to(folder).as_posix().encode()+b'\0'+str(path.readlink()).encode())
            continue
        if path.is_file():
            digest.update(path.relative_to(folder).as_posix().encode()+b'\0')
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def validate_lock(value,refs):
    if not isinstance(value,dict) or set(value)!={'refs','commits'}:
        raise ValueError('Invalid runtime lock schema')
    if value['refs']!=refs or not isinstance(value['commits'],dict) or set(value['commits'])!=set(refs):
        raise ValueError('Lock architecture/runtime does not match manifest')
    if any(not isinstance(c,str) or not re.fullmatch('[a-f0-9]{64}',c) for c in value['commits'].values()):
        raise ValueError('Invalid immutable commit')
    return value


def write_lock(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.runtime-lock-',delete=False) as output:
            temporary=Path(output.name)
            json.dump(value,output,indent=2);output.write('\n');output.flush();os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary:temporary.unlink(missing_ok=True)


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--prepare',action='store_true',help='Online dependency provisioning; creates or reuses immutable commit lock')
    parser.add_argument('--bootstrap',action='store_true',help='Explicit first-time online resolution; retain and commit the resulting lock')
    parser.add_argument('--lock',type=Path,default=ROOT/'packaging/flatpak/runtime-lock.json')
    args=parser.parse_args(argv)
    manifest=json.loads(MANIFEST.read_text())
    arch=run(['flatpak','--default-arch'])
    refs=[name+'/'+arch+'/'+manifest['runtime-version'] for name in (manifest['runtime'],manifest['sdk'])]
    locked=validate_lock(json.loads(args.lock.read_text()),refs) if args.lock.exists() else None
    if args.bootstrap and not args.prepare:raise ValueError('--bootstrap requires --prepare')
    if args.prepare and locked is None and not args.bootstrap:
        raise ValueError('Missing runtime commit lock: first provisioning requires explicit --prepare --bootstrap')
    if args.prepare:
        run(['flatpak','remote-add','--user','--if-not-exists','flathub','https://flathub.org/repo/flathub.flatpakrepo'])
        for ref in refs:
            run(['flatpak','install','--user','--noninteractive','-y','flathub',ref])
            if locked:run(['flatpak','update','--user','--noninteractive','-y','--commit='+locked['commits'][ref],ref])
        commits={ref:run(['flatpak','info','--user','--show-commit',ref]) for ref in refs}
        if locked and commits!=locked['commits']:raise ValueError('Provisioned commit mismatch')
        verified=validate_lock({'refs':refs,'commits':commits},refs)
        write_lock(args.lock,verified)
        return
    if not locked:raise ValueError('Missing runtime commit lock: run --prepare on Linux first and retain the lock')
    for ref in refs:
        if run(['flatpak','info','--user','--show-commit',ref])!=locked['commits'][ref]:
            raise ValueError('Installed SDK/runtime differs from lock; provision exact commits first')
    bundle=ROOT/'dist/OrangeNotes'
    if not (bundle/'OrangeNotes').is_file():raise ValueError('Build frozen Linux dependencies first')
    provenance={'runtime_lock':locked,'prebuilt_bundle_sha256':bundle_hash(bundle)}
    run(['flatpak-builder','--user','--disable-download','--disable-updates','--force-clean',
         '--repo='+str(ROOT/'dist/flatpak-repo'),str(ROOT/'build/flatpak'),str(MANIFEST)])
    run(['flatpak','build-bundle',str(ROOT/'dist/flatpak-repo'),str(ROOT/'dist'/('OrangeNotes-Linux-'+arch+'.flatpak')),manifest['app-id']])
    (ROOT/'dist/Flatpak-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')


if __name__=='__main__':main()
