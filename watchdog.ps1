# Watchdog for xaubot — restart bot (and MT5 if needed) when heartbeat is stale.
# Run: powershell -ExecutionPolicy Bypass -File .\watchdog.ps1
# Schedule: Task Scheduler AtLogOn only (MT5 needs an interactive session).
#
# VPS-only overrides (gitignored): create watchdog.local.ps1 next to this file, e.g.
#   $Python = "C:\Users\Administrator\.venv\Scripts\python.exe"
#   $StaleSec = 180   # only after heartbeat.txt is proven fresh
# Or set WATCHDOG_PYTHON=... in .env
#
# Do NOT put Redirect*/Start-Process logic in local — those live here as defaults.
# Bot file log: logs/bot.log (TimedRotatingFileHandler). stderr here is crash-only.

$BotDir = if ($PSScriptRoot) { $PSScriptRoot } else { "C:\bots\xauusd" }
# Full path required: relative "bot.py" broke process detection (multiple bots).
$EntryScript = Join-Path $BotDir "bot.py"
$HeartbeatFile = Join-Path $BotDir "heartbeat.txt"
$StaleSec = 90
$CheckEverySec = 30
$LogFile = Join-Path $BotDir "watchdog.log"
$LogsDir = Join-Path $BotDir "logs"
$StderrRetainDays = 14

$EnvFile = Join-Path $BotDir ".env"
$Mt5Path = $null
$PythonFromEnv = $null
$SupabaseUrl = $null
$SupabaseKey = $null
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*MT5_PATH\s*=\s*(.+)\s*$') {
            $Mt5Path = $Matches[1].Trim().Trim('"').Trim("'")
        }
        if ($_ -match '^\s*WATCHDOG_PYTHON\s*=\s*(.+)\s*$') {
            $PythonFromEnv = $Matches[1].Trim().Trim('"').Trim("'")
        }
        if ($_ -match '^\s*SUPABASE_URL\s*=\s*(.+)\s*$') {
            $SupabaseUrl = $Matches[1].Trim().Trim('"').Trim("'")
        }
        if ($_ -match '^\s*SUPABASE_SERVICE_ROLE_KEY\s*=\s*(.+)\s*$') {
            $SupabaseKey = $Matches[1].Trim().Trim('"').Trim("'")
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

# Local overrides survive git pull (file is gitignored). Variables only.
$LocalOverride = Join-Path $BotDir "watchdog.local.ps1"
if (Test-Path $LocalOverride) {
    . $LocalOverride
}

function Write-Log([string]$msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $msg
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Host $line
}

function Ensure-LogsDir {
    if (-not (Test-Path $LogsDir)) {
        New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
    }
}

function Get-HeartbeatAgeSec {
    if (-not (Test-Path $HeartbeatFile)) { return $null }
    $age = (Get-Date).ToUniversalTime() - (Get-Item $HeartbeatFile).LastWriteTimeUtc
    return [math]::Round($age.TotalSeconds, 1)
}

function Rotate-StderrLog([string]$path) {
    if (-not (Test-Path $path)) { return }
    $stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
    $dest = Join-Path $LogsDir ("stderr_{0}.log" -f $stamp)
    try {
        Move-Item -LiteralPath $path -Destination $dest -Force
        Write-Log "Rotated stderr -> $dest"
    } catch {
        Write-Log "Could not rotate stderr: $_"
    }
}

function Cleanup-OldStderr {
    Ensure-LogsDir
    $cutoff = (Get-Date).AddDays(-$StderrRetainDays)
    Get-ChildItem -Path $LogsDir -Filter "stderr_*.log" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff } |
        ForEach-Object {
            try { Remove-Item -LiteralPath $_.FullName -Force } catch { }
        }
}

function Emit-BotEventRestart([double]$ageSec) {
    if (-not $SupabaseUrl -or -not $SupabaseKey) { return }
    $uri = ($SupabaseUrl.TrimEnd('/') + "/rest/v1/bot_events")
    $body = @{
        level = "WARNING"
        kind  = "restart"
        msg   = ("heartbeat stale age={0}s StaleSec={1}" -f $ageSec, $StaleSec)
        data  = @{ age = $ageSec; StaleSec = $StaleSec }
    } | ConvertTo-Json -Compress
    try {
        Invoke-RestMethod -Method Post -Uri $uri -Body $body -ContentType "application/json" `
            -Headers @{
                apikey           = $SupabaseKey
                Authorization    = "Bearer $SupabaseKey"
                Prefer           = "return=minimal"
            } -TimeoutSec 5 | Out-Null
    } catch {
        Write-Log "bot_events restart emit failed: $_"
    }
}

function Get-BotProcesses {
    # Match this project's bot by full script path in CommandLine.
    # Windows venv redirector often runs base Python311\python.exe as the child.
    $scriptName = [System.IO.Path]::GetFileName($EntryScript)
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            if (-not $_.CommandLine) { return $false }
            $cl = $_.CommandLine
            ($cl -like "*$EntryScript*") -or (
                ($cl -like "*$scriptName*") -and ($cl -like "*$BotDir*")
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
    Ensure-LogsDir
    Cleanup-OldStderr
    $existing = @(Get-BotProcesses)
    if ($existing.Count -gt 0) {
        Write-Log "Bot already running (pids: $($existing.ProcessId -join ', '))"
        return
    }
    if (-not (Test-Path $EntryScript)) {
        Write-Log "Entry script missing: $EntryScript"
        return
    }
    $stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
    $stderrPath = Join-Path $LogsDir ("stderr_{0}.log" -f $stamp)
    $stdoutPath = Join-Path $LogsDir ("stdout_{0}.log" -f $stamp)
    Write-Log "Starting bot: $Python $EntryScript (stdout=$stdoutPath stderr=$stderrPath)"
    # Fresh dated stderr/stdout each start — never overwrite prior crash logs.
    # Main application log is logs/bot.log written by the bot itself.
    Start-Process -FilePath $Python `
        -ArgumentList "`"$EntryScript`"" `
        -WorkingDirectory $BotDir `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Minimized
}

function Restart-Bot {
    $age = Get-HeartbeatAgeSec
    $ageTxt = if ($null -eq $age) { "missing" } else { "$age" }
    Write-Log "Heartbeat stale or missing — restarting bot age=${ageTxt}s StaleSec=$StaleSec"
    $ageNum = if ($null -eq $age) { [double]$StaleSec } else { [double]$age }
    Emit-BotEventRestart $ageNum

    # Preserve any leftover fixed-name err log from older watchdog versions.
    Ensure-LogsDir
    Rotate-StderrLog (Join-Path $BotDir "bot_err.log")
    Rotate-StderrLog (Join-Path $BotDir "bot_out.log")

    Get-BotProcesses | ForEach-Object {
        Write-Log "Stopping pid $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
    Start-Bot
}

Write-Log "Watchdog started. bot=$BotDir entry=$EntryScript stale=${StaleSec}s python=$Python"
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
