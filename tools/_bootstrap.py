"""Allow direct tool execution while keeping the application code in src."""
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parent.parent/'src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0,str(SOURCE))
