@echo off
cd /d %~dp0
set PYTHONPATH=%CD%\src
python -m touchsight_edu --host 0.0.0.0 --port 8877
pause

