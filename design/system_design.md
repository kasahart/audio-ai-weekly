# 音声研究週報 自動更新システム
## 要件定義書 ／ アーキテクチャ設計書

**バージョン:** v1.3　　**作成日:** 2026年4月25日　　**作成:** Claude Sonnet 4.6

---

### 改訂履歴

| 版 | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-04-25 | 初版作成 |
| 1.1 | 2026-04-25 | 4項目の要件変更を反映（キーワード管理・50件・GitHub Models・過去データ保持） |
| 1.2 | 2026-04-25 | 開発環境（DevContainer）節を追加 |
| 1.3 | 2026-04-25 | JSON ファイル命名規則を YYYY-MMDD 形式に変更 |

> ★ = 変更箇所

---

## 1. プロジェクト概要

本ドキュメントは、音声・音響分野（arXiv cs.SD / eess.AS カテゴリ）の最新論文を毎週自動収集・解析し、GitHub Pages 上で公開する Web システムの要件定義およびアーキテクチャ設計をまとめたものです。

### 1.1 背景と目的

- arXiv cs.SD・eess.AS には週 100 件超の論文が投稿されており、手動での全量確認は困難
- 音の基盤モデル・音源分離・異音検知の 3 分野に特化した週次サマリーを自動生成・公開する
- GitHub Models から提供される Claude を用いて日本語要約（6 観点）を生成する
- 過去の週次データをすべて保持し、UI 上で任意の週を参照できるようにする

### 1.2 システム名称

ArXiv 音声研究週報 自動更新システム（Sound Research Weekly Report System）

### 1.3 対象ユーザー

- 音声・音響 AI 研究者・エンジニア
- 音の基盤モデル / 音源分離 / 異音検知に関心を持つ実務者

---

## 2. 要件定義

### 2.1 機能要件

#### 2.1.1 論文収集

- arXiv API（export.arxiv.org）から cs.SD・eess.AS カテゴリの直近 7 日分の新着論文を取得する
- ★ 対象キーワードは `config/keywords.yaml` で管理し、追加・削除をコード修正なしに行えるようにする
- ★ フィルタ後の取得件数上限は **50 件**とする（設定ファイルで変更可能）
- 重複論文（クロスリスト）は arXiv ID をキーとして除外する
- キーワードは OR 条件で適用し、タイトルまたはアブストラクトに含まれる論文を対象とする

#### 2.1.2 論文解析・要約

- ★ GitHub Models から提供される Claude（`github-models` エンドポイント）を使用し、`GITHUB_TOKEN` で認証する
- 各論文のアブストラクトを以下の 6 観点で日本語構造化要約する
  - ① どんなもの？
  - ② 先行研究と比べてすごい点
  - ③ 技術・手法のキモ
  - ④ 有効性の検証方法
  - ⑤ 議論・限界
  - ⑥ 次に読むべき論文（arXiv ハイパーリンク付き）
- 今週の技術トレンドを 3 行で要約する

> **変更（v1.1）:** `Anthropic Claude API + ANTHROPIC_API_KEY` から `GitHub Models + GITHUB_TOKEN` に変更。外部シークレット不要になりセットアップが簡素化される。

#### 2.1.3 データ永続化

- ★ 解析結果の JSON ファイルは上書きせず、週ごとに新規ファイルとして保存する（`data/weekly/YYYY-MMDD.json`）
- ★ 全週分のインデックスファイル（`data/index.json`）を自動生成・更新し、週一覧を管理する
- `data/index.json` の先頭要素が最新週ファイルを示す
- Git リポジトリに週次ファイルを蓄積することで、データは半永久的に保持される

> **変更（v1.1）:** 「週次ファイル新規作成 + インデックス管理」に変更。過去データがすべて保持される。

#### 2.1.4 Web ページ生成・公開

- React + Vite でビルドした静的 HTML を GitHub Pages で公開する
- UI は現行の Artifact デザイン（ダークテーマ、アコーディオン表示）を踏襲する
- ★ 週セレクター（ドロップダウン）を追加し、過去の任意の週のデータを UI 上で参照できるようにする
- 初期表示は最新週のデータを表示する

#### 2.1.5 自動スケジューリング

