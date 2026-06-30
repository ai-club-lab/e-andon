# Team Dev Kit — Claude Code 共通設定（DevOps × AI Agent Hackathon）

このリポジトリで Claude Code を使う全員が**同じ作法**で作業するための共通設定です。
個人の機密・社外秘・個人メモリは一切含みません（持ち込み禁止）。

## このkitの狙い

ハッカソンの採点軸は徹頭徹尾エンジニアリング（DevOps / SDLC）。よって本kitは
「**1閉ループを完璧に・稼働URLを落とさない**」を最短で実現する作法に絞っています。

- 仕様駆動（Kiro: Requirements → Design → Tasks → Implementation）で手戻りを減らす
- モデル/モードのルーティングを揃え、メンバー間で出力の質をブレさせない
- GCP（Cloud Run + Gemini/ADK）の落とし穴を最初から共有

## モデル・ルーティング（コーディング既定）

> ハッカソン中の自動化/スケジュール実行はフルモデルIDを固定すること（alias は使わない）。

- **既定: Opus 4.8**（`claude-opus-4-8`）— マルチファイル編集・アーキ・Kiro各フェーズ・セキュリティレビュー。
- **フォールバック: Opus 4.7**（フルID `claude-opus-4-7`）— 4.8 がレート制限のとき / 自動・スケジュール実行。
- **`/fast`**: Opus 4.8 の高速モード（同モデル・別レート枠）。対話デバッグ・REPL反復・長いビルド監視に。
- **Sonnet 4.6**: 浅い調査・1ファイル参照・雑談。
- **Haiku 4.5**: サブエージェントの量産ワーカー・並列リサーチ（frontmatter で明示ピン）。
- **Effort: 既定 medium**。アーキ/多ファイルリファクタ/セキュリティレビューのときだけ high。

## モード・ルーティング（Plan / Auto-edit / Auto）

- 3ファイル超 or アーキ判断あり or 要件が曖昧 → **Plan モード**（Shift+Tab ×2）で合意してから編集。
- 特定ファイル＋具体的変更が明確 → **Auto-edit**。
- 事前合意済みの定型作業（バッチ整備・反復） → **Auto**。
- 迷ったら一言「これは [planning / editing / batch] 作業に見える、[mode] でいい？」と確認。

## 仕様駆動ワークフロー（Kiro）

`.kiro/steering/`（プロジェクト全体ルール）と `.kiro/specs/{feature}/`（機能ごとの仕様）で進める。

- Phase 0（任意）: `/kiro:steering` `/kiro:steering-custom`
- Phase 1（仕様）: `/kiro:spec-init "説明"` → `/kiro:spec-requirements {f}` → `/kiro:spec-design {f}` → `/kiro:spec-tasks {f}`
- Phase 2（実装）: `/kiro:spec-impl {f} [tasks]`
- 進捗確認: `/kiro:spec-status {f}`（いつでも）
- レビュー（任意）: `/kiro:validate-gap` `/kiro:validate-design` `/kiro:validate-impl`

ルール: 各フェーズで人間レビュー。`-y` は意図的な早送りのときだけ。

## コンテキスト衛生

- 探索フェーズ後・マイルストーンの節目で `/compact` を提案（キャッシュが温かい ~5分以内）。実行は人間が判断。
- 無関係なタスクに移るときは `/clear`。
- 必要なファイルだけ読む。広いディレクトリ走査より grep/glob で当てる。

## 進め方の原則

- 非自明な提案には、同意の前にリスク/代替/暗黙の前提を一度出す（明らかに機械的な作業は除く）。
- 秘密情報（APIキー・トークン・`.env`）は公開repoに**絶対入れない**。Secret Manager + WIF を使う。
- 詳細ルールは `.claude/rules/` を参照（coding-style / gcp-integration / cost-and-context）。
