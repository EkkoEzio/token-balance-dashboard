#!/bin/bash
# token 余额看板启动脚本
# 双击此文件即可启动,浏览器打开 http://localhost:5060
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "首次运行,正在创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip -q 2>&1
  # 安装依赖:失败时显示完整错误(不用 -q,让用户看到具体原因)
  if ! pip install -r requirements.txt 2>&1; then
    echo ""
    echo "============================================"
    echo "依赖安装失败!请看上面的错误信息。"
    echo "常见原因:网络问题、Python 版本兼容。"
    echo "============================================"
    read -n 1 -s -r -p "按任意键关闭窗口..."
    exit 1
  fi
else
  source .venv/bin/activate
fi

# 启动前清掉占用 5060 的残留进程(避免重复双击 / 上次没关干净导致端口冲突)
OLDPID=$(lsof -nP -iTCP:5060 -sTCP:LISTEN -t 2>/dev/null)
if [ -n "$OLDPID" ]; then
  echo "检测到端口 5060 被占用(PID $OLDPID),正在关闭旧进程..."
  kill "$OLDPID" 2>/dev/null
  sleep 1
fi

# 自动在浏览器打开(后台执行,2秒后)
( sleep 2 && open "http://localhost:5060" ) &

python3 app.py
read -n 1 -s -r -p "按任意键关闭窗口..."