- GitHub Actions により **毎週金曜日 21:00 JST（12:00 UTC）** に自動実行する
- 手動トリガー（`workflow_dispatch`）にも対応する
- 実行ログを GitHub Actions の Artifacts として 30 日間保持する

---

### 2.2 非機能要件

| 項目 | 要件 | 備考 |
|---|---|---|
| 可用性 | GitHub Pages の SLA に準拠（月次 99.9% 以上） | |
| 実行時間 | GitHub Actions ジョブは 30 分以内に完了すること | 50 件 × 解析時間を考慮 |
| コスト | ★ GitHub Models 利用のため API 費用は原則無料 | GitHub Pro / Team プランの制限内 |
| セキュリティ | ★ `GITHUB_TOKEN` のみ使用。外部 API キー不要 | Actions の自動発行トークンで完結 |
| 保守性 | スクリプト・設定ファイルはすべて Git 管理下に置く | |
| 拡張性 | ★ キーワードを設定ファイルで追加・削除可能 | コード修正不要 |
| データ保持 | ★ 過去の週次 JSON を削除・上書きせず永続保持する | Git 履歴でも復元可能 |

### 2.3 制約事項

- arXiv API の利用規約に準拠し、過度なリクエストを行わない（最大 3 req/sec）
- GitHub Models の利用規約・レート制限に準拠する
- GitHub Actions の無料枠（月 2,000 分）内で運用する（週次実行で消費は月約 120〜180 分）
- 論文の図・全文の無断転載は行わず、アブストラクトのみを使用する
- 週次 JSON ファイルの蓄積によりリポジトリサイズが増加するため、1 ファイルあたり 500KB 以内を目安とする

---

## 3. アーキテクチャ設計

### 3.1 システム全体構成

本システムは「データ収集・解析パイプライン」と「静的 Web フロントエンド」の 2 層で構成され、GitHub エコシステムで完結します。

> 外部依存は arXiv API と GitHub Models の 2 つのみ。GitHub Models は `GITHUB_TOKEN` で認証されるため、外部シークレット管理が不要です。

| レイヤー | コンポーネント | 技術スタック |
|---|---|---|
| スケジューラ | 週次自動実行 | GitHub Actions (cron) |
| データ収集 | arXiv API クライアント | Python 3.11 + requests |
| AI 解析 | ★ GitHub Models クライアント | ★ OpenAI SDK（GitHub Models エンドポイント） |
| データ永続化 | ★ 週次 JSON の Git コミット | ★ `data/weekly/YYYY-MMDD.json`（上書きなし） |
| インデックス管理 | ★ 全週インデックス自動更新 | ★ `data/index.json` |
| フロントエンド | 静的 React アプリ | React 18 + Vite |
| ホスティング | 静的サイト配信 | GitHub Pages |

---

### 3.2 GitHub Models 連携詳細

| 項目 | 値 |
|---|---|
| エンドポイント | `https://models.github.ai/inference` |
| 認証 | `Authorization: Bearer $GITHUB_TOKEN` |
| 使用モデル | `openai/gpt-5`（settings.yaml で変更可能） |
| SDK | `openai` Python パッケージ（base_url を GitHub Models に向ける） |
| レート制限 | GitHub Pro: 50 req/day, 10 req/min |
| コスト | GitHub Pro / Team / Enterprise に含まれる |

> `GITHUB_TOKEN` は GitHub Actions が自動発行するため、リポジトリ管理者が Secrets に登録する必要はありません。

---

### 3.3 リポジトリ構成

```
arxiv-weekly/
├── .devcontainer/
│   └── devcontainer.json       # Python 3.11 ベース image + features + 初回セットアップ
├── .github/
│   └── workflows/
│       └── update.yml          # GitHub Actions ワークフロー
├── config/
│   ├── keywords.yaml           # ★ フィルタキーワード（追加・削除はここだけ）
│   └── settings.yaml           # max_papers=50 等のシステム設定
├── data/
│   ├── index.json              # ★ 全週インデックス
│   └── weekly/
│       └── YYYY-MMDD.json      # ★ 週次論文データ（不変・上書きなし）
├── scripts/
│   ├── fetch_papers.py         # arXiv 取得・フィルタ・カテゴリ分類
│   ├── analyze_papers.py       # GitHub Models で 6 観点解析
│   ├── build_data.py           # 週次 JSON 生成・インデックス更新
│   └── test_connection.py      # GitHub Models 疎通確認
├── web/
│   ├── public/data/            # ビルド時にコピーされるデータ
│   ├── src/
│   │   ├── App.jsx
│   │   └── components/
│   │       ├── Header.jsx
│   │       ├── WeekSelector.jsx
│   │       ├── CategoryFilter.jsx
│   │       ├── PaperCard.jsx
│   │       └── TrendSummary.jsx
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── requirements.txt            # openai, pyyaml
└── README.md
```

