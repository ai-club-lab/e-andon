---
name: zenn-article
description: Author top-tier Zenn (zenn.dev) engineering articles in Japanese — title patterns, opening hooks, structural skeletons, voice/register defaults, visual rhythm, and a pre-publish checklist distilled from ~30 trending Zenn articles. Use when drafting, restructuring, or polishing any zenn.dev post — including hackathon writeups (Microsoft Agent Hackathon, etc.), tutorials, postmortems, comparison tables, and AI/Azure/agent/MCP content. Skip for English-only blog posts or non-Zenn platforms (Qiita, note, dev.to use different conventions).
---

# Zenn Article Authoring Skill

A reusable playbook for writing engineering articles that perform on Zenn. Grounded in pattern extraction from ~30 trending/top-engagement Zenn posts (general tech + AI/Azure/agent subset, 2025–2026).

**Default register**: です・ます体 (100% of sampled top articles). Never use だ・である.

**Default language**: 日本語. Technical terms in カタカナ + 英語表記 on first use.

---

## When to invoke this skill

- Drafting a new Zenn article from scratch
- Restructuring an existing draft that "doesn't feel Zenn-ish"
- Polishing title / opening / closing for engagement
- Hackathon submission writeups (Microsoft Agent Hackathon, etc.) where the deliverable is a Zenn article
- Reviewing someone else's Zenn draft

**Do NOT invoke for**: Qiita posts (different culture — だ・である OK, less story framing), note.com essays, dev.to / Medium, internal SaaS docs.

---

## Step 1 — Decide the archetype (BEFORE writing)

Pick exactly one of four skeletons. Mixing kills the article.

### Archetype 1: チュートリアル / セットアップ型
For: "X を作って動かす" hands-on guide
Skeleton:
```
はじめに → 必要な準備 → 設定 → 実装 → デプロイ → ハマりどころ・料金 → まとめ → 参考資料
```
Best for: framework intros, Azure/cloud setup, MCP server自作.

### Archetype 2: 物語型 (postmortem / 試行錯誤)
For: "苦労して〜した話"
Skeleton:
```
序章(地獄の状況) → 試み1 → 試み2 → 試み3 → ついに勝利 → 学び → おわりに
```
Best for: パフォーマンス改善, MVPリリース, 失敗談. **Must include concrete numbers** (回数 / 期間 / 金額 / ユーザー数) — that's the differentiator.

### Archetype 3: Nリスト / 数珠つなぎ型
For: "N選 / Nのステップ / Nつのコツ"
Skeleton:
```
導入(なぜこのリスト) → Tip1 → Tip2 → ... → TipN → まとめ + Next Action
```
**Must end with a single summary table** that lets a skimmer screenshot the whole article.

### Archetype 4: 比較・分類型
For: "A vs B" / "〇〇の選び方"
Skeleton:
```
問題提起 → 分類軸の提示 → カテゴリA → カテゴリB → カテゴリC → 比較まとめ表 → おわりに
```
Best for: SDK/サービス比較, 設計選択肢, AI モデル選定.

---

## Step 2 — Write the title (use a tested formula)

Each formula has ≥2 high-performing precedents on Zenn.

| 型 | テンプレ | 例 |
|---|---|---|
| 年度版・決定版 | 【YYYY年版】〜完全ガイド | 【2026年版】Claude Code 完全ガイド |
| N選・Nステップ | 〜のN選 / 〜N のステップ | 初心者が爆速で〇〇を習得する10のステップ |
| してみた | 〜を〇〇してみた | Azure OpenAIを使ってみた |
| した話・物語 | 〜した話 / なぜ〜か | 20回以上の挫折を経て、MVPをリリースできた話 |
| コロン副題 | 〜入門：〇〇を△△する | LangGraph入門：LLMを"チーム"として動かす〜 |
| 疑問形 | なぜ〜なのか / どう〜するか | TypeScript 7はなぜGoで書き直されたのか |
| 比較表 | [比較表] AとB | [比較表] Azure OpenAIと本家OpenAI APIの比較表 |
| 強い助動詞 | 【結論】【体験記】【簡単に実装！】 | 【結論】TypeScriptの型定義はtypeよりinterfaceを使うべき理由 |

**Rules**:
- Title must contain **concrete object + concrete verb/promise**.
- Use 【】 to front-load context (年度 / レベル / 結論).
- For fast-moving topics (AI/Azure/agent), **年度タグ必須** — looks stale without.
- 抽象タイトル禁止 (e.g., 「AIについて考えた」).

