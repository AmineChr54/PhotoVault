# Builds dist\PhotoVault\PhotoVault.exe and puts a "Photo Vault" shortcut on the Desktop.
# (No $ErrorActionPreference=Stop: PyInstaller logs to stderr, which PowerShell 5.1 would treat as an error.
#  Failures are detected through $LASTEXITCODE instead.)
Set-Location $PSScriptRoot

python -m pytest -q -p no:cacheprovider tests
if ($LASTEXITCODE -ne 0) { throw "Tests failed - not building." }

python tools\make_icon.py
python -m PyInstaller --noconfirm --clean --windowed --name PhotoVault --icon icon.ico `
    --add-data "icon.ico;." --add-data "icon-256.png;." --add-data "docs/USER_GUIDE.md;docs" --add-data "CHANGELOG.md;." --add-data "photovault;worker_src/photovault" --add-data "requirements-ai.txt;worker_src" --add-data "constraints.txt;worker_src" --exclude-module tkinter --exclude-module matplotlib run.pyw
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$exe = Join-Path $PSScriptRoot "dist\PhotoVault\PhotoVault.exe"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop "Photo Vault.lnk"))
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = Split-Path $exe
$lnk.IconLocation = "$exe,0"
$lnk.Description = "Photo Vault - organize, de-duplicate and back up your photos"
$lnk.Save()
Write-Host "Built $exe and created a Desktop shortcut."
