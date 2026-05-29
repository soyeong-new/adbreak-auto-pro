#!/bin/bash
#드라이브
cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro
../.venv/bin/python watcher.py "/Users/choisoyeong/Library/CloudStorage/GoogleDrive-so-yeong@its-newid.com/내 드라이브/AD Break"

# 로컬
cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro
../.venv/bin/python app.py

# smb

cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro
../.venv/bin/python watcher.py "/Volumes/guest1/Public/Drop Box"