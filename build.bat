@echo off
rem Build Phu_Don_Rac.exe vao thu muc dist\
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name Phu_Don_Rac --icon icon.ico --add-data "icon.ico;." main.py
