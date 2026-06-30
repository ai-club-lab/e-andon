# GCP Integration — ハッカソン標準スタック & 落とし穴

審査要件は「動作確認できる稼働URL」。**Cloud Run（実行基盤）＋ Gemini/ADK（GCP AI）**を最小構成として、
審査期間中（提出7/10 →二次審査 ~7/24、決勝なら 8/19）に**デプロイを落とさない**ことを最優先する。

## 標準スタック

- 実行: **Cloud Run**（scale-to-zero / min-instances=0 で待機コストほぼゼロ）
- AI: **Gemini 2.5 Flash** or **ADK**（Agent Development Kit）。リージョンは **us-central1**（新モデル提供が早い）
- 状態: **Cloud SQL（Postgres + pgvector）** を RAG / セッション永続化に
- CI/CD: **GitHub Actions + Workload Identity Federation（WIF）→ `gcloud run deploy`**
- 秘密: **Secret Manager**（`.env`・キーは公開repoに絶対入れない）

## de-risk Top5（着手と同時に潰す — 既知の地雷）

1. **ADK セッション永続化（Cloud Run）**: 既定 in-memory はインスタンス毎で履歴消失＋ユーザー混線。
   失敗時に無音フォールバックする。→ Day1に `SESSION_DB_URL`（Cloud SQL）を配線して smoke test。
2. **マルチエージェント意味論**: `transfer_to_agent` は一方向で文脈/出力を落とす。制御を保つなら
   **`AgentTool`（agents-as-tools）**。**ADK のバージョンは固定**（state/output_key の regression が生きている）。
3. **WIF → Cloud Run の CI 4大403**: ① project **番号**（IDでなく番号）/ ② workflow に `id-token: write` /
   ③ デプロイに明示 `service_account` / ④ SA へ `workloadIdentityUser` + `tokenCreator`。IAM反映は ~5分待つ。
4. **Gemini スキーマ ＋ 429**: ツールスキーマの `anyOf` / `default` をサニタイズ。
   `HttpRetryOptions[429]` は**既定OFF**なので明示有効化。リージョンは us-central1。
5. **pgvector 次元 ＋ Cloud SQL ソケット**: HNSW は **≤2000次元**（Gemini埋込3072 → halfvec/IVFFlat）。
   Cloud Run→Cloud SQL は `--add-cloudsql-instances` ＋ SA に `roles/cloudsql.client`。

## コスト・撤収

- project = blast radius。全リソースを専用 project に閉じ込め、不要なら `gcloud projects delete` 一発。
- Budget alert を 50/90/100% で設定（**通知のみ。課金は止めない**点に注意）。無料トライアル内に収める。
- 撤収: 非入賞→発表後 / 決勝→決勝後。Cloud Run scale-to-zero なら焦って早期 teardown 不要。

## セキュリティ衛生（公開repo前提）

- 初日に `.gitignore` ＋ GitHub secret scanning を確認。`.env` / キー / SAキーJSON をコミットしない。
- GitHub→GCP は **キーレス（WIF）**。長期 SA キーを発行・配布しない。
- 金額・判定・破壊操作など重い意思決定は **LLM の外（決定論ロジック）＋監査ログ**に置く（HITL）。
