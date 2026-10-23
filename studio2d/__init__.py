"""studio2d - 离线程序化 2D 动漫角色生成器（仅依赖 Pillow）。

用法:
    python -m studio2d.cli create --spec contract/characters/laozhou.json \
        --outdir outputs/characters/laozhou/2d
"""

__version__ = "1.0.0"
RENDERER = "pillow"
