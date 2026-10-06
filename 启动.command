#!/usr/bin/env bash
cd "$(dirname "$0")"
bash scripts/start-local.sh
echo '服务已停止。按回车关闭。'
read -r
