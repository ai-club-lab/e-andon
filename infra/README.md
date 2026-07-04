# infra

Cloud Run / Cloud SQL の配線メモとデプロイ用資材（Dockerfile・設定）を置く。

## P1 デプロイ方針

- CI/WIF は提出repo確定後に owner が設定（それまで組まない）。
- P1 は各サービスをローカルから `gcloud run deploy --source .` で公開:

```bash
gcloud run deploy <svc> --source . --region asia-northeast1 --allow-unauthenticated \
  --min-instances=0 --add-cloudsql-instances <CONNECTION_NAME>
```

## Cloud SQL（稼働中）

- インスタンス: `chokotei-db`（POSTGRES_16 / db-f1-micro / asia-northeast1 / HDD 10GB / no-backup）
- 接続名: `fhack26-aiclub:asia-northeast1:chokotei-db`
- DB: `chokotei`（pgvector 有効・schema.sql 適用済み）
- パスワード: Secret Manager `chokotei-db-password`（**repoには絶対置かない**）
- ADK 接続: `postgresql+asyncpg://postgres:<pw>@/chokotei?host=/cloudsql/<接続名>`（Cloud Run, unix socket）
  ／ ローカルは Cloud SQL Auth Proxy 経由 `@127.0.0.1:5432`

### コスト管理（常駐課金 ~¥1,400/月・停止可）
```bash
# 使わない時は停止（課金ほぼ停止）
gcloud sql instances patch chokotei-db --activation-policy=NEVER
# 再開
gcloud sql instances patch chokotei-db --activation-policy=ALWAYS
```

### ローカル接続（Auth Proxy）
```bash
cloud-sql-proxy fhack26-aiclub:asia-northeast1:chokotei-db --port 5432 &
PW=$(gcloud secrets versions access latest --secret=chokotei-db-password)
PGPASSWORD="$PW" psql -h 127.0.0.1 -U postgres -d chokotei
```
