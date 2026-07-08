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

## 公開デモURLの脅威モデル（無認証エンドポイント）

審査要件上、稼働URLは誰でも触れる状態で公開している。攻撃面と対策:

- **RAG汚染**（最重要）: `/feedback` の訂正はそのまま `past_cases` に埋め込み化され次回推論の few-shot になる。
  対策: ①実在イベントの検証（`event_id` が現インスタンスに存在しなければ拒否）②`human_cause` は200字上限
  ③IP毎のレート制限（20 req/min）④全訂正は `feedback` テーブルに監査ログとして残り、汚染事例は
  `past_cases` から SQL で除去可能（出所 `source_event_id` を保持）。
- **LLMコスト攻撃**: `/chat` は Vertex 呼び出しを伴うため、メッセージ500字上限＋同レート制限。
  Cloud Run 側も max-instances でスパイクの上限を拘束。
- **プロンプトインジェクション**: チャットは読み取り専用ツール（センサー照会・過去事例検索）しか持たず、
  停止・裁定・削除など状態を変える操作は一切 LLM から実行できない（重い操作はLLMの外、上記の通り）。
- 本番運用時は Cloud Run の IAM 認証（`--no-allow-unauthenticated`）＋ IAP を前提とする。

## リージョン（Req 10.6）

- モデル呼び出し = **global エンドポイント**（Gemini 3 系は global 提供のみ。embedding も同居）／
  実行基盤・Cloud SQL・AR・GCS = **asia-northeast1**。
- `test_guardrails.py` で `GCP.model_region` / `GCP.runtime_region` を検証。

## コスト（Req 10.2）

- Cloud Run は **min-instances=0（scale-to-zero）**。Cloud SQL は常駐課金のため停止手順を
  `infra/README.md` に明記（`activation-policy=NEVER`）。予算 ≤¥10,000/月・無料枠優先。

## 既知の限界

- `event_id` は インスタンス毎の連番のため複数インスタンス跨ぎで衝突しうる（単一インスタンスのデモでは問題なし）。
  本番運用ではインスタンス/時刻成分を付与して大域一意化する。
- Monitoring/Trace は Cloud Run 標準ログのみ（ダッシュボード化は今後）。
