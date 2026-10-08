"""Install or inspect Orange Notes' per-user Windows reminder task."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.windows_background import BackgroundTaskError, install_background, inspect_background, remove_background, start_background


def main():
    parser = argparse.ArgumentParser(description='Фоновые напоминания Orange Notes через планировщик Windows.')
    parser.add_argument('action', choices=['install', 'inspect', 'start', 'remove'])
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--dependencies')
    parser.add_argument('--database')
    args = parser.parse_args()
    try:
        if args.action == 'install':
            status = install_background(args.python, Path(__file__).resolve().parents[1] / 'app' / 'main.py', args.dependencies, args.database)
        else:
            status = {'inspect': inspect_background, 'start': start_background, 'remove': remove_background}[args.action]()
    except BackgroundTaskError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
