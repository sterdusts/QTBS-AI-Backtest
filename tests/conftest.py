import sys
from pathlib import Path

# 保证无论从哪里运行 pytest，都能 import 项目模块
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
