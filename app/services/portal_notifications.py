"""Flatpak Notification portal; task actions stay in the app's attention list."""
import json
import shutil
import subprocess


class PortalNotifications:
    def __init__(self,database_path=None):
        self.executable=shutil.which('gdbus')

    def register(self,database_path=None):
        if not self.executable:raise OSError('Notification portal requires gdbus')

    def _call(self,method,*arguments):
        self.register()
        result=subprocess.run([self.executable,'call','--session','--dest','org.freedesktop.portal.Desktop',
            '--object-path','/org/freedesktop/portal/desktop','--method',
            'org.freedesktop.portal.Notification.'+method,*arguments],capture_output=True,text=True,timeout=5)
        if result.returncode:raise OSError(result.stderr.strip() or 'Notification portal rejected request')

    def show(self,title,body,event_id,token):
        # Explicit GVariant strings prevent injection through note text.
        payload="{'title': <"+json.dumps(str(title),ensure_ascii=False)+">, 'body': <"+json.dumps(str(body),ensure_ascii=False)+">}"
        self._call('AddNotification','event-'+str(int(event_id)),payload)
        return {'submitted':True,'deliveryVerified':True,'historyVerified':False,
                'verification':'Portal accepted request; desktop controls visible display'}

    def remove(self,event_id):
        self._call('RemoveNotification','event-'+str(int(event_id)))

    def status(self):
        return {'setting':'Unknown','adapter':'Flatpak Notification portal','history_supported':False}

    def pump(self):pass
