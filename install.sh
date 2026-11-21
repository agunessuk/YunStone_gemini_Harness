#!/bin/sh
# Mac·Linux 설치 진입점. 사용법: ./install.sh [--dry-run | --uninstall]
cd "$(dirname "$0")" && PYTHONIOENCODING=utf-8 python3 install.py "$@"
