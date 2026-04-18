param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$Region = 'us-central1',
    [string]$ServiceName = 'nys-ws5-generator',
    [string]$Repository = 'nys-ws5-generator',
    [string]$ImageTag = 'prod',
    [string]$AppDomain = 'app.example.com',
    [string]$ServiceAccount = ''
)

$ErrorActionPreference = 'Stop'

if (-not $ServiceAccount) {
    $ServiceAccount = "$ServiceName-sa@$ProjectId.iam.gserviceaccount.com"
}

$image = "$Region-docker.pkg.dev/$ProjectId/$Repository/$ServiceName`:$ImageTag"
$renderedManifest = Join-Path $PSScriptRoot 'deployment/cloud-run.rendered.yaml'

New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'deployment') | Out-Null

Write-Host "Configuring Docker auth for Artifact Registry..."
gcloud auth configure-docker "$Region-docker.pkg.dev" --quiet

Write-Host "Building and pushing container image: $image"
gcloud builds submit "$PSScriptRoot/backend" --tag $image --project $ProjectId

Write-Host "Rendering Cloud Run manifest..."
(Get-Content (Join-Path $PSScriptRoot 'cloud-run-service.yaml') -Raw) `
    .Replace('APP_IMAGE_PLACEHOLDER', $image) `
    .Replace('PROJECT_ID_PLACEHOLDER', $ProjectId) `
    .Replace('APP_DOMAIN_PLACEHOLDER', $AppDomain) `
    .Replace('SERVICE_ACCOUNT_PLACEHOLDER', $ServiceAccount) |
    Set-Content -Path $renderedManifest -Encoding UTF8

Write-Host "Deploying Cloud Run service..."
gcloud run services replace $renderedManifest --region $Region --project $ProjectId

Write-Host ''
Write-Host 'Deployment submitted successfully.'
Write-Host "Next step: point Cloudflare DNS for $AppDomain to the Cloud Run service URL and protect /admin* with Cloudflare Access."
