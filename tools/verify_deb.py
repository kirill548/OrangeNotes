"""Check package ownership, payload and absence of privileged install hooks."""
import os
from pathlib import PurePosixPath
import subprocess
import sys
import tarfile


def verify(filename):
    if os.geteuid()==0:raise RuntimeError('Run this packaging verification as an unprivileged user')
    control=subprocess.run(['dpkg-deb','--ctrl-tarfile',filename],check=True,capture_output=True)
    import io
    with tarfile.open(fileobj=io.BytesIO(control.stdout)) as archive:
        if any(PurePosixPath(m.name).name in ('preinst','postinst','prerm','postrm','triggers') for m in archive):
            raise RuntimeError('Package contains install hooks; service setup must remain user-level')
    process=subprocess.Popen(['dpkg-deb','--fsys-tarfile',filename],stdout=subprocess.PIPE)
    names=set()
    try:
        with tarfile.open(fileobj=process.stdout,mode='r|') as archive:
            for item in archive:
                name=item.name.removeprefix('./');names.add(name)
                if item.uid or item.gid:raise RuntimeError('Package ownership must be root:root metadata')
                if name.startswith(('etc/','usr/lib/systemd/','lib/systemd/')):raise RuntimeError('Unexpected host service payload')
        if process.wait(timeout=30):raise RuntimeError('dpkg-deb extraction failed')
    finally:
        process.stdout.close()
        if process.poll() is None:process.kill();process.wait()
    required={'opt/orangenotes/OrangeNotes','usr/bin/orange-notes','usr/share/applications/orange-notes.desktop'}
    if not required<=names:raise RuntimeError('Incomplete package payload')
    print('Unprivileged deb verification passed; no system service or maintainer hooks.')


if __name__=='__main__':verify(sys.argv[1])
