import sys
import os

# 将 src 目录加入路径以便从 src 目录作为包根目录访问
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 导入 tools 模块以触发注册
import tools
import tools.file_tools

# 现在 fuxi_engine 可以正常导入
from . import fuxi_engine

# 导出
FuxiEngine = fuxi_engine.FuxiEngine

__all__ = ["FuxiEngine"]