---

### 3.4 設定ファイル仕様

#### 3.4.1 config/keywords.yaml

| フィールド | 型 | 説明 |
|---|---|---|
| `include` | `string[]` | フィルタリングキーワード（OR 条件）。タイトル・アブストに対してマッチ |
| `exclude` | `string[]` | 除外キーワード（任意）|
| `categories` | `string[]` | 対象 arXiv カテゴリ（例: cs.SD, eess.AS） |
| `ui_categories` | `object[]` | UI 表示用カテゴリ定義（id / label / color / keywords） |

> 例）「異常音検知」の研究が増えた場合は `include` に `'anomalous sound'` を追記するだけでフィルタに反映される。

#### 3.4.2 config/settings.yaml

| フィールド | 型 | デフォルト | 説明 |
|---|---|---|---|
| `max_papers` | int | `50` | ★ フィルタ後の最大取得件数 |
| `lookback_days` | int | `7` | 取得対象期間（日数） |
| `model` | string | `claude-3-7-sonnet` | ★ GitHub Models で使用するモデル名 |
| `retry_max` | int | `3` | API エラー時の最大リトライ回数 |
| `request_interval` | float | `1.0` | arXiv API リクエスト間隔（秒） |

---

### 3.5 データフロー

#### Step 1 — 論文収集（fetch_papers.py）

1. arXiv API に GET リクエスト（クエリ：`cat:cs.SD OR cat:eess.AS`、直近 7 日）
2. Atom XML を解析し、タイトル・ID・投稿日・著者・アブストラクトを抽出
3. `config/keywords.yaml` の include キーワードで OR フィルタリングを適用
4. ★ フィルタ後 50 件を上限として `data/raw_papers.json` に保存

#### Step 2 — AI 解析（analyze_papers.py）

1. ★ GitHub Models エンドポイントに openai SDK 経由でリクエスト。`GITHUB_TOKEN` で認証
2. `raw_papers.json` を読み込み、各論文アブストラクトを 6 観点の JSON で返すよう指示
3. レスポンスをパースし `analyzed_papers.json` に追記
4. API エラー時はリトライ（最大 3 回、指数バックオフ）

#### Step 3 — データ生成・コミット（build_data.py）

1. ★ 実行日付（`YYYY-MMDD`）をファイル名として `data/weekly/YYYY-MMDD.json` に**新規保存**
2. ★ 既存ファイルが存在する場合はスキップ（同一日の重複実行を防止）
3. ★ `data/index.json` を更新：日付・ファイルパス・論文数・実行日時を追記
4. 変更ファイルをまとめて git commit & push

#### Step 4 — フロントエンドビルド・デプロイ

1. `web/` ディレクトリで `npm ci && npm run build`
2. ★ フロントエンドは起動時に `data/index.json` を取得して週一覧を構築
3. ★ デフォルトはインデックス先頭の最新週を表示。週セレクターで任意の週に切り替え可能
4. `web/dist/` を GitHub Pages（gh-pages ブランチ）にデプロイ

---

### 3.6 GitHub Actions ワークフロー

| ジョブ名 | 内容 | 使用トークン | 依存 |
|---|---|---|---|
| `fetch` | arXiv API から論文取得 | 不要 | なし |
| `analyze` | ★ GitHub Models で 6 観点解析 | ★ `GITHUB_TOKEN` | fetch |
| `build` | ★ 週次 JSON 生成・インデックス更新・push | `GITHUB_TOKEN` | analyze |
| `deploy` | Vite ビルド → GitHub Pages | `GITHUB_TOKEN` | build |

> ワークフローに `permissions: contents: write` を付与することで push 操作が可能になります。

---

### 3.7 フロントエンド設計

#### 3.7.1 コンポーネント構成

