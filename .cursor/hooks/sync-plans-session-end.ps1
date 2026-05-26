# sessionEnd: ~/.cursor/plans -> docs/plans (+ commit/push if changed)
$ErrorActionPreference = 'SilentlyContinue'

$RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $RepoRoot

& "$RepoRoot\scripts\sync-plans.ps1" -Action push
