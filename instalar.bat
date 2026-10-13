@echo off
REM ==================================================================
REM  Instala epacal en una carpeta aislada (.venv) dentro de este directorio.
REM  Requisito: Python 3.11 o 3.12 de 64 bits (python.org), con la casilla
REM  "Add python.exe to PATH" marcada al instalarlo.
REM ==================================================================
cd /d "%~dp0"
echo Creando entorno de Python en .venv ...
py -3 -m venv .venv 2>nul || python -m venv .venv
if errorlevel 1 (
  echo No se encontro Python. Instale Python 3.12 de 64 bits desde https://www.python.org
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo Fallo la instalacion de paquetes. Revise la conexion a internet.
  pause
  exit /b 1
)
python -c "from epacal import engine as en; v=en.library_version(); print('OK: EPANET %%d.%%d listo' %% (v//10000, v//100%%100))"
echo.
echo Instalacion terminada. Ahora puede ejecutar calibrar.bat
pause
