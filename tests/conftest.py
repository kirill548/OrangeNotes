"""Keep portable application state out of the developer's installation."""
import sys

import pytest


@pytest.fixture(autouse=True)
def isolated_application_directory(request, tmp_path_factory, monkeypatch):
    if request.node.path.name == 'test_app_paths.py':
        return
    from app.services import app_paths
    original = app_paths.get_app_dir
    root = tmp_path_factory.mktemp('portable-application')
    for module in tuple(sys.modules.values()):
        if module and getattr(module, '__name__', '').startswith('app.'):
            if getattr(module, 'get_app_dir', None) is original:
                monkeypatch.setattr(module, 'get_app_dir', lambda: root)
