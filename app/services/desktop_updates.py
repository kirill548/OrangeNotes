"""Verified downloads from the official Orange Notes GitHub release."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.version import APP_VERSION


REPOSITORY = 'https://github.com/kirill548/OrangeNotes'
LATEST_RELEASE = 'https://api.github.com/repos/kirill548/OrangeNotes/releases/latest'
INSTALLER = 'Orange-Notes-Setup.exe'
UPDATE_DIR_PREFIX = 'OrangeNotes-update-'
STALE_DOWNLOAD_AGE = 24 * 60 * 60
MAX_INSTALLER_BYTES = 1024 ** 3
VERSION = re.compile(r'(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})')
ASSET_HOSTS = {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}


@dataclass(frozen=True)
class Release:
    version: str
    page: str
    installer: str | None
    size: int = 0
    sha256: str = ''


def _version(value):
    match = VERSION.fullmatch(value) if isinstance(value, str) else None
    return tuple(map(int, match.groups())) if match else None


def select_release(metadata, current=APP_VERSION):
    if not isinstance(metadata, dict):
        raise ValueError('Invalid release response')
    if metadata.get('draft') is not False or metadata.get('prerelease') is not False:
        return None
    tag = metadata.get('tag_name')
    version = tag[1:] if isinstance(tag, str) and tag.startswith('v') else ''
    parsed = _version(version)
    if parsed is None or parsed <= _version(current):
        return None
    page = f'{REPOSITORY}/releases/tag/{tag}'
    installer = f'{REPOSITORY}/releases/download/{tag}/{INSTALLER}'
    assets = metadata.get('assets')
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict) or asset.get('name') != INSTALLER:
                continue
            size = asset.get('size')
            digest = asset.get('digest')
            if (asset.get('state') == 'uploaded' and asset.get('browser_download_url') == installer
                    and type(size) is int and 0 < size <= MAX_INSTALLER_BYTES
                    and isinstance(digest, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', digest)):
                return Release(version, page, installer, size, digest[7:])
    return Release(version, page, None)


class _GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if (request.full_url == LATEST_RELEASE or url.scheme != 'https'
                or url.netloc not in ASSET_HOSTS):
            raise ValueError('Unexpected GitHub redirect')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _open(url):
    return build_opener(_GitHubRedirect()).open(
        Request(url, headers={'User-Agent': 'OrangeNotes/' + APP_VERSION}), timeout=12)


def check_for_update(open_url=_open):
    clean_old_downloads()
    limit = 1024 ** 2
    with open_url(LATEST_RELEASE) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Release response is too large')
    return select_release(json.loads(data))


def download_update(release, cancelled, open_url=_open):
    if not release.installer:
        raise ValueError('No verified installer')
    if cancelled.is_set():
        raise ValueError('Installer download cancelled')
    digest = hashlib.sha256()
    total = 0
    folder = Path(tempfile.mkdtemp(prefix=UPDATE_DIR_PREFIX))
    target = folder / INSTALLER
    try:
        with open_url(release.installer) as response, target.open('xb') as output:
            while not cancelled.is_set():
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > release.size:
                    raise ValueError('Installer exceeds expected size')
                digest.update(chunk)
                output.write(chunk)
        if cancelled.is_set() or total != release.size or digest.hexdigest() != release.sha256:
            raise ValueError('Installer download interrupted or checksum mismatch')
        return target
    except BaseException:
        discard_installer(target)
        raise


def discard_installer(path):
    try:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
    except OSError:
        pass


def clean_old_downloads():
    """Remove expired update files left after an installer or the app closed."""
    cutoff = time.time() - STALE_DOWNLOAD_AGE
    for folder in Path(tempfile.gettempdir()).glob(f'{UPDATE_DIR_PREFIX}????????'):
        try:
            info = folder.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_mtime >= cutoff
                    or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                continue
            target = folder / INSTALLER
            try:
                info = target.lstat()
            except FileNotFoundError:
                folder.rmdir()
                continue
            if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                discard_installer(target)
        except OSError:
            continue
