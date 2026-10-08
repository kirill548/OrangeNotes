"""Ephemeral CI certificate provisioning; secrets must only reach trusted builds."""
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile


def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=180)
    if result.returncode:raise RuntimeError('Signing tool failed: '+Path(args[0]).name)
    return result.stdout.strip()


def main():
    root=Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir()))/'orangenotes-signing'
    state=root/'state.json'
    if '--cleanup' in sys.argv:
        if not state.exists():
            if root.exists():
                for path in root.iterdir():path.unlink()
                root.rmdir()
            return
        data=json.loads(state.read_text())
        if sys.platform=='darwin':
            run(['security','list-keychains','-d','user','-s',*data['search_list']])
            run(['security','delete-keychain',data['keychain']])
        elif data.get('imported'):
            os.environ['ORANGE_IMPORTED_THUMBPRINT']=data['thumbprint']
            run(['powershell','-NoProfile','-NonInteractive','-Command',
                 'Remove-Item -LiteralPath ("Cert:\\CurrentUser\\My\\" + $env:ORANGE_IMPORTED_THUMBPRINT)'])
        for path in root.iterdir():path.unlink()
        root.rmdir()
        return
    encoded=os.environ.get('WINDOWS_CERTIFICATE_BASE64' if sys.platform=='win32' else 'MAC_CERTIFICATE_BASE64')
    if not encoded:
        print('No CI certificate supplied; existing signing configuration is retained.')
        return
    if state.exists():raise RuntimeError('Signing staging directory already in use')
    root.mkdir(parents=True,exist_ok=True)
    root.chmod(0o700)
    certificate=root/'certificate.p12'
    certificate.write_bytes(base64.b64decode(encoded,validate=True));certificate.chmod(0o600)
    values={}
    if sys.platform=='win32':
        os.environ['ORANGE_CERTIFICATE_PATH']=str(certificate)
        result=run(['powershell','-NoProfile','-NonInteractive','-Command',
            '$before=@(Get-ChildItem Cert:\\CurrentUser\\My | ForEach-Object Thumbprint); '
            '$p=ConvertTo-SecureString $env:WINDOWS_CERTIFICATE_PASSWORD -AsPlainText -Force; '
            '$c=Import-PfxCertificate -FilePath $env:ORANGE_CERTIFICATE_PATH -CertStoreLocation Cert:\\CurrentUser\\My -Password $p; '
            '$c=$c | Where-Object HasPrivateKey | Select-Object -First 1; '
            'if (-not $c) { throw "No signing private key" }; '
            '@{thumbprint=$c.Thumbprint; imported=($before -notcontains $c.Thumbprint)} | ConvertTo-Json -Compress'])
        data=json.loads(result);state.write_text(json.dumps(data))
        values['WINDOWS_SIGN_THUMBPRINT']=data['thumbprint']
        tools=sorted(Path(os.environ['ProgramFiles(x86)']).glob('Windows Kits/10/bin/*/x64/signtool.exe'))
        if not tools:raise RuntimeError('Windows SDK signtool not installed')
        values['SIGNTOOL_PATH']=str(tools[-1])
    elif sys.platform=='darwin':
        import shlex
        keychain=str(root/'build.keychain-db');password=secrets.token_urlsafe(32)
        previous=shlex.split(run(['security','list-keychains','-d','user']))
        state.write_text(json.dumps({'keychain':keychain,'search_list':previous}))
        run(['security','create-keychain','-p',password,keychain])
        run(['security','set-keychain-settings','-lut','21600',keychain])
        run(['security','unlock-keychain','-p',password,keychain])
        run(['security','import',str(certificate),'-k',keychain,'-P',os.environ['MAC_CERTIFICATE_PASSWORD'],'-T','/usr/bin/codesign'])
        run(['security','set-key-partition-list','-S','apple-tool:,apple:,codesign:','-s','-k',password,keychain])
        run(['security','list-keychains','-d','user','-s',*previous,keychain])
        if not os.environ.get('MAC_SIGN_IDENTITY'):raise RuntimeError('MAC_SIGN_IDENTITY is required')
        values['MAC_SIGN_IDENTITY']=os.environ['MAC_SIGN_IDENTITY']
        credentials=[os.environ.get(k) for k in ('APPLE_ID','APPLE_TEAM_ID','APPLE_APP_PASSWORD')]
        if any(credentials) and not all(credentials):raise RuntimeError('Incomplete notarization credentials')
        if all(credentials):
            profile='OrangeNotes-CI'
            run(['xcrun','notarytool','store-credentials',profile,'--apple-id',credentials[0],'--team-id',credentials[1],'--password',credentials[2],'--keychain',keychain])
            values.update(MAC_NOTARY_PROFILE=profile,MAC_NOTARY_KEYCHAIN=keychain)
    certificate.unlink(missing_ok=True)
    lines=''.join(k+'='+v+'\n' for k,v in values.items())
    if any('\n' in v or '\r' in v for v in values.values()):raise ValueError('Invalid signing environment')
    with Path(os.environ.get('GITHUB_ENV',root/'signing.env')).open('a',encoding='utf-8') as out:out.write(lines)
    print('CI signing credentials provisioned (values omitted).')


if __name__=='__main__':main()
