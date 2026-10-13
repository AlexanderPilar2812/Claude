@echo off
REM ==================================================================
REM  Ejecuta una calibracion.
REM    calibrar.bat                          -> casos\ZP02EC\config.yaml
REM    calibrar.bat casos\MI_SECTOR\config.yaml
REM  Tambien puede arrastrar un config.yaml sobre este archivo.
REM ==================================================================
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Primero ejecute instalar.bat
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
set CFG=%~1
if "%CFG%"=="" set CFG=casos\ZP02EC\config.yaml
echo Calibrando con %CFG% ...
python -m epacal "%CFG%"
echo.
echo Terminado. Abra informe_calibracion.html en la carpeta de resultados
echo (indicada arriba en la linea "Informe:").
pause
