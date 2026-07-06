#!/bin/bash

# 로컬 (html UI, localhost:8000)
cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro
../.venv/bin/python app.py

# smb (공유 폴더 자동 감시)

cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro
../.venv/bin/python watcher.py "/Volumes/guest1/Public"