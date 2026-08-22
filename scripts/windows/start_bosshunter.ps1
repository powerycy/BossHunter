[CmdletBinding()]
param(
	[switch]$SkipChrome
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Bosshunter = Get-Command "bosshunter" -ErrorAction SilentlyContinue
if ($Bosshunter) {
	$Runner = $Bosshunter.Source
	$RunnerPrefix = @()
} else {
	$Python = Get-Command "py" -ErrorAction SilentlyContinue
	if (-not $Python) {
		$Python = Get-Command "python" -ErrorAction SilentlyContinue
	}
	if (-not $Python) {
		throw "Could not find BossHunter or Python. Install the project first with: pip install -e ."
	}
	$Runner = $Python.Source
	$RunnerPrefix = @("-m", "bosshunter.main")
}

$ChromeCandidates = @()
if ($env:ProgramFiles) {
	$ChromeCandidates += Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"
}
if (${env:ProgramFiles(x86)}) {
	$ChromeCandidates += Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"
}
if ($env:LOCALAPPDATA) {
	$ChromeCandidates += Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe"
}
$ChromeCandidates = $ChromeCandidates | Where-Object { Test-Path -LiteralPath $_ }

if (-not $ChromeCandidates) {
	throw "Could not find Google Chrome. Install Chrome or set up a compatible browser manually."
}

$Chrome = $ChromeCandidates | Select-Object -First 1
$ChromeProfile = Join-Path $env:LOCALAPPDATA "BossHunterChrome"
$ChromeArguments = @(
	"--remote-debugging-port=9222",
	"--user-data-dir=$ChromeProfile",
	"https://www.zhipin.com"
)

if (-not $SkipChrome) {
	Write-Host "Starting the BossHunter Chrome profile..."
	Start-Process -FilePath $Chrome -ArgumentList $ChromeArguments
	$ChromeReady = $false
	for ($i = 0; $i -lt 20; $i++) {
		Start-Sleep -Milliseconds 500
		try {
			$null = Invoke-RestMethod -Uri "http://127.0.0.1:9222/json/version" -TimeoutSec 2
			$ChromeReady = $true
			break
		} catch {
			# Chrome is still starting.
		}
	}
	if (-not $ChromeReady) {
		Write-Warning "Chrome remote debugging did not become ready within 10 seconds."
	}
}

Write-Host "Starting the Browser Runtime..."
& $Runner @RunnerPrefix "connect"
if ($LASTEXITCODE -ne 0) {
	Write-Warning "Browser connection check returned exit code $LASTEXITCODE. The workbench will still be opened."
}

Write-Host "Starting the local workbench..."
$WebArguments = @($RunnerPrefix) + @("web", "--no-open")
Start-Process -FilePath $Runner -ArgumentList $WebArguments -WorkingDirectory $RepoRoot -WindowStyle Hidden

for ($i = 0; $i -lt 20; $i++) {
	Start-Sleep -Milliseconds 500
	try {
		$null = Invoke-WebRequest -Uri "http://127.0.0.1:8686/" -UseBasicParsing -TimeoutSec 2
		break
	} catch {
		# The local server is still starting.
	}
}

if (-not $SkipChrome) {
	Start-Process -FilePath $Chrome -ArgumentList @(
		"--remote-debugging-port=9222",
		"--user-data-dir=$ChromeProfile",
		"http://127.0.0.1:8686"
	)
}

Write-Host "BossHunter is ready. Log in manually in the dedicated Chrome window if needed."
