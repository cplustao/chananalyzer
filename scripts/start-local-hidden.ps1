param(
    [switch]$NoBrowser,
    [switch]$NoPopup
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $projectRoot 'logs'
$startupMutex = $null
$ownsMutex = $false

function Show-StartupError {
    param([string]$Message)

    try {
        $shell = New-Object -ComObject WScript.Shell
        $null = $shell.Popup($Message, 0, 'ChanAnalyzer startup failed', 16)
    } catch {
        # The detailed error is also written to the launcher log below.
    }
}

function Test-ApiReady {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200 -and $response.Content -match '"status"\s*:\s*"ready"'
    } catch {
        return $false
    }
}

try {
    $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $frontend = Join-Path $projectRoot 'frontend\dist\index.html'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python virtual environment not found: $python`nFollow the local setup steps in README.md."
    }
    if (-not (Test-Path -LiteralPath $frontend -PathType Leaf)) {
        throw "Frontend build not found: $frontend`nRun npm install and npm run build in frontend first."
    }

    $portText = & $python -c 'from backend.app.core.config import get_settings; print(get_settings().api_port)' 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read API_PORT: $($portText -join ' ')"
    }
    $port = [int]($portText | Select-Object -Last 1)
    $url = "http://127.0.0.1:$port/"
    $readyUrl = "${url}health/ready"

    $startupMutex = New-Object System.Threading.Mutex($false, 'Local\ChanAnalyzerLocalStartup')
    $ownsMutex = $startupMutex.WaitOne([TimeSpan]::FromSeconds(60))
    if (-not $ownsMutex) {
        throw 'Another ChanAnalyzer startup is still in progress.'
    }

    if (-not (Test-ApiReady $readyUrl)) {
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $outputLog = Join-Path $logDirectory 'local-launcher.out.log'
        $errorLog = Join-Path $logDirectory 'local-launcher.err.log'
        $process = Start-Process -FilePath $python `
            -ArgumentList '-m', 'backend.app.start_local' `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $outputLog `
            -RedirectStandardError $errorLog `
            -PassThru

        $ready = $false
        for ($attempt = 0; $attempt -lt 45; $attempt++) {
            Start-Sleep -Seconds 1
            $process.Refresh()
            if ($process.HasExited) {
                throw "Service exited early (code $($process.ExitCode)). See $errorLog and $outputLog."
            }
            if (Test-ApiReady $readyUrl) {
                $ready = $true
                break
            }
        }
        if (-not $ready) {
            throw "Timed out waiting for the service. See $errorLog and $outputLog."
        }
    }

    if (-not $NoBrowser) {
        Start-Process $url
    }
} catch {
    try {
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        Add-Content -LiteralPath (Join-Path $logDirectory 'local-launcher.error.log') `
            -Value "$(Get-Date -Format o) $($_.Exception.Message)"
    } catch {
        # A visible error is still shown when log writing fails.
    }
    if (-not $NoPopup) {
        Show-StartupError "Startup failed: $($_.Exception.Message)"
    }
    exit 1
} finally {
    if ($ownsMutex) {
        $startupMutex.ReleaseMutex()
    }
    if ($null -ne $startupMutex) {
        $startupMutex.Dispose()
    }
}
