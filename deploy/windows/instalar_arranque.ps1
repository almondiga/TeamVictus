# Instala el bot como tarea programada de Windows:
#  - Arranca automáticamente cuando inicias sesión en el PC.
#  - Si el bot se cae por error, lo relanza (hasta 5 veces, cada 5 min).
# Uso:  ejecutar con PowerShell (botón derecho > "Ejecutar con PowerShell")
$ErrorActionPreference = "Stop"

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat = Join-Path $dir "start_bot.bat"

if (-not (Test-Path $bat)) {
    Write-Host "❌ No encuentro start_bot.bat en $dir" -ForegroundColor Red
    exit 1
}

$action   = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $dir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                                         -RestartCount 5 `
                                         -RestartInterval (New-TimeSpan -Minutes 5) `
                                         -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "OPTCG Discord Bot" `
                       -Action $action `
                       -Trigger $trigger `
                       -Settings $settings `
                       -Force | Out-Null

Write-Host "✅ Tarea 'OPTCG Discord Bot' creada: arranca al iniciar sesión y se reinicia si se cae." -ForegroundColor Green
Get-ScheduledTask -TaskName "OPTCG Discord Bot" | Select-Object TaskName, State
