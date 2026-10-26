#!/bin/bash
# Shell脚本用于启动ChatterPal

echo "启动ChatterPal..."

# 检查是否安装了uv
if ! command -v uv &> /dev/null; then
    echo "错误: 未找到uv包管理器"
    echo "请访问 https://docs.astral.sh/uv/getting-started/installation/ 安装uv"
    exit 1
fi

# 运行应用
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
uv run --no-sync --project "$SCRIPT_DIR/.." python "$SCRIPT_DIR/run.py" "$@"