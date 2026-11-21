# Windows entry point. Usage: powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 [--dry-run | --uninstall]
$env:PYTHONIOENCODING = 'utf-8'
& python "$PSScriptRoot\install.py" @args
exit $LASTEXITCODE
