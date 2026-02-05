@echo off
echo ========================================
echo    SDV System - FMU to CAN Bridge
echo ========================================

REM Cấu hình
set FMU_PATH=C:\Users\LOQ\Workspace\06_Emtek\01_Workspace\Test_vECU\autoLamp.fmu
set WSL_HOST=localhost
set WSL_PORT=8888
set DURATION=60

echo.
echo Starting FMU CAN Bridge...
echo FMU: %FMU_PATH%
echo WSL: %WSL_HOST%:%WSL_PORT%
echo Duration: %DURATION% seconds
echo.

python fmu_can_bridge.py --fmu "%FMU_PATH%" --mode can --duration %DURATION% --wsl-host %WSL_HOST% --wsl-port %WSL_PORT%

echo.
echo Bridge stopped.
pause