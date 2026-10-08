"""Read-only WinRT diagnostics. A blocked toast must not be reported as delivered."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.windows_notifications import WindowsNotifications


def main():
    transport=WindowsNotifications()
    status=transport.status()
    result={'setting':status.get('setting'),'history_count':len(transport.history()),
            'banner_verified':False,'native_button_clicks_verified':False,
            'next_step':'Enable Windows notifications, then verify a real reminder and both actions in the notification center.'}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['setting']=='Enabled' else 2


if __name__=='__main__':sys.exit(main())