---

## Step 3 — Open with a tested hook (first 200-300 chars)

Pick one of five devices. **Never start with**「本記事では〜について説明します」cold.

### A. 共感の質問フック
> Claude Code を使い始めて、こんな悩みはありませんか？「セットアップしたけど、次に何をすればいいの？」「便利な機能があるらしいけど、使いこなせていない」…

Use for: チュートリアル, Nリスト.

### B. 修羅場シーン
> VSCode や Cursor などでこのようなログ、見たことありますか？私たちのプロジェクトでは、このダイアログが 1 日に何度も表示され、開発体験を著しく損なっていました。

Use for: 物語型 postmortem.

### C. 挨拶＋自己紹介＋本記事の位置づけ
> こんにちは。去年はZennでいっぱい記事を書いたり LLM 関連のお仕事をしてきた 〇〇です。2026年の予想を大胆にまとめておきたいなと思ってこの記事を書き始めました。

Use for: 企業テックブログ, advent calendar, 意見記事.

### D. 結論ファースト / 定義一文
> Claude Code は、Anthropic が提供する**ターミナル／デスクトップベースのAIコーディングエージェント**です。自律的にファイル読み書き、シェルコマンド実行、Git管理を行います。

Use for: リファレンス, 比較記事.

### E. 経験エビデンス先出し
> 私自身、Claude Code を業務で半年以上使い込んできましたが、最初の1ヶ月は正直うまく使えていませんでした。

Use for: Nリスト of tips, opinion essays, postmortem.

---

## Step 4 — Voice & register defaults

Hard rules (observed in 17/17 sampled articles):

- **です・ます体 100%.** だ・である禁止.
- **一人称は1つに固定**:
  - 「私」 → solo-author tutorials / postmortems (most common)
  - 「僕」 → casual tone (sunagaku-style)
  - 「筆者」 → formal reference articles
  - 一人称なし → company blogs, comparison tables, reference posts
  - 「私たち」「我々」「うちのチーム」 → company narrative
- **カタカナ専門用語 OK**, but **first use spells out English + 日本語補足**: `Model Context Protocol (MCP)`, `Retrieval-Augmented Generation (RAG)`.
- **コードブロックは必ず言語タグ**: ` ```typescript `, ` ```python `, ` ```bash `, ` ```json `. 言語なし禁止.
- **太字 (`**...**`)** を H2 ごとに 2-4 回、用語と結論を強調. 斜体はほぼ使わない.
- **絵文字**: 軽め (1-3 個/記事) は OK. リスト記事だけ多めに. エッセイ・比較記事はほぼ無し.

---

## Step 5 — Visual rhythm (per ~1500 words: ≥1 visual aid)

| 視覚要素 | 使いどころ | 観測頻度 |
|---|---|---|
| Markdown 表 | 比較・要約・コマンドリファレンス | 11/17 (高) |
| コードブロック | 実装サンプル・設定 | 14/17 (技術記事必須) |
| スクリーンショット | UI 操作・コンソール出力 | 9/17 (チュートリアル必須) |
| Mermaid 図 | アーキテクチャ・シーケンス | 1/17 (**差別化チャンス**) |
| `:::message` callout | 補足説明 | 3-4/17 (低活用) |
| `:::message alert` callout | 料金・セキュリティ警告 | 3-4/17 (低活用) |
| 更新履歴セクション | エバーグリーンなリファレンス | 必須 |

**狙い目**: Mermaid と callout は使われていないので、入れると一気に「ちゃんとした記事」感が出る。

Zenn 独自記法:
```markdown
:::message
これは補足メッセージ
:::

:::message alert
これは警告メッセージ
:::

:::details タイトル
折りたたみコンテンツ
:::
```

---

## Step 6 — Close with momentum

Final 2 H2 sections matter as much as the opening.

### まとめ / おわりに (どちらか1つ — 物語型のみ両方可)
必須要素:
1. **要点の3行リステート** — "本記事では〜を紹介しました" だけは禁止
2. **Next Action / 関連リンク** — 読者を次の動作へ
3. **オプション CTA** — X/Twitter, 関連書籍, 採用情報

### 参考リンク / 参考資料
必須. 階層付け:
1. 公式ドキュメント
2. 関連ブログ記事
3. GitHub リポジトリ

エバーグリーン記事には **更新履歴 H2** を追加 (再シェアの起点になる).

---

