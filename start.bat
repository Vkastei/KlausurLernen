@echo off
cd /d "%~dp0"
python -c "import pymupdf, pptx, docx" 2>nul || python -m pip install -q -r requirements.txt
python -m app.server %*
pause
