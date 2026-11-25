@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" ( echo 用法：把 episodes 文件夹里的 .yaml 文案文件拖到 check.bat 图标上。 & pause & exit /b 1 )
python -m reelrank check "%~1"
pause
