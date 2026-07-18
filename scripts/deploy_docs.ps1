<#
.SYNOPSIS
  Build the oryxflow docs and publish ./site to Cloudflare Pages (Windows / local).

.DESCRIPTION
  Windows-native mirror of scripts/deploy_docs.sh. Runs the doc-example tests, builds
  the MkDocs site, then deploys ./site to Cloudflare Pages with wrangler direct-upload.

  Prerequisites:
    pip install -e .
    pip install -r docs/requirements-docs.txt
    node/npx on PATH (wrangler is fetched on demand via npx)
    $env:CLOUDFLARE_API_TOKEN  = "<token with Cloudflare Pages: Edit>"
    $env:CLOUDFLARE_ACCOUNT_ID = "<account id>"

.EXAMPLE
  ./scripts/deploy_docs.ps1
  ./scripts/deploy_docs.ps1 -BuildOnly
  ./scripts/deploy_docs.ps1 -SkipTests
#>
param(
  [switch]$BuildOnly,
  [switch]$SkipTests,
  [string]$Project = $(if ($env:CF_PAGES_PROJECT) { $env:CF_PAGES_PROJECT } else { "oryxflow-docs" }),
  [string]$Branch  = $(if ($env:CF_BRANCH) { $env:CF_BRANCH } else { "main" })
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$buildArgs = @()
if ($SkipTests) { $buildArgs += "--skip-tests" }

Write-Host "==> Building docs" -ForegroundColor Cyan
python scripts/build_docs.py @buildArgs
if ($LASTEXITCODE -ne 0) { throw "build_docs.py failed" }

if ($BuildOnly) {
  Write-Host "==> -BuildOnly set — skipping Cloudflare deploy. Site is in ./site" -ForegroundColor Yellow
  exit 0
}

Write-Host "==> Deploying ./site to Cloudflare Pages project '$Project' (branch '$Branch')" -ForegroundColor Cyan
npx --yes wrangler@3 pages deploy site --project-name $Project --branch $Branch --commit-dirty=true
if ($LASTEXITCODE -ne 0) { throw "wrangler deploy failed" }
