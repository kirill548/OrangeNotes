"""Native command modifier without rewriting literal text or note contents."""
import sys


def shortcut(value):
    # Qt maps ControlModifier to Command on macOS. Meta is physical Control.
    return value


def shortcut_label(value):
    return value.replace('Ctrl+', 'Cmd+') if sys.platform == 'darwin' else value