| コンポーネント | 役割 |
|---|---|
| `App.jsx` | データ取得・週セレクター・カテゴリフィルター状態管理 |
| `Header.jsx` | タイトル・日付・論文数 |
| `WeekSelector.jsx` | ★ 週一覧ドロップダウン。`index.json` から週リストを生成 |
| `CategoryFilter.jsx` | カテゴリタブ（すべて / 音の基盤モデル / 音源分離 / 異音検知） |
| `PaperCard.jsx` | 論文カード。ヘッダークリックで 6 観点を一括展開 |
| `TrendSummary.jsx` | 今週のトレンド 3 行 |

#### 3.7.2 データ取得フロー

1. 起動時：`data/index.json` を fetch → 週リストを WeekSelector に渡す
2. 初期表示：インデックス先頭の `data/weekly/YYYY-MMDD.json` を fetch
3. ★ 週切替時：`data/weekly/YYYY-MMDD.json` を fetch → 選択週のデータを表示

---

### 3.8 データスキーマ

#### data/index.json

```json
{
  "weeks": [
    {
      "date": "2026-0425",
      "file": "weekly/2026-0425.json",
      "count": 42,
      "generated_at": "2026-04-25T12:00:00Z"
    }
  ],
  "generated_at": "2026-04-25T12:05:00Z"
}
```

#### data/weekly/YYYY-MMDD.json

```json
{
  "date": "2026-0425",
  "generated_at": "2026-04-25T12:00:00Z",
  "total": 42,
  "categories": [
    {
      "id": "foundation",
      "label": "音の基盤モデル",
      "color": "#38bdf8",
      "papers": [
        {
          "id": "2604.10905",
          "date": "Apr 15",
          "title": "...",
          "titleJa": "...",
          "org": "NVIDIA / UMD",
          "url": "https://arxiv.org/abs/2604.10905",
          "what": "...",
          "novel": "...",
          "method": "...",
          "validation": "...",
          "discussion": "...",
          "nextReads": [
            { "label": "Qwen-Audio (2023)", "url": "https://arxiv.org/abs/2311.07919" }
          ]
        }
      ]
    }
  ],
  "trend": [
    "① 音の基盤モデルは...",
    "② 音源分離は...",
    "③ 異音検知は..."
  ]
}
```

---

### 3.9 セキュリティ設計

| 対象 | 対策 |
|---|---|
| ★ GitHub Models 認証 | ★ `GITHUB_TOKEN`（Actions 自動発行）を使用。外部 API キー不要 |
| GitHub Token 権限 | `contents: write` のみ付与。最小権限の原則を遵守 |
| arXiv API | 認証不要。User-Agent ヘッダーを付与してリクエスト元を明示 |
| フロントエンド | 静的サイトのため API エンドポイントは存在しない。React のデフォルトエスケープで XSS 対策 |
| 週次 JSON の改ざん | Git コミット履歴で変更を追跡可能。意図しない上書きはスキップ処理で防止 |

---

## 4. 開発環境（DevContainer）

### 4.1 DevContainer が解決する課題

| 課題 | DevContainer による解決 |
|---|---|
| Python・Node.js のバージョン差異 | コンテナ内で固定バージョンを使用。ホストの環境に依存しない |
| arXiv / GitHub Models への疎通確認 | コンテナからそのまま Python スクリプトを実行できる |
| GitHub Actions との環境差異 | CI と同じ Ubuntu ベースイメージを使用 |
| 依存パッケージの競合 | venv をコンテナ内に閉じ込め、グローバル汚染を防ぐ |
| 新メンバーのセットアップ時間 | 「Reopen in Container」するだけで完了 |

### 4.2 コンテナ環境仕様

| 項目 | 仕様 |
|---|---|
| ベースイメージ | `mcr.microsoft.com/devcontainers/python:3.11-bullseye` |
| Python | 3.11（コンテナ固定） |
| Node.js | 20.x LTS（nvm 経由） |
| GitHub CLI | インストール済み（GitHub Models テスト用） |

### 4.3 VS Code 拡張機能（自動インストール）

