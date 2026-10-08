"""Optional native release signing and DMG packaging. Never claim unsigned success."""
import os
import json
from pathlib import Path
import subprocess
import sys


def _magic(path):
    with path.open('rb') as stream:return stream.read(4)


def sign_bundle(folder,target=None):
    target=target or sys.platform
    if target=='win32':
        thumbprint=os.environ.get('WINDOWS_SIGN_THUMBPRINT')
        if not thumbprint:
            print('UNSIGNED: Windows certificate/hardware provider not configured.')
            return False
        tool=(os.environ.get('SIGNTOOL_PATH') or 'signtool')
        subprocess.run([tool,'sign','/sha1',thumbprint,'/fd','SHA256','/tr',os.environ.get('SIGN_TIMESTAMP_URL','https://timestamp.digicert.com'),'/td','SHA256',str(folder/'OrangeNotes.exe')],check=True)
        subprocess.run([tool,'verify','/pa',str(folder/'OrangeNotes.exe')],check=True)
        return True
    if target=='darwin':
        identity=os.environ.get('MAC_SIGN_IDENTITY')
        if not identity:
            print('UNSIGNED: macOS Developer ID not configured.')
            return False
        # Sign nested executable code inside-out before the bundle.
        code=sorted((p for p in folder.rglob('*') if p.is_file() and (p.suffix in ('.so','.dylib') or _magic(p) in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe'))),key=lambda p:len(p.parts),reverse=True)
        for path in code+[folder]:
            subprocess.run(['codesign','--force','--timestamp','--options','runtime','--sign',identity,str(path)],check=True)
        subprocess.run(['codesign','--verify','--deep','--strict',str(folder)],check=True)
        return True
    return False


def mac_dmg(folder,destination):
    subprocess.run(['hdiutil','create','-volname','Orange Notes','-srcfolder',str(folder),'-ov','-format','UDZO',str(destination)],check=True)
    profile=os.environ.get('MAC_NOTARY_PROFILE')
    if profile:
        if not os.environ.get('MAC_SIGN_IDENTITY'):raise RuntimeError('Notarization requires signed Developer ID bundle.')
        keychain=os.environ.get('MAC_NOTARY_KEYCHAIN')
        result=subprocess.run(['xcrun','notarytool','submit',str(destination),'--keychain-profile',profile,'--wait','--output-format','json']+(['--keychain',keychain] if keychain else []),check=True,capture_output=True,text=True)
        response=json.loads(result.stdout)
        if response.get('status') != 'Accepted':
            raise RuntimeError('Apple notarization was not accepted; status: '+str(response.get('status','missing')))
        subprocess.run(['xcrun','stapler','staple',str(destination)],check=True)
        subprocess.run(['xcrun','stapler','validate',str(destination)],check=True)
    else:print('NOT NOTARIZED: MAC_NOTARY_PROFILE not configured.')
    return destination


def linux_appimage(folder,destination):
    tool=os.environ.get('APPIMAGETOOL_PATH')
    if not tool:
        print('APPIMAGE NOT BUILT: APPIMAGETOOL_PATH not configured; tar.gz remains available.')
        return None
    import shutil
    appdir=folder.parent/'OrangeNotes.AppDir'
    appdir.mkdir(parents=True,exist_ok=True)
    shutil.copytree(folder,appdir/'usr/lib/OrangeNotes',dirs_exist_ok=True)
    shutil.copy2(folder/'OrangeNotes.desktop',appdir/'OrangeNotes.desktop')
    shutil.copy2(folder/'OrangeNotes.png',appdir/'OrangeNotes.png')
    launcher=appdir/'AppRun'
    launcher.write_text('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/usr/lib/OrangeNotes/OrangeNotes" "$@"\n',encoding='utf-8')
    launcher.chmod(0o755)
    runtime=os.environ.get('APPIMAGE_RUNTIME_FILE')
    if not runtime or not Path(runtime).is_file():raise RuntimeError('Provide verified APPIMAGE_RUNTIME_FILE to prevent unpinned runtime download.')
    subprocess.run([tool,'--runtime-file',runtime,str(appdir),str(destination)],check=True)
    if not destination.is_file():raise RuntimeError('appimagetool did not produce an artifact.')
    return destination
