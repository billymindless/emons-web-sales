param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("pull", "push", "sync")]
  [string]$Action
)

$RepoRoot = Split-Path (Split-Path $MyInvocation.MyCommand.Path -Parent) -Parent
$DocsPlans = Join-Path $RepoRoot "docs\plans"
$CursorPlans = Join-Path $env:USERPROFILE ".cursor\plans"

function Ensure-Dirs {
  New-Item -ItemType Directory -Force -Path $DocsPlans | Out-Null
  New-Item -ItemType Directory -Force -Path $CursorPlans | Out-Null
}

function Pull-Plans {
  Ensure-Dirs
  $copied = 0
  Get-ChildItem -Path $DocsPlans -Filter "*.plan.md" -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination (Join-Path $CursorPlans $_.Name) -Force
    $copied++
  }
  Get-ChildItem -Path $CursorPlans -Filter "*.plan.md" -ErrorAction SilentlyContinue | ForEach-Object {
    $target = Join-Path $DocsPlans $_.Name
    if (-not (Test-Path $target)) {
      Remove-Item -Path $_.FullName -Force
    }
  }
  Write-Host "[sync-plans] pull complete ($copied file(s) -> $CursorPlans)"
}

function Push-Plans {
  Ensure-Dirs
  $copied = 0
  Get-ChildItem -Path $CursorPlans -Filter "*.plan.md" -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination (Join-Path $DocsPlans $_.Name) -Force
    $copied++
  }
  Write-Host "[sync-plans] copied $copied file(s) -> $DocsPlans"

  Push-Location $RepoRoot
  try {
    git add docs/plans
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
      Write-Host "[sync-plans] no plan changes to commit"
      return
    }
    git commit -m "docs: Cursor plans 동기화"
    git push
    Write-Host "[sync-plans] push complete"
  } finally {
    Pop-Location
  }
}

switch ($Action) {
  "pull" { Pull-Plans }
  "push" { Push-Plans }
  "sync" {
    Pull-Plans
    Push-Plans
  }
}
