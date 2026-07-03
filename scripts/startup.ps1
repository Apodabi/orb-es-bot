# Session bootstrap for orb-es-bot on Windows. NO trading process is started.
#
#   powershell -ExecutionPolicy Bypass -File scripts\startup.ps1
#
# What it does:
#   1. Launches IB Gateway (ibgateway.exe) if it isn't running, and reports
#      which API port (4001 live / 4002 paper) is reachable. Login happens in
#      the Gateway's own window with IBKR-mobile 2FA - NOT in a browser.
#   2. Probes the OPTIONAL Client Portal Gateway (https://localhost:5000,
#      the Web-API route used by ibkr_data.py). That one DOES use a browser
#      login; the script tells you if it's up and whether 2FA is needed.
#   3. Prints a status summary: git state, data files with their date ranges,
#      and where the research protocol stands per research/ROUND2_PREREG.md.

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Test-Port([int]$Port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $ok = $client.ConnectAsync("127.0.0.1", $Port).Wait(1500)
        $client.Close()
        return $ok
    } catch { return $false }
}

Write-Host ""
Write-Host "=== orb-es-bot session bootstrap ===" -ForegroundColor Cyan
Write-Host "(no trading process is started by this script)"
Write-Host ""

# --------------------------------------------------------------------------
# 1) IB Gateway (socket API, ports 4001 live / 4002 paper)
# --------------------------------------------------------------------------
Write-Host "--- IB Gateway (socket API) ---" -ForegroundColor Cyan
$proc = Get-Process ibgateway -ErrorAction SilentlyContinue
if ($null -eq $proc) {
    $exe = Get-ChildItem "C:\Jts\ibgateway" -Recurse -Filter "ibgateway.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    if ($null -eq $exe) {
        Write-Host "[!!] ibgateway.exe not found under C:\Jts - is IB Gateway installed?" -ForegroundColor Red
    } else {
        Write-Host "[..] IB Gateway not running - launching $exe"
        Start-Process $exe
        Write-Host "     Waiting up to 60s for an API port (login may take longer than this)..."
        $deadline = (Get-Date).AddSeconds(60)
        while ((Get-Date) -lt $deadline) {
            if ((Test-Port 4001) -or (Test-Port 4002)) { break }
            Start-Sleep -Seconds 5
        }
    }
} else {
    Write-Host "[OK] IB Gateway is running (pid $($proc[0].Id))"
}

$live = Test-Port 4001
$paper = Test-Port 4002
if ($live) {
    Write-Host "[OK] API port 4001 (LIVE login) is open - data fetches will work."
    Write-Host "     NOTE: 4001 means you are logged into the LIVE account. The bot's"
    Write-Host "     paper trading expects a PAPER login (port 4002)."
} elseif ($paper) {
    Write-Host "[OK] API port 4002 (PAPER login) is open."
} else {
    Write-Host "[!!] No API port open yet. ACTION NEEDED: complete the login in the" -ForegroundColor Yellow
    Write-Host "     IB Gateway window (username/password + IBKR-mobile 2FA prompt)," -ForegroundColor Yellow
    Write-Host "     then re-run this script to confirm." -ForegroundColor Yellow
}