## AI/Azure/agent 特化レイヤー (hackathon writeup などで重要)

一般技術記事との差分。Microsoft Agent Hackathon 提出など、AI/agent 系を書くときは追加でこれを満たす:

1. **年度・月度タグを必ず入れる** — 「【2026年版】」「【2026年3月版】」. AI 領域はスピードが速いので無いと古く見える.
2. **「〜してみた / 作ってみた」フレーミング比率高め** — 探索ログとしての価値が高い (karaage0703, fez_tech, makumaaku など).
3. **料金 H2 を必ず1つ立てる** — 「7. 検証時に発生した実際の料金について」式. 自分で動かした ¥ / $ を具体的に出す. 観測サンプルでは Azure 系記事のほぼ全てが料金セクションを持つ. 一般技術記事は皆無.
4. **アーキテクチャ図 (Mermaid)** を1枚は入れる — 多くの AI/agent 記事が省略しているので、入れるだけで差別化.
5. **デモ媒体 (GIF / 動画リンク)** を本文中に貼る — エンゲージメント向上.
6. **明示的なポジショニング**:
   - 「作ってみた」= 探索ログ・カジュアルトーン
   - 「実装解説」= レシピ・チュートリアル
   - 「仕組み」「入門」= 概念解説 (「ゼロから実装」フレーム強い)
   - タイトルと内容を一致させる.
7. **公式 SDK を使わず自作するフレーム** が高評価につながりやすい (loglass「手作りして学ぶMCPの仕組み」, norma「PythonとOllamaでゼロからRAGを実装」). 深い理解の証明になる.
8. **比較表記事は AI 領域で特に強い** — 変化の激しさ × クリアな整理 = ゴールド (microsoft Azure vs OpenAI, yukikato AWS vs Azure 2026).
9. **筆者経歴 & 期間を明記** — 「業務で半年以上」「CTO」「去年から LLM 関連」など信頼性のアンカーを入れる.

---

## Pre-publish checklist (15 items)

公開前に必ず通す:

- [ ] タイトルがテスト済みフォーミュラ ( Step 2 表) のいずれかに該当
- [ ] タイトルに具体的な対象物 + 具体的な動詞/約束が含まれる
- [ ] (AI/Azure/agent 記事の場合) 年度タグあり
- [ ] 冒頭 200-300 字が 5 つのフックパターン (A〜E) のいずれかに該当
- [ ] 「本記事では〜について説明します」で始まっていない
- [ ] アーキタイプ 1〜4 のいずれか1つで一貫している (混在していない)
- [ ] です・ます体で統一. だ・である無し
- [ ] 一人称が 1 つに固定 (私 / 僕 / 筆者 / なし のいずれか)
- [ ] 略語は初出で英語フルスペル + 日本語補足
- [ ] 全コードブロックに言語タグあり
- [ ] 1500 字あたり最低 1 つの視覚要素 (表 / Mermaid / SS / callout)
- [ ] (AI/Azure 系の場合) 料金 H2 あり & 具体的 ¥/$ あり
- [ ] まとめが 3 行リステート + Next Action を含む
- [ ] 参考リンク H2 あり (公式 > ブログ > GitHub の階層)
- [ ] (postmortem の場合) 具体的な数字 (回数 / 期間 / 金額 / ユーザー数) を最低 1 つ

---

## Anti-patterns (これをやると死ぬ)

- 冒頭 200 字に hook が無い (setup や import からいきなり始まる)
- コードブロックに言語タグ無し / 散文ゼロのコード壁
- 抽象タイトル ( 「AIについて考えた」 など )
- 早く動く話題なのに年度タグ無し
- まとめが要約ゼロ (「本記事では〜を紹介しました」のみ)
- 個人開発 / 物語フレーミングなのに筆者の声がゼロ
- Zenn で だ・である を使う (場違い)

---

## Working with this skill

ユーザーから Zenn 記事執筆を依頼されたら:

1. **アーキタイプを聞く / 提案する** (Step 1)
2. **タイトル案を 3-5 個出す** (Step 2 のフォーミュラ別に)
3. **冒頭フックの候補を出す** (Step 3 の A〜E から内容に合うもの 2-3 案)
4. **アウトラインを skeleton から派生** (Step 1 のスケルトン)
5. 執筆 → Step 4-6 のルール適用
6. **公開前チェックリストを通す** (Pre-publish checklist)

タイトル・冒頭・まとめは特に時間をかける. ボディは skeleton に沿って書けばだいたい安定する.
