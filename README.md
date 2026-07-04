# chokotei-anomaly-rca

工場ラインの俯瞰映像から部品の整列異常（等間隔からの逸脱＝縦ズレ／回転）を**決定論CV**で検知し、
**ADKエージェント**が振動等のIoTログを相関して原因を推定、**ダッシュボードのチャット**で通知。
人が確認・訂正した内容を蓄積して次回の few-shot 改善に還す **HITL 閉ループ**。

- 仕様: [.kiro/specs/chokotei-anomaly-rca/](.kiro/specs/chokotei-anomaly-rca/)（requirements / design / tasks / research）
- 検知PoC: [docs/poc/findings.md](docs/poc/findings.md)
- GCP作法: [.claude/rules/gcp-integration.md](.claude/rules/gcp-integration.md)

## 構成

```
services/
  detector/   # CV二段検知＋時系列集約＋SSE配信
  agent/      # ADK RCAエージェント（AgentTool・DatabaseSessionService）
  dashboard/  # FastAPI＋軽量フロント（監視・チャット・HITL）
packages/shared/  # 型付き契約(pydantic)・設定注入
data/ / video/    # 素材（factory_01.mov）
infra/            # Dockerfile・Cloud Run 設定
```

## ローカル開発

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e packages/shared
pip install -r services/detector/requirements.txt   # 必要なサービスごと
```

## スタック

- 実行: Cloud Run（min-instances=0） / AI: Vertex AI Gemini 2.5 Flash（ADC・鍵不要, us-central1）
- 状態: Cloud SQL Postgres + pgvector / エージェント: google-adk==2.3.0（`postgresql+asyncpg://`）
- リージョン: モデル=us-central1 / 実行基盤=asia-northeast1

## ガードレール

APIキー/SAキー/`.env` は公開repoに入れない（Secret Manager / ADC）。
`gcloud projects delete` / `run services delete` は打たない（撤収は owner 判断）。
