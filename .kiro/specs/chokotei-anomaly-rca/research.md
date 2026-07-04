# Research Log — chokotei-anomaly-rca

Discovery type: **Full**（新規・複合・GCP外部依存あり）。言語: ja。

## Summary

決定論CV検知＋ADKエージェント推論＋HITL学習ループの閉ループを、Cloud Run + Cloud SQL + Vertex AI で構成する。
外部依存の核心は **ADK のセッション永続（Cloud Run 地雷 #1）** と **AgentTool 構成（#2）**。PoC で検知の成立を実証済み。

## Research Log

### Topic 1: ADK バージョンとセッション永続（de-risk #1）
- **所見**: ADK 2.0 GA が最新。セッションは `DatabaseSessionService` で Postgres 永続化。
  2.0 は **async ドライバ必須** → 接続URLは `postgresql+asyncpg://`（`postgresql://` 不可）。
  2.0 で `events` テーブルに新カラムが増え、旧スキーマのままだとチャットが 500。
- **含意（新規プロジェクトの利点）**: 移行が無いので **最初から 2.0＋asyncpg** で組めば地雷を回避。
  バージョンは `google-adk==2.0.x` を**厳密ピン**（`pip freeze` で patch まで固定）。
  Cloud Run は in-memory 既定で履歴消失＋ユーザー混線 → `SESSION_DB_URL` を Cloud SQL に配線し Day1 smoke。
- 出典: [ADK Cloud Run docs](https://google.github.io/adk-docs/deploy/cloud-run/) /
  [ADK 2.0 on Cloud SQL 移行の3つの罠](https://dev.to/toyama0919/upgrading-google-adk-to-20-on-a-cloud-sql-postgres-backend-the-three-things-that-bit-us-43ff) /
  [Persistent ADK + Cloud SQL codelab](https://codelabs.developers.google.com/persistent-adk-cloudsql)

### Topic 2: マルチエージェント意味論（de-risk #2）
- **所見**: `AgentTool`（agents-as-tools）が推奨。ルートを orchestrator/router にし、専門エージェントをツール化。
  `transfer_to_agent` は一方向で文脈/出力を落とすため制御保持には不向き。
- **含意**: 原因推定は **root(orchestrator) + tools**（`query_vibration` / `query_logs` / `search_past_cases` / `get_frame`）。
  マルチエージェント化する場合も専門エージェントは `AgentTool` でラップ。
- 出典: [ADK Custom Tools](https://google.github.io/adk-docs/tools-custom/) /
  [Build agents with ADK: tools codelab](https://codelabs.developers.google.com/devsite/codelabs/build-agents-with-adk-empowering-with-tools)

### Topic 3: Gemini 呼び出し（de-risk #4）
- **所見**: `gemini-2.5-flash` @ us-central1 を ADC で疎通確認済み（PoCセッション実測）。
  ツールスキーマの `anyOf`/`default` はサニタイズ、`HttpRetryOptions[429]` は既定OFF→明示ON。
- **含意**: Vision二段確認・Agent推論とも同モデル。リージョンはモデル=us-central1 / 実行基盤=asia-northeast1。

### Topic 4: pgvector（de-risk #5）
- **所見**: HNSW は ≤2000 次元。Gemini 埋め込みは高次元 → RAG は次元圧縮 or IVFFlat/halfvec を選択。
- **含意**: 過去事例RAGはテキスト埋め込み次元を確認し、HNSW可否で index 種別を決定。P1では件数少のため IVFFlat でも可。

### Topic 5: 検知アルゴリズム（PoC 実証済み）
- **所見**: 正常ラインは cy σ≈1px / 角度 σ≈1°。異常=部品1個が約18px上方変位（frame139–240）。
  融合時は間隔ギャップで検知。false positive ゼロ、閾値マージン大。
- **含意**: 決定論CVで異常確定（重い判定はLLM外＝ガードレール準拠）。二段目Geminiは境界帯のみ。
- 出典: `docs/poc/findings.md`

## Architecture Pattern 評価

- **採用**: イベント駆動の疎結合パイプライン（検知→イベント→推論→通知）＋ HITL フィードバックループ。
- P1 は Cloud Run 少数サービス（monolith 寄り）で疎結合は関数境界。P3 で Pub/Sub 実体化。
- 却下: フル・ストリーミング基盤（Dataflow等）＝ハッカソン規模に過剰。

## Risks & Mitigations（de-risk Top5 対応）

| リスク | 対策 |
|---|---|
| ADK セッション消失（#1） | `DatabaseSessionService` + `postgresql+asyncpg://` + Day1 smoke |
| マルチエージェント文脈落ち（#2） | AgentTool 構成・ADK 2.0.x 厳密ピン |
| WIF 403（#3） | P1はローカル `gcloud run deploy`。CIは提出repo確定後にMao |
| Gemini schema/429（#4） | anyOf/default サニタイズ・429リトライ明示ON・us-central1 |
| pgvector 次元/ソケット（#5） | index種別を次元で選択・`--add-cloudsql-instances`＋`roles/cloudsql.client` |
