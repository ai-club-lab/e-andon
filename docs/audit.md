# Audit & Guardrail Compliance (Req 10)

ハッカソン ガードレール（`.claude/rules/gcp-integration.md`）への適合を明文化する。

## 重い意思決定は LLM の外（Req 10.4）

- **異常の確定は決定論CV**（`services/detector/detection.py`）。重心・回転角・間隔の閾値判定で、
  同一入力→同一出力（`test_guardrails.py` で検証）。LLM は判定に関与しない。
- Gemini は **原因推論の説明** と **境界帯のみの二段確認**に限定（判定の主導権を持たない）。
- 監査証跡: `anomaly_events`（検知の事実）＋ `rca_results.evidence`（推論が参照した数値）＋
  `feedback`（人手判定）を Cloud SQL に永続。誰が・いつ・何を根拠に、が追える。

## Human-in-the-Loop（Req 8）

- AI 推定は必ず人の確認/訂正を経る。訂正は `feedback` に記録され、`past_cases` に還流して
  次回 few-shot に反映（学習ループ）。無音フォールバックはしない（`rca_agent` は失敗を例外＋ログ化）。

## セキュリティ衛生

- **鍵レス**: Vertex は ADC、CI は WIF（提出repo確定後）。長期SAキーは発行しない。
- **秘密の非混入**: DBパスワードは Secret Manager（`chokotei-db-password`）。`.gitignore` で `.env`/鍵/
  credentials を除外。公開バケットに秘密を置かない（フレームは `public-access-prevention=enforced` の
  非公開バケット `fhack26-aiclub-frames`、`/frame` プロキシ経由でのみ配信）。

## リージョン（Req 10.6）

- モデル呼び出し = **us-central1**（新モデルが早い）／ 実行基盤・Cloud SQL・AR・GCS = **asia-northeast1**。
- `test_guardrails.py` で `GCP.model_region` / `GCP.runtime_region` を検証。

## コスト（Req 10.2）

- Cloud Run は **min-instances=0（scale-to-zero）**。Cloud SQL は常駐課金のため停止手順を
  `infra/README.md` に明記（`activation-policy=NEVER`）。予算 ≤¥10,000/月・無料枠優先。

## 既知の限界

- `event_id` は インスタンス毎の連番のため複数インスタンス跨ぎで衝突しうる（単一インスタンスのデモでは問題なし）。
  本番運用ではインスタンス/時刻成分を付与して大域一意化する。
- Monitoring/Trace は Cloud Run 標準ログのみ（ダッシュボード化は今後）。
