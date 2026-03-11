@echo off
echo ======================================
echo Starting Optimized Dashboard
echo ======================================
echo.

cd /d "%~dp0"

echo Checking Streamlit version...
python -c "import streamlit as st; v=st.__version__; print(f'Streamlit {v}'); major,minor=map(int,v.split('.')[:2]); exit(0 if major>1 or (major==1 and minor>=37) else 1)"

if %errorlevel% neq 0 (
    echo.
    echo WARNING: Streamlit version is too old!
    echo Fragment feature requires Streamlit >= 1.37.0
    echo.
    echo Upgrading Streamlit...
    pip install --upgrade streamlit
    echo.
)

echo.
echo Starting dashboard_optimized.py...
echo.
echo Features:
echo   - Fragment-based partial refresh
echo   - No page flicker
echo   - Sidebar stays static
echo   - Auto-refresh every 5 seconds
echo.
echo Press Ctrl+C to stop
echo.

streamlit run dashboard_optimized.py

pause
