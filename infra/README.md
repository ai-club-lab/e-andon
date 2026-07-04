# infra

Cloud Run / Cloud SQL の配線メモとデプロイ用資材（Dockerfile・設定）を置く。

## P1 デプロイ方針

- CI/WIF は提出repo確定後に owner が設定（それまで組まない）。
- P1 は各サービスをローカルから `gcloud run deploy --source .` で公開:

```bash
gcloud run deploy <svc> --source . --region asia-northeast1 --allow-unauthenticated \
  --min-instances=0 --add-cloudsql-instances <CONNECTION_NAME>
```

## Cloud SQL（未作成・課金ゲート）

- Postgres + pgvector。**scale-to-zero ではなく常駐課金**するため作成は owner/予算合意のうえで。
- 作成後: SA に `roles/cloudsql.client`、接続は `postgresql+asyncpg://`（ADK 2.x 必須）。