# --------------------------------------------------------------------------
# 2) Optional Client Portal Gateway (Web API, https://localhost:5000)
# --------------------------------------------------------------------------
Write-Host ""
Write-Host "--- Client Portal Gateway (optional Web-API route) ---" -ForegroundColor Cyan
if (-not (Test-Port 5000)) {
    Write-Host "[--] Not running. Only needed for ibkr_data.py's Web-API route;"
    Write-Host "     the bot and fetchers use the socket API above instead."
} else {
    # self-signed cert on localhost: relax validation for this probe only
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
    $oldCb = [System.Net.ServicePointManager]::ServerCertificateValidationCallback
    [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    try {
        $status = Invoke-RestMethod -Method Post -Uri "https://localhost:5000/v1/api/iserver/auth/status" -TimeoutSec 10
        if ($status.authenticated) {
            Write-Host "[OK] Client Portal Gateway: authenticated brokerage session."
        } else {
            Write-Host "[!!] Client Portal Gateway is up but NOT authenticated." -ForegroundColor Yellow
            Write-Host "     ACTION NEEDED: open https://localhost:5000 in a browser and" -ForegroundColor Yellow
            Write-Host "     complete the login + 2FA there." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "[!!] Port 5000 is open but auth/status failed: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "     If this is a fresh CP Gateway start, log in at https://localhost:5000" -ForegroundColor Yellow
    } finally {
        [System.Net.ServicePointManager]::ServerCertificateValidationCallback = $oldCb
    }
}

# --------------------------------------------------------------------------
# 3) Repo status
# --------------------------------------------------------------------------
Write-Host ""
Write-Host "--- Git ---" -ForegroundColor Cyan
git -C $RepoRoot status --short --branch
git -C $RepoRoot log --oneline -1 | ForEach-Object { Write-Host "last commit: $_" }

# --------------------------------------------------------------------------
# 4) Data files + date ranges
# --------------------------------------------------------------------------
Write-Host ""
Write-Host "--- Data files ---" -ForegroundColor Cyan
$csvs = Get-ChildItem (Join-Path $RepoRoot "data") -Filter "*.csv" -ErrorAction SilentlyContinue
if ($null -eq $csvs -or $csvs.Count -eq 0) {
    Write-Host "(none - run scripts\fetch_yf.py or scripts\fetch_data.py)"
} else {
    foreach ($f in $csvs) {
        $firstData = Get-Content $f.FullName -TotalCount 2 | Select-Object -Last 1
        $lastData = Get-Content $f.FullName -Tail 1
        $t0 = ($firstData -split ",")[0]
        $t1 = ($lastData -split ",")[0]
        $mb = [math]::Round($f.Length / 1MB, 1)
        if ($t0 -match "^\d{4}-\d{2}-\d{2}") {
            Write-Host ("{0,-28} {1,7} MB   {2}  ->  {3}" -f $f.Name, $mb, $t0, $t1)
        } else {
            Write-Host ("{0,-28} {1,7} MB   (not a bar file)" -f $f.Name, $mb)
        }
    }
    Write-Host "(timestamps are UTC; consumed research datasets must never be re-tested)"
}

# --------------------------------------------------------------------------
# 5) Research protocol position (from the prereg's own amendments log)
# --------------------------------------------------------------------------
Write-Host ""
Write-Host "--- Round-2 protocol status (research/ROUND2_PREREG.md) ---" -ForegroundColor Cyan
$prereg = Join-Path $RepoRoot "research\ROUND2_PREREG.md"
if (-not (Test-Path $prereg)) {
    Write-Host "(no pre-registration found)"
} else {
    $text = Get-Content $prereg -Raw -Encoding UTF8
    if ($text -match "HOLDOUT CONSUMED") {
        Write-Host "[DONE] Round 2 is COMPLETE: holdout consumed, verdict recorded" -ForegroundColor Green
        Write-Host "       (zero survivors). Both research datasets are consumed forever."
        Write-Host "       Next: a round-3 prereg on genuinely fresh data - see the"
        Write-Host "       proposals in research/LEARNING_LOG.md."
    } elseif ($text -match "accepted data exceptions") {
        Write-Host "[....] Data accepted with signed-off exceptions; selection/holdout pending."
    } elseif ($text -match "fallback data plan activated") {
        Write-Host "[....] Fallback dataset in force; integrity/selection pending."
    } else {
        Write-Host "[....] Prereg locked; data fetch not yet recorded in the amendments log."
    }
    Write-Host ""
    Write-Host "amendments log (authoritative trail):"
    $inLog = $false
    foreach ($line in (Get-Content $prereg -Encoding UTF8)) {
        if ($line -match "^## Amendments log") { $inLog = $true; continue }
        if ($inLog -and $line -match "^\*\*") { Write-Host "  - $($line -replace '\*\*','')" }
    }
}

Write-Host ""
Write-Host "=== bootstrap done - nothing was traded, no orders exist ===" -ForegroundColor Cyan
