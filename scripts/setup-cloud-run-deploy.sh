#!/usr/bin/env bash
# 一次性設定：讓 GitHub Actions（.github/workflows/cloud-run.yml）能部署 Cloud Run，不需要金鑰檔。
# 在 Google Cloud Shell 執行（已登入、已選好專案）：
#
#   git clone https://github.com/wudn9922/my-stock-screener.git && cd my-stock-screener
#   bash scripts/setup-cloud-run-deploy.sh                       # 先列出現有的 Cloud Run 服務
#   REGION=asia-east1 BREAKOUT_SERVICE=<突破監控服務名稱> DISCORD_SERVICE=<Discord 查詢服務名稱，可省略> \
#     bash scripts/setup-cloud-run-deploy.sh
#
# 會做的事（重複執行是安全的）：
#   1. 啟用需要的 API
#   2. 建立部署用服務帳號 github-deployer，授予 Cloud Run 部署、Artifact Registry 寫入，
#      以及「以服務的執行身分部署」的權限
#   3. 建立 Workload Identity Pool／Provider，只信任 GitHub repo wudn9922/my-stock-screener
#   4. 印出要貼到 GitHub repository variables 的值
set -euo pipefail

REPO="wudn9922/my-stock-screener"
POOL="github"
PROVIDER="my-stock-screener"
DEPLOY_SA_NAME="github-deployer"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
if [ -z "${PROJECT_ID}" ]; then
  echo "找不到專案，請先執行：gcloud config set project <專案 ID>" >&2
  exit 1
fi
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')

if [ -z "${REGION:-}" ] || [ -z "${BREAKOUT_SERVICE:-}" ]; then
  echo "專案 ${PROJECT_ID} 的 Cloud Run 服務："
  gcloud run services list --project "${PROJECT_ID}" \
    --format='table(metadata.name:label=服務名稱,region:label=區域,spec.template.spec.containers[0].image:label=映像)'
  echo
  echo "找出突破監控（以及 Discord 查詢）的服務名稱與區域後，再執行："
  echo "  REGION=<區域> BREAKOUT_SERVICE=<服務名稱> DISCORD_SERVICE=<服務名稱或省略> bash $0"
  exit 0
fi

describe() {
  gcloud run services describe "$1" --project "${PROJECT_ID}" --region "${REGION}" --format="value($2)"
}

image_without_tag() {
  local image="${1%@*}"
  local last="${image##*/}"
  if [[ "${last}" == *:* ]]; then
    image="${image%:*}"
  fi
  echo "${image}"
}

echo "== 1. 啟用 API"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com --project "${PROJECT_ID}"

DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
echo "== 2. 部署用服務帳號 ${DEPLOY_SA}"
if ! gcloud iam service-accounts describe "${DEPLOY_SA}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${DEPLOY_SA_NAME}" --project "${PROJECT_ID}" \
    --display-name "GitHub Actions deployer (${REPO})"
fi
for role in roles/run.developer roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" --condition=None --quiet \
    --member "serviceAccount:${DEPLOY_SA}" --role "${role}" >/dev/null
done

declare -A IMAGES=()
for service in "${BREAKOUT_SERVICE}" ${DISCORD_SERVICE:+"${DISCORD_SERVICE}"}; do
  image=$(describe "${service}" 'spec.template.spec.containers[0].image')
  runtime_sa=$(describe "${service}" 'spec.template.spec.serviceAccountName')
  runtime_sa="${runtime_sa:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"
  IMAGES["${service}"]=$(image_without_tag "${image}")
  echo "   ${service}：映像 ${IMAGES[${service}]}，執行身分 ${runtime_sa}"
  gcloud iam service-accounts add-iam-policy-binding "${runtime_sa}" --project "${PROJECT_ID}" --quiet \
    --member "serviceAccount:${DEPLOY_SA}" --role roles/iam.serviceAccountUser >/dev/null
done

echo "== 3. Workload Identity（只信任 ${REPO}）"
if ! gcloud iam workload-identity-pools describe "${POOL}" --project "${PROJECT_ID}" --location global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${POOL}" --project "${PROJECT_ID}" --location global \
    --display-name "GitHub Actions"
fi
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER}" --project "${PROJECT_ID}" \
    --location global --workload-identity-pool "${POOL}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER}" --project "${PROJECT_ID}" \
    --location global --workload-identity-pool "${POOL}" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository == '${REPO}' && assertion.ref == 'refs/heads/main'"
fi
gcloud iam service-accounts add-iam-policy-binding "${DEPLOY_SA}" --project "${PROJECT_ID}" --quiet \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" >/dev/null

WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"

echo
echo "== 4. 完成。到 https://github.com/${REPO}/settings/variables/actions 新增以下 Variables（不是 Secrets）："
echo
printf '  %-28s %s\n' GCP_WIF_PROVIDER "${WIF_PROVIDER}"
printf '  %-28s %s\n' GCP_DEPLOY_SA "${DEPLOY_SA}"
printf '  %-28s %s\n' GCP_PROJECT_ID "${PROJECT_ID}"
printf '  %-28s %s\n' GCP_REGION "${REGION}"
printf '  %-28s %s\n' CLOUD_RUN_BREAKOUT_SERVICE "${BREAKOUT_SERVICE}"
printf '  %-28s %s\n' CLOUD_RUN_BREAKOUT_IMAGE "${IMAGES[${BREAKOUT_SERVICE}]}"
if [ -n "${DISCORD_SERVICE:-}" ]; then
  printf '  %-28s %s\n' CLOUD_RUN_DISCORD_SERVICE "${DISCORD_SERVICE}"
  printf '  %-28s %s\n' CLOUD_RUN_DISCORD_IMAGE "${IMAGES[${DISCORD_SERVICE}]}"
fi
echo
echo "之後到 Actions → Deploy Cloud Run (breakout alert) → Run workflow 手動部署一次確認。"
