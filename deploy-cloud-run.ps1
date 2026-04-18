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

function Get-GCloudPath {
    $command = Get-Command gcloud -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        'C:\Users\kw99f\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd',
        'C:\Program Files\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd',
        'C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd'
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw 'Google Cloud CLI was not found. Install gcloud or reopen the terminal after installation.'
}

$gcloud = Get-GCloudPath

if (-not $ServiceAccount) {
    $ServiceAccount = "$ServiceName-sa@$ProjectId.iam.gserviceaccount.com"
}

$image = "$Region-docker.pkg.dev/$ProjectId/$Repository/$ServiceName`:$ImageTag"
$renderedManifest = Join-Path $PSScriptRoot 'deployment/cloud-run.rendered.yaml'

New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'deployment') | Out-Null

Write-Host "Setting active project..."
& $gcloud config set project $ProjectId

Write-Host "Enabling required Google Cloud services..."
& $gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com secretmanager.googleapis.com --project $ProjectId

Write-Host "Ensuring Artifact Registry repository exists..."
& $gcloud artifacts repositories describe $Repository --location $Region --project $ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    & $gcloud artifacts repositories create $Repository --repository-format=docker --location $Region --description="Docker images for $ServiceName" --project $ProjectId
}

Write-Host "Configuring Docker auth for Artifact Registry..."
& $gcloud auth configure-docker "$Region-docker.pkg.dev" --quiet

Write-Host "Building and pushing container image: $image"
& $gcloud builds submit "$PSScriptRoot/backend" --tag $image --project $ProjectId

Write-Host "Rendering Cloud Run manifest..."
$manifest = Get-Content (Join-Path $PSScriptRoot 'cloud-run-service.yaml') -Raw
$manifest = $manifest.Replace('APP_IMAGE_PLACEHOLDER', $image)
$manifest = $manifest.Replace('PROJECT_ID_PLACEHOLDER', $ProjectId)
$manifest = $manifest.Replace('APP_DOMAIN_PLACEHOLDER', $AppDomain)
$manifest = $manifest.Replace('SERVICE_ACCOUNT_PLACEHOLDER', $ServiceAccount)
Set-Content -Path $renderedManifest -Value $manifest -Encoding UTF8

Write-Host "Deploying Cloud Run service..."
& $gcloud run services replace $renderedManifest --region $Region --project $ProjectId

Write-Host ''
Write-Host 'Deployment submitted successfully.'
Write-Host "Next step: point Cloudflare DNS for $AppDomain to the Cloud Run service URL and protect /admin* with Cloudflare Access."
