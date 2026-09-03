# Windows-native AgentDebugX capture launcher.
#
# Capture is a passive sensor: this script must never block a host session or
# surface an error, so every path exits 0 and no output reaches the host.

param(
    [string]$Platform = ''
)

$ErrorActionPreference = 'SilentlyContinue'

try {
    if ($Platform -ne 'claude' -and $Platform -ne 'codex') { exit 0 }

    $cli = $env:AGENTDEBUG_HOOK_CLI
    if ([string]::IsNullOrEmpty($cli)) {
        $found = Get-Command agentdebug -ErrorAction SilentlyContinue
        if ($null -eq $found) { exit 0 }
        $cli = $found.Source
    }
    if (-not (Test-Path -LiteralPath $cli)) { exit 0 }

    $payload = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrEmpty($payload)) { exit 0 }

    $precheck = Join-Path $PSScriptRoot 'precheck.py'
    if (Test-Path -LiteralPath $precheck) {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $python) {
            $python = Get-Command python3 -ErrorAction SilentlyContinue
        }
        if ($null -ne $python) {
            $payload | & $python.Source $precheck $Platform *> $null
            # Only a deliberate "skip" (1) suppresses dispatch; any other
            # status means the probe did not run, so defer to the CLI.
            if ($LASTEXITCODE -eq 1) { exit 0 }
        }
    }

    $payload | & $cli integrations capture dispatch --platform $Platform *> $null
} catch {
    exit 0
}

exit 0
