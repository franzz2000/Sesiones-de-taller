$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$AppName = "Programador-de-alarmas"
$PythonBin = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }

if (-not $IsWindows) {
    throw "Este script debe ejecutarse en Windows. Desde macOS utiliza build_windows.sh."
}

Write-Host "Instalando dependencias de ejecución y construcción..."
& $PythonBin -m pip install -r requirements.txt -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Construyendo $AppName.exe..."
& $PythonBin -m PyInstaller `
    --clean `
    --noconfirm `
    --onefile `
    --windowed `
    --name $AppName `
    --icon "assets/app-icon.png" `
    --add-data "assets/app-icon.png;assets" `
    --add-data "sonidos;sonidos" `
    --add-data "LICENSE;." `
    app.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Ejecutable creado en:"
Write-Host "$PSScriptRoot\dist\$AppName.exe"
