@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== ReelRank 安装 ====
where python >nul 2>nul
if errorlevel 1 (
  echo [X] 没有找到 Python。请到 https://www.python.org/downloads/ 安装 3.11，安装时勾选 "Add Python to PATH"，然后重新运行本脚本。
  pause & exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo 正在安装 ffmpeg ...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  echo.
  echo [!] ffmpeg 装好后需要关闭这个窗口，再双击运行一次 install.bat。
  pause & exit /b 0
)
echo 正在安装 Python 依赖（第一次需要几分钟）...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 ( echo [X] 依赖安装失败，请把上面的错误信息发给 Claude。 & pause & exit /b 1 )
if not exist config.yaml (
  copy config.example.yaml config.yaml >nul
  echo 已创建 config.yaml，正在用记事本打开，请填入 tmdb_token 和 omdb_key 后保存。
  notepad config.yaml
)
echo.
echo ==== 安装完成 ====
pause
