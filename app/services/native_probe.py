"""Real packaged OS transport probe; never opens a user database."""
import json
import sys
import uuid


def run():
    if sys.platform not in ('linux','darwin'):
        raise OSError('Native probe requires Linux or macOS')
    from app.services.portable_notifications import notification_transport
    transport=notification_transport()
    event=900000000+uuid.uuid4().int%1000000
    transport.register()
    try:
        receipt=transport.show('Orange Notes · integration test','Disposable notification; no user notes accessed.',event,'integration-test-only')
        if not receipt.get('submitted'):raise OSError('Transport rejected request')
        print(json.dumps({'platform':sys.platform,'accepted':True,'visible_banner_verified':False}))
    finally:transport.remove(event)
    return 0
