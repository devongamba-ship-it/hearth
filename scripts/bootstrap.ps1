# Hearth GitHub bootstrap — idempotent; re-run safely, it only does what's missing.
# Prereq: `gh auth login` completed (scopes: repo, workflow).
# NOTE (PS 5.1): native commands like gh/git NEVER throw into try/catch —
# always gate on $LASTEXITCODE.

$repoDir = Split-Path $PSScriptRoot -Parent
Set-Location $repoDir
$owner = gh api user --jq .login
if ($LASTEXITCODE -ne 0) { Write-Error "gh is not authenticated. Run 'gh auth login' first."; exit 1 }
$repo = "$owner/hearth"

# 1. Repo exists?
gh api "repos/$repo" --silent
if ($LASTEXITCODE -ne 0) {
    gh repo create hearth --private --description "Contactless WiFi-sensing fall detection. The house is the alert system."
    if ($LASTEXITCODE -ne 0) { Write-Error "repo create failed"; exit 1 }
}

# 2. Local repo + push (commit only when there are changes)
if (-not (Test-Path "$repoDir\.git")) { git init -b main }
if (git status --porcelain) {
    git add -A
    git -c user.name="Devon Gamba" -c user.email="devongamba@gmail.com" commit -m "Bootstrap updates"
}
if (-not (git remote | Select-String -Quiet "^origin$")) {
    git remote add origin "https://github.com/$repo.git"
}
git push -u origin main
if ($LASTEXITCODE -ne 0) { Write-Error "push failed"; exit 1 }

# 3. Branch protection (requires public repo or GitHub Pro on private)
$protection = '{"required_status_checks":null,"enforce_admins":false,"required_pull_request_reviews":{"required_approving_review_count":1},"restrictions":null,"allow_force_pushes":false,"allow_deletions":false}'
$protection | gh api -X PUT "repos/$repo/branches/main/protection" --input - | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Branch protection refused (private repo on free plan)."
    Write-Warning "  a) gh repo edit $repo --visibility public --accept-visibility-change-consequences"
    Write-Warning "  b) upgrade to GitHub Pro, then re-run this script"
}

# 4. Secrets — only prompt for ones not already set
$existing = gh secret list --repo $repo --json name --jq '.[].name'
foreach ($name in @("ANTHROPIC_API_KEY", "LINEAR_API_KEY")) {
    if ($existing -contains $name) { Write-Host "$name already set - skipping"; continue }
    $hint = if ($name -eq "ANTHROPIC_API_KEY") { "console.anthropic.com -> API keys" }
            else { "linear.app -> Settings -> Security & access -> API keys" }
    $sec = Read-Host "Paste $name ($hint)" -AsSecureString
    $val = [System.Net.NetworkCredential]::new("", $sec).Password
    gh secret set $name --repo $repo --body $val
    if ($LASTEXITCODE -ne 0) { Write-Error "failed to set $name"; exit 1 }
}

# 5. First agent run (skipped if any run already exists)
$runs = gh run list --workflow=watchtower-agent.yml --repo $repo --limit 1 --json databaseId --jq length
if ($runs -eq "0") {
    gh workflow run watchtower-agent.yml --repo $repo -f issue_id=BAT-20
    if ($LASTEXITCODE -ne 0) { Write-Error "dispatch failed"; exit 1 }
    Write-Host "`nDispatched BAT-20. Watch with: gh run watch --repo $repo" -ForegroundColor Green
} else {
    Write-Host "Workflow has prior runs - not auto-dispatching. Manual: gh workflow run watchtower-agent.yml --repo $repo -f issue_id=BAT-XX"
}
Write-Host "Cron is live (every 6h). Kill switch: gh workflow disable watchtower-agent.yml --repo $repo"