| 拡張機能 | 用途 |
|---|---|
| `ms-python.python` | Python 補完・デバッグ |
| `charliermarsh.ruff` | Python フォーマッタ |
| `esbenp.prettier-vscode` | TypeScript / JSX フォーマット |
| `GitHub.copilot` | AI コード補完 |
| `GitHub.vscode-github-actions` | workflow.yml の構文チェック |
| `redhat.vscode-yaml` | config/*.yaml の編集支援 |

### 4.4 開発環境チェックリスト

```bash
python --version                              # Python 3.11.x
node --version                                # v20.x.x
gh auth status                                # Logged in to github.com
python scripts/test_connection.py             # GitHub Models 疎通確認
python scripts/fetch_papers.py --dry-run      # arXiv 取得テスト
cd web && npm run dev                         # http://localhost:5173
```

---

## 5. 開発ロードマップ

| フェーズ | 作業内容 | 期間目安 |
|---|---|---|
| Phase 1 | ★ リポジトリ作成・DevContainer 構築・GitHub Pages 疎通確認 | 1 日 |
| Phase 2 | arXiv 取得スクリプト実装・keywords.yaml 設計・単体テスト | 0.5 日 |
| Phase 3 | GitHub Models 解析スクリプト実装・プロンプト調整（50 件対応） | 1 日 |
| Phase 4 | 週次 JSON 生成・index.json 管理スクリプト実装 | 0.5 日 |
| Phase 5 | GitHub Actions ワークフロー実装・E2E テスト | 1 日 |
| Phase 6 | React フロントエンド実装（週セレクター UI 追加） | 1 日 |
| Phase 7 | 全体結合テスト・初回本番実行確認 | 0.5 日 |

---

## 6. 技術スタック一覧

| カテゴリ | ライブラリ／サービス | バージョン | 用途 |
|---|---|---|---|
| 言語 | Python | 3.11 | バックエンドスクリプト全般 |
| 言語 | TypeScript / React | 18.x | フロントエンド |
| ★ AI | ★ GitHub Models (Claude) | — | ★ 論文解析・要約（GITHUB_TOKEN 認証） |
| ★ AI SDK | ★ openai（Python） | latest | ★ GitHub Models への接続 |
| ビルド | Vite | 5.x | React アプリビルド |
| CI/CD | GitHub Actions | — | 自動実行・デプロイ |
| ホスティング | GitHub Pages | — | 静的サイト配信 |
| ★ 開発環境 | ★ Dev Containers | — | 統一開発環境 |
| ★ 開発環境 | ★ GitHub Codespaces | — | ブラウザベースの開発環境 |

---

## 7. リスクと対策

| リスク | 影響 | 対策 |
|---|---|---|
| arXiv API の仕様変更 | 中 | 公式ドキュメントを定期確認。OAI-PMH をフォールバックとして実装 |
| ★ GitHub Models のレート制限 | 中 | ★ 1 論文ごとに sleep を挿入。50 件を上限として制限内に収める |
| ★ GitHub Models の提供モデル変更 | 中 | ★ モデル名を settings.yaml で管理し、変更時はファイル編集のみで対応 |
| リポジトリサイズの増大 | 低 | 週次 JSON を 500KB 以内に抑える |
| 同一日の重複実行 | 低 | 既存ファイルが存在する場合はスキップ処理で防止 |
| GitHub Actions 無料枠超過 | 低 | 月 2,000 分の枠内で運用（週次で月約 120〜180 分） |

---

## 8. 用語集

| 用語 | 説明 |
|---|---|
| arXiv | オープンアクセスプレプリントサーバー（Cornell University 運営） |
| cs.SD | arXiv の Sound カテゴリ |
| eess.AS | arXiv の Audio and Speech Processing カテゴリ |
| GitHub Models | GitHub が提供する AI モデル推論サービス。`GITHUB_TOKEN` で利用可能 |
| `GITHUB_TOKEN` | GitHub Actions が自動発行するリポジトリスコープのアクセストークン |
| `YYYY-MMDD` | 本システムのファイル命名規則（例：`2026-0425` = 2026年4月25日） |
| GitHub Actions | GitHub が提供する CI/CD プラットフォーム |
| GitHub Pages | GitHub が提供する静的サイトホスティングサービス |
| `workflow_dispatch` | GitHub Actions の手動トリガー機能 |
| Vite | 高速な JavaScript ビルドツール |

---

*以上（v1.3）*
