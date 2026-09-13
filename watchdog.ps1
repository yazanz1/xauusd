# Watchdog for xaubot — restart bot (and MT5 if needed) when heartbeat is stale.
# VPS defaults; edit $BotDir if the folder differs.
# Run: powershell -ExecutionPolicy Bypass -File C:\bots\xauusd\watchdog.ps1
# Schedule: Task Scheduler AtLogOn only (MT5 needs an interactive session).

$BotDir = "C:\bots\xauusd"
$EntryScript = "bot.py"
$HeartbeatFile = Join-Path $BotDir "heartbeat.txt"
$StaleSec = 90
$CheckEverySec = 30
$LogFile = Join-Path $BotDir "watchdog.log"

$VenvPython = Join-Path $BotDir ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

$Mt5Path = $null
$EnvFile = Join-Path $BotDir ".env"
if (Test-Path $EnvFile) {
    $line = Get-Content $EnvFile | Where-Object { $_ -match '^\s*MT5_PATH\s*=' } | Select-Object -First 1
    if ($line) {
        $Mt5Path = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'")
    }
}
if (-not $Mt5Path) {
    $Mt5Path = "C:\Program Files\MetaTrader 5\terminal64.exe"
}

function Write-Log([string]$msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $msg
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Host $line
}

function Get-BotProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            ($_.CommandLine -like "*$EntryScript*") -and
            ($_.CommandLine -like "*$BotDir*" -or $_.ExecutablePath -like "*$BotDir*")
        }
}

function Test-HeartbeatFresh {
    if (-not (Test-Path $HeartbeatFile)) { return $false }
    $age = (Get-Date).ToUniversalTime() - (Get-Item $HeartbeatFile).LastWriteTimeUtc
    return ($age.TotalSeconds -lt $StaleSec)
}

function Ensure-Mt5 {
    $running = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue
    if ($running) { return }
    if (-not (Test-Path $Mt5Path)) {
        Write-Log "MT5 not found at $Mt5Path"
        return
    }
    Write-Log "Starting MT5: $Mt5Path"
    Start-Process -FilePath $Mt5Path
    Start-Sleep -Seconds 15
}

function Start-Bot {
    Ensure-Mt5
    $existing = @(Get-BotProcesses)
    if ($existing.Count -gt 0) {
        Write-Log "Bot already running (pids: $($existing.ProcessId -join ', '))"
        return
    }
    Write-Log "Starting bot: $Python $EntryScript"
    Start-Process -FilePath $Python `
        -ArgumentList $EntryScript `
        -WorkingDirectory $BotDir `
        -WindowStyle Minimized
}

function Restart-Bot {
    Write-Log "Heartbeat stale or missing — restarting bot"
    Get-BotProcesses | ForEach-Object {
        Write-Log "Stopping pid $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
    Start-Bot
}

Write-Log "Watchdog started. bot=$BotDir stale=${StaleSec}s python=$Python"
Start-Bot

while ($true) {
    try {
        $procs = @(Get-BotProcesses)
        $fresh = Test-HeartbeatFresh
        if ($procs.Count -eq 0 -or -not $fresh) {
            Restart-Bot
        } else {
            Write-Log "OK pids=$($procs.ProcessId -join ',') heartbeat_fresh=$fresh"
        }
    } catch {
        Write-Log "Watchdog error: $_"
    }
    Start-Sleep -Seconds $CheckEverySec
}
