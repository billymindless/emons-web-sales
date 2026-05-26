# sessionStart: git pull 후 docs/plans -> ~/.cursor/plans
$ErrorActionPreference = 'SilentlyContinue'

$RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $RepoRoot

git pull --rebase --autostash 2>$null
& "$RepoRoot\scripts\sync-plans.ps1" -Action pull
