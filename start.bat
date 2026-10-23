@echo off
chcp 936 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 字幕翻译工作台
set PYTHONIOENCODING=gbk
set PYTHONUTF8=0
set NO_BROWSER=0

set "PY="
set "PYARG="
for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY (
  for /f "delims=" %%i in ('where py 2^>nul') do if not defined PY set "PY=%%i"
  if defined PY set "PYARG=-3"
)

if not defined PY (
  echo.
  echo   [!] 没找到 Python。
  echo.
  echo   这个工具需要 Python 3.9 以上，去这里下：
  echo   https://www.python.org/downloads/
  echo.
  echo   安装时务必勾上 "Add Python to PATH"，装完重新双击本文件。
  echo.
  pause
  exit /b 1
)

"!PY!" !PYARG! tools\bootstrap.py
if errorlevel 1 (
  echo.
  echo   [启动失败] 上面有报错，截图发我。
  echo.
  pause
  exit /b 1
)

echo.
"!PY!" !PYARG! app.py
if errorlevel 1 (
  echo.
  echo   [启动失败] 上面有报错，截图发我。
  echo.
)
pause
