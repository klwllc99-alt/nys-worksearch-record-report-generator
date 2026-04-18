# Production deployment guide

This project is prepared to run behind Cloudflare and on Google Cloud Run.

## Production artifacts

### Google Cloud
- Cloud Run service manifest: [cloud-run-service.yaml](cloud-run-service.yaml)
- App container build: [backend/Dockerfile](backend/Dockerfile)
- Firestore collection for retained metrics: `nys_ws5_metrics_daily`
- Secret Manager secrets:
  - `nys-ws5-admin-email`
  - `nys-ws5-admin-password`
- Service account for Cloud Run with:
  - `roles/datastore.user`
  - `roles/secretmanager.secretAccessor`

### Cloudflare
- Proxied DNS record for the production domain
- SSL/TLS in `Full (strict)` mode
- Access policy protecting `/admin/*`
- Optional WAF and rate-limit rules for `/api/*`

## Suggested production flow

1. Create or choose a GCP project.
2. Enable these services:
   - Cloud Run
   - Artifact Registry
   - Firestore
   - Secret Manager
   - Cloud Build
3. Create the two admin secrets in Secret Manager.
4. Build and deploy the container with [deploy-cloud-run.ps1](deploy-cloud-run.ps1).
5. Point the Cloudflare DNS record at the Cloud Run URL.
6. Add a Cloudflare Access app for the admin routes.

## Recommended Cloudflare setup

- Public hostname: `app.your-domain.com`
- Admin protection: `https://app.your-domain.com/admin*`
- Cache: disabled for dynamic API routes
- Rate limits: enabled for repeated POST requests to `/api/generate`

## Notes

- Local development continues to use the local rolling metrics store by default.
- Cloud Run production is configured to use Firestore for bounded daily metrics retention.
- Update the placeholders in [cloud-run-service.yaml](cloud-run-service.yaml) before first deploy.
