from pathlib import Path
import sys

def resource_path(relative):
    base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
    return base/relative
