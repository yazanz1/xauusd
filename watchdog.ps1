# Watchdog for xaubot — restart bot (and MT5 if needed) when heartbeat is stale.
# Run: powershell -ExecutionPolicy Bypass -File .\watchdog.ps1
# Schedule: Task Scheduler AtLogOn only (MT5 needs an interactive session).
#
# Optional permanent overrides (not in git): create watchdog.local.ps1 next to this file, e.g.
#   $Python = "C:\bots\xauusd\.venv\Scripts\python.exe"
# Or set WATCHDOG_PYTHON=... in .env

$BotDir = if ($PSScriptRoot) { $PSScriptRoot } else { "C:\bots\xauusd" }
$EntryScript = "bot.py"
$HeartbeatFile = Join-Path $BotDir "heartbeat.txt"
$StaleSec = 90
$CheckEverySec = 30
$LogFile = Join-Path $BotDir "watchdog.log"

$EnvFile = Join-Path $BotDir ".env"
$Mt5Path = $null
$PythonFromEnv = $null
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*MT5_PATH\s*=\s*(.+)\s*$') {
            $Mt5Path = $Matches[1].Trim().Trim('"').Trim("'")
        }
        if ($_ -match '^\s*WATCHDOG_PYTHON\s*=\s*(.+)\s*$') {
            $PythonFromEnv = $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
}
if (-not $Mt5Path) {
    $Mt5Path = "C:\Program Files\MetaTrader 5\terminal64.exe"
}

$VenvPython = Join-Path $BotDir ".venv\Scripts\python.exe"
$Python = $null
if ($PythonFromEnv -and (Test-Path $PythonFromEnv)) {
    $Python = $PythonFromEnv
} elseif (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = "python"
}

# Local overrides survive git pull (file is gitignored).
$LocalOverride = Join-Path $BotDir "watchdog.local.ps1"
if (Test-Path $LocalOverride) {
    . $LocalOverride
}

function Write-Log([string]$msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $msg
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Host $line
}

function Get-BotProcesses {
    # Match bot.py for this folder. Do not require ExecutablePath under $BotDir:
    # Windows venv redirector often runs base Python311\python.exe as the child.
    $scriptNeedle = Join-Path $BotDir $EntryScript
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            if (-not $_.CommandLine) { return $false }
            $cl = $_.CommandLine
            ($cl -like "*$EntryScript*") -and (
                ($cl -like "*$BotDir*") -or
                ($cl -like "*$scriptNeedle*") -or
                ($cl -match [regex]::Escape($EntryScript) + '\s*$')
            )
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
        -ArgumentList "`"$EntryScript`"" `
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
