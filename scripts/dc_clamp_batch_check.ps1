# CLAMP local batch check - Windows PowerShell 5.1 compatible
# Read-only. Does not fetch, modify, commit, push, or change system settings.
Clear-Host
$ErrorActionPreference = 'Continue'
$repo = 'D:\AI_Project\wosafety0clamp-static-extractor-20261029'
$integration = 'D:\AI_Project\KancolleBot-Worktrees\poi-safety-consolidation-20261029'
Write-Host '=== CLAMP DC BATCH CHECK ==='
Write-Host ('Repository path: ' + $repo)
if (Test-Path -LiteralPath (Join-Path $repo '.git')) {
  Write-Host '--- FORMAL WORKTREE ---'
  git -C $repo branch --show-current
  git -C $repo rev-parse HEAD
  git -C $repo status --short --branch
  git -C $repo remote -v
} else { Write-Host 'Formal worktree: NOT_FOUND' }
if (Test-Path -LiteralPath (Join-Path $integration '.git')) {
  Write-Host '--- INTEGRATION WORKTREE ---'
  git -C $integration branch --show-current
  git -C $integration rev-parse HEAD
  git -C $integration status --short --branch
} else { Write-Host 'Integration worktree: NOT_FOUND' }
Write-Host '--- KNOWN PROJECT PATHS ---'
foreach ($p in @(
  (Join-Path $repo 'docs\PROJECT_kancollebot_Progress.md'),
  (Join-Path $repo 'docs\MVP_EXPEDITION_SINGLE_LOOP.md'),
  (Join-Path $repo 'scripts\dc_clamp_batch_check.ps1'),
  (Join-Path $repo 'tests'),
  (Join-Path $repo 'tools')
)) {
  Write-Host ((Test-Path -LiteralPath $p).ToString().ToUpper() + ' ' + $p)
}
Write-Host '--- WORKTREE MAP ---'
git -C $repo worktree list --porcelain
Write-Host '=== END ===' -ForegroundColor Magenta
