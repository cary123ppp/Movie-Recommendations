@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" ( echo 用法：把 scripts 文件夹里的 .txt 文案拖到 plan.bat 图标上。 & pause & exit /b 1 )
python -m reelrank plan "%~1"
if not errorlevel 1 start "" "%~dp0episodes"
pause
