# e-Andon — AIアンドン：工場ライン見える化 × AI自律原因推定

[![ci](https://github.com/ai-club-lab/e-andon/actions/workflows/ci.yml/badge.svg)](https://github.com/ai-club-lab/e-andon/actions/workflows/ci.yml)

**稼働URL: https://chokotei-dashboard-523085315022.asia-northeast1.run.app** （Cloud Run / min-instances=0）

生産ラインの「**チョコ停**」（部品の位置ずれ等で起きる微小な短時間停止）を、**カメラ映像**で検知し、
**AIエージェント**が原因を自律推定してチャットで通知、人が確認・訂正して次回に活かす —— **1本のHITL閉ループ**。

- **画像で検知（主役）**: 俯瞰カメラ映像から、部品の **整列ズレ(px) / 角度ズレ(°) / ピッチ偏差** を
  決定論CV（OpenCV）で毎フレーム算出。位置決め機構はセンサー非搭載＝**映像でしか捉えられない**。
- **AIが真因を推定**: 整列異常を検知すると、ADK/Gemini エージェントが機械センサー
  （ベルト速度・モータ電流・振動・温度・エア圧）を照会し、**すべて正常＝過負荷や噛み込みではない**と除外して、
  上流の**機械的な位置決め機構**を真因として提示（確信度・根拠つき）。
- **工場まるごと監視**: サイドバーで複数ラインを管理。組立ライン1は実映像＋AI原因推定、他ラインはセンサーログ表示。
- **HITL**: 通知カードで人が「正しい/違う」を確認・訂正 → 過去事例に蓄積し、次回の few-shot 推論へ還元。

## デモの流れ

部品が流れる → 整列ズレを検知（黄・監視中）→ ライン停止（チョコ停）→ **AIエージェントが原因を通知** →
停止画面で保持（「▶ もう一度再生」で最初から）。

## アーキテクチャ

```
services/
  detector/   # 決定論CV検知（整列/角度/ピッチ）＋二段確認＋時系列集約
  agent/      # ADK RCAエージェント（AgentTool・DatabaseSessionService）＋合成センサー
  dashboard/  # FastAPI＋軽量フロント（複数ライン監視・SSE配信・チャット・HITL）
packages/shared/  # 型付き契約(pydantic)・設定注入
infra/            # Cloud SQL スキーマ・WIFセットアップ
video/            # 素材（factory_01.mov）
.github/workflows/ # CI（テスト＋Dockerビルド）／CD（キーレスWIFデプロイ）
```

- **実行**: Cloud Run（min-instances=0） / **AI**: Vertex AI Gemini 2.5 Flash（ADC・鍵不要, us-central1）
- **状態**: Cloud SQL Postgres + pgvector（RCA・セッション永続化） / **エージェント**: google-adk==2.3.0
- **リージョン**: モデル=us-central1 / 実行基盤=asia-northeast1
- 仕様: [.kiro/specs/chokotei-anomaly-rca/](.kiro/specs/chokotei-anomaly-rca/)（requirements / design / tasks / research）

## CI/CD

- **CI** ([.github/workflows/ci.yml](.github/workflows/ci.yml)): 毎 push / PR で、決定論CVテスト・コンパイル健全性・
  Cloud Run と同一イメージの Docker ビルドを実行（GCP認証不要）。
- **CD** ([.github/workflows/deploy.yml](.github/workflows/deploy.yml)): **キーレス（Workload Identity Federation）**で
  `main` → Cloud Run 自動デプロイ。長期SAキーは発行しない。
- **有効化**: プロジェクト管理者が [infra/setup-wif.sh](infra/setup-wif.sh) を1回実行（WIFプール作成＋リポジトリ変数設定）。
  未実行の間はデプロイジョブはスキップされ、pushが失敗しない設計。

## ローカル開発

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e packages/shared
pip install -r services/detector/requirements.txt   # 必要なサービスごと
# オフラインテスト（動画パスはリポジトリ直下基準）
PYTHONPATH=services/detector python -m pytest -q services/detector/test_detection.py services/detector/test_guardrails.py
```

## ガードレール（公開repo前提）

- APIキー/SAキー/`.env` は公開repoに入れない（**Secret Manager / ADC / WIF**）。
- 重い意思決定（異常確定）は**決定論CV**に置き、LLMの外＋監査ログに（HITL）。
- `gcloud projects delete` / `run services delete` は打たない（撤収は owner 判断）。
