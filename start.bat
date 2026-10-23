@echo off
chcp 936 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 字幕翻译工作台
set PYTHONIOENCODING=gbk
set PYTHONUTF8=0
if not defined NO_BROWSER set "NO_BROWSER=0"

call :find_python
if not defined PY goto :nopy

"!PY!" !PYARG! tools\bootstrap.py
if errorlevel 1 goto :failed

echo.
"!PY!" !PYARG! app.py
if errorlevel 1 goto :failed
goto :done

rem ---------------- 找 Python ----------------
rem PATH 里未必有：不少机器装 Python 时没勾 "Add to PATH"，
rem 只把安装目录留在 LocalAppData 里。三层兜底才靠得住。
:find_python
set "PY="
set "PYARG="
for /f "delims=" %%i in ('where python 2^>nul') do call :try "%%i" ""
if not defined PY for /f "delims=" %%i in ('where py 2^>nul') do call :try "%%i" "-3"
if not defined PY call :scan "%LOCALAPPDATA%\Programs\Python"
if not defined PY call :scan "%LOCALAPPDATA%\Microsoft\WindowsApps"
if not defined PY call :scan "%ProgramFiles%"
if not defined PY call :scan "%ProgramFiles(x86)%"
if not defined PY call :scan "C:/"
goto :eof

:try
if defined PY goto :eof
echo %~1 | find /i "WindowsApps" >nul && goto :eof
if not exist "%~1" goto :eof
set "PY=%~1"
set "PYARG=%~2"
goto :eof

:scan
for /d %%d in ("%~1\Python3*") do if not defined PY if exist "%%~d\python.exe" set "PY=%%~d\python.exe"
goto :eof

rem ---------------- 出错 ----------------
:nopy
echo.
echo   没找到 Python。这个工具需要 Python 3.9 以上。
echo.
echo   去这里下载：https://www.python.org/downloads/
echo   安装时请勾上 "Add python.exe to PATH"，装完重新双击本文件。
echo.
pause
exit /b 1

:failed
echo.
echo   [启动失败] 上面有报错，截图发我。
echo.
pause
exit /b 1

:done
pause
