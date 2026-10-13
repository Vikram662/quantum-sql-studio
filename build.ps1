# Builds dist\QuantumSQL\QuantumSQL.exe with PyInstaller, then the Windows installer with Inno Setup.
#   powershell -ExecutionPolicy Bypass -File build.ps1            # exe + installer
#   powershell -ExecutionPolicy Bypass -File build.ps1 -SkipInstaller
param([switch]$SkipInstaller)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$version = (Select-String -Path "quantumsql\__init__.py" -Pattern 'APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
Write-Host "== Quantum SQL $version ==" -ForegroundColor Cyan

python -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# Qt modules the app never uses - keeps the build small.
$exclude = @("PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtOpenGL",
             "PySide6.QtOpenGLWidgets", "PySide6.QtDBus", "PySide6.QtConcurrent", "PySide6.QtSql",
             "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtPrintSupport", "PySide6.QtHelp",
             "PySide6.QtUiTools", "PySide6.QtDesigner", "tkinter", "unittest", "pydoc")
$excludeArgs = $exclude | ForEach-Object { "--exclude-module=$_" }

python -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name QuantumSQL `
    --icon app_icon.ico `
    --add-data "app_icon.ico;." `
    --add-data "docs\USER_GUIDE.md;docs" `
    --add-data "LICENSE;." `
    --add-data "THIRD_PARTY_NOTICES.txt;." `
    --version-file installer\version_info.txt `
    @excludeArgs `
    run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Drop Qt translations (English UI only).
Remove-Item -Recurse -Force "dist\QuantumSQL\_internal\PySide6\translations" -ErrorAction SilentlyContinue
Write-Host "Built dist\QuantumSQL\QuantumSQL.exe" -ForegroundColor Green

if ($SkipInstaller) { exit 0 }

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup 6 not found. Install it (winget install JRSoftware.InnoSetup) and run this script again."
    exit 1
}
& $iscc "/DAppVersion=$version" "installer\QuantumSQL.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Host "Installer: Output\QuantumSQL_Setup_$version.exe" -ForegroundColor Green
