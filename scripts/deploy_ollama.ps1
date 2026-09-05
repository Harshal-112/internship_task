<#
.SYNOPSIS
    Deploys and verifies Ollama and models for local LLM inference on Windows.
.DESCRIPTION
    Checks for Ollama installation, starts the service if needed,
    pulls the primary model (default: qwen2.5:1.5b) or fallback (gemma2:2b),
    and verifies readiness against http://localhost:11434/api/tags.
#>

[CmdletBinding()]
param (
    [string]$Model = "qwen2.5:1.5b",
    [string]$FallbackModel = "gemma2:2b",
    [string]$OllamaUrl = "http://localhost:11434",
    [int]$StartupWaitSeconds = 15
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   Ollama Local LLM Deployment Script (Windows)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Target Primary Model : $Model"
Write-Host "Fallback Model       : $FallbackModel"
Write-Host "Ollama Service URL   : $OllamaUrl"
Write-Host ""

# 1. Check if Ollama executable exists
Write-Host "[1/4] Checking Ollama installation..." -ForegroundColor Yellow
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue

if (-not $ollamaCmd) {
    Write-Host "[!] Ollama is not installed or not in PATH." -ForegroundColor Red
    Write-Host ""
    Write-Host "To install Ollama automatically via Windows Package Manager (winget):" -ForegroundColor Cyan
    Write-Host "    winget install Ollama.Ollama" -ForegroundColor Green
    Write-Host ""
    Write-Host "Or download and run the installer directly from:" -ForegroundColor Cyan
    Write-Host "    https://ollama.com/download/windows" -ForegroundColor Green
    Write-Host ""
    
    $installChoice = Read-Host "Would you like to run 'winget install Ollama.Ollama' now? (y/N)"
    if ($installChoice -match '^[yY]$') {
        Write-Host "Running: winget install Ollama.Ollama --accept-source-agreements --accept-package-agreements" -ForegroundColor Cyan
        try {
            & winget install Ollama.Ollama --accept-source-agreements --accept-package-agreements
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
            $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
        } catch {
            Write-Host "[x] Automated installation encountered an issue: $_" -ForegroundColor Red
        }
    }

    if (-not $ollamaCmd) {
        Write-Warning "Please install Ollama, ensure it is added to your PATH, and re-run this script."
        exit 1
    }
}

Write-Host "[+] Ollama binary detected: $($ollamaCmd.Source)" -ForegroundColor Green

# 2. Check if Ollama service is running
Write-Host "`n[2/4] Checking Ollama service connectivity at $OllamaUrl..." -ForegroundColor Yellow
function Test-OllamaService {
    param([string]$Url)
    try {
        $response = Invoke-RestMethod -Uri "$Url/api/tags" -Method Get -TimeoutSec 3 -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

$serviceRunning = Test-OllamaService -Url $OllamaUrl
if (-not $serviceRunning) {
    Write-Host "[!] Ollama service is not running. Attempting to start background service..." -ForegroundColor Yellow
    try {
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
        Write-Host "Waiting up to $StartupWaitSeconds seconds for service to initialize..." -ForegroundColor Gray
        $elapsed = 0
        while ($elapsed -lt $StartupWaitSeconds) {
            Start-Sleep -Seconds 2
            $elapsed += 2
            if (Test-OllamaService -Url $OllamaUrl) {
                $serviceRunning = $true
                break
            }
        }
    } catch {
        Write-Warning "Could not start 'ollama serve' automatically: $_"
    }
}

if (-not $serviceRunning) {
    Write-Host "[x] Ollama service could not be reached at $OllamaUrl." -ForegroundColor Red
    Write-Host "Please start the Ollama application or run 'ollama serve' in a separate terminal." -ForegroundColor Yellow
    exit 1
}

Write-Host "[+] Ollama service is active and responsive." -ForegroundColor Green

# 3. Pull primary model or fallback
Write-Host "`n[3/4] Pulling model: $Model..." -ForegroundColor Yellow
$pulledModel = $null

try {
    Write-Host "Executing: ollama pull $Model" -ForegroundColor Cyan
    & ollama pull $Model
    if ($LASTEXITCODE -eq 0) {
        $pulledModel = $Model
        Write-Host "[+] Successfully pulled $Model" -ForegroundColor Green
    } else {
        throw "ollama pull $Model exited with code $LASTEXITCODE"
    }
} catch {
    Write-Warning "[!] Failed to pull primary model ($Model): $_"
    Write-Host "Attempting fallback to model: $FallbackModel..." -ForegroundColor Yellow
    try {
        & ollama pull $FallbackModel
        if ($LASTEXITCODE -eq 0) {
            $pulledModel = $FallbackModel
            Write-Host "[+] Successfully pulled fallback model $FallbackModel" -ForegroundColor Green
        } else {
            throw "ollama pull $FallbackModel exited with code $LASTEXITCODE"
        }
    } catch {
        Write-Host "[x] Failed to pull fallback model ($FallbackModel): $_" -ForegroundColor Red
        exit 1
    }
}

# 4. Verify model readiness via /api/tags
Write-Host "`n[4/4] Verifying model readiness via $OllamaUrl/api/tags..." -ForegroundColor Yellow
try {
    $tags = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -Method Get -TimeoutSec 5
    $availableModels = $tags.models | ForEach-Object { $_.name }
    Write-Host "Available models on host:" -ForegroundColor Gray
    $availableModels | ForEach-Object { Write-Host " - $_" -ForegroundColor Gray }

    $match = $availableModels | Where-Object { $_ -like "*$pulledModel*" }
    if ($match) {
        Write-Host ""
        Write-Host "==========================================================" -ForegroundColor Green
        Write-Host " [SUCCESS] Model '$pulledModel' is ready for inference!" -ForegroundColor Green
        Write-Host " Endpoint: $OllamaUrl" -ForegroundColor Green
        Write-Host "==========================================================" -ForegroundColor Green
    } else {
        Write-Warning "Model '$pulledModel' was not found in /api/tags response, but pull succeeded."
    }
} catch {
    Write-Host "[x] Error checking /api/tags: $_" -ForegroundColor Red
    exit 1
}
