<#
.SYNOPSIS
  Ajastaa `forecaster update` -päivityksen viikoittain Windowsin Tehtävien ajoitukseen (K7).

.DESCRIPTION
  Tehtävä ajetaan nykyisen käyttäjän oikeuksin, kun käyttäjä on kirjautuneena (salasanaa ei
  tallenneta). Jos kone on ajankohtana pois päältä, ajo tehdään seuraavalla käynnistyksellä
  (StartWhenAvailable). Ikkuna on piilotettu; tuloste menee tiedostoon
  data\logs\scheduled_last.txt ja ajon yhteenveto tiedostoon data\logs\update_*.log.
  Hälytykset näkyvät dashboardin yläreunassa ja Datan laatu -sivulla.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\schedule_update.ps1
  powershell -ExecutionPolicy Bypass -File scripts\schedule_update.ps1 -Day Sunday -Time 20:00
  powershell -ExecutionPolicy Bypass -File scripts\schedule_update.ps1 -Remove
#>
param(
    [ValidateSet("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")]
    [string]$Day = "Monday",
    [ValidatePattern("^\d{2}:\d{2}$")]
    [string]$Time = "07:00",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "AI Benchmark Forecaster - update"
$Root = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Ajastus poistettu: $TaskName"
    return
}

$Exe = Join-Path $Root ".venv\Scripts\forecaster.exe"
if (-not (Test-Path $Exe)) {
    throw "forecaster.exe puuttuu ($Exe). Asenna ensin: pip install -e .[dev,ui] ja scripts\fix_editable_pth.py"
}
$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Log = Join-Path $LogDir "scheduled_last.txt"

# Yksinkertaiset lainausmerkit suojaavat polun &- ja ä-merkit PowerShellin -Command-tulkinnalta
$q = { param($s) "'" + ($s -replace "'", "''") + "'" }
# UTF-8: forecaster kirjoittaa uudelleenohjatessa UTF-8:aa (θ, ä); ilman tätä PowerShell 5.1
# tulkitsisi tulosteen konsolin merkistöllä ja lokiin tulisi mojibakea.
$Command = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " +
    "& $(& $q $Exe) update --root $(& $q $Root) *> $(& $q $Log)"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$Command`"" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "AI Benchmark Forecaster: haku, IRT-sovitus ja ennusteet (forecaster update)" -Force | Out-Null

$Info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Ajastettu: $TaskName"
Write-Host "  joka $Day klo $Time, seuraava ajo: $($Info.NextRunTime)"
Write-Host "  tuloste: $Log"
Write-Host "Aja heti testiksi:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Poista:             powershell -ExecutionPolicy Bypass -File scripts\schedule_update.ps1 -Remove"
