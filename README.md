# surfbot

RSS フィードを定期巡回し、重要なアイテムを Kanboard の Inbox へ自動追加するボット。`claude` CLI を使って重要度評価・カード生成を行い、ユーザーのフィードバック（Positive/Negative 列への移動）から興味プロファイルを学習する。

## 必要なもの

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- [claude CLI](https://claude.ai/code)（`claude` コマンドが PATH に存在すること）
- Kanboard インスタンスと API トークン

## セットアップ

```bash
# 依存関係のインストール
uv sync

# 設定ファイルの作成
cp config/config.yaml.example config/config.yaml
# config.yaml を編集して Kanboard の接続情報を設定

# フィードと評価指示を編集
vi config/feeds.yaml
vi config/preferences.md
vi config/format.md
```

## 起動

```bash
uv run surfbot
```

SIGTERM / SIGINT（Ctrl+C）で現在のサイクル完了後に安全に終了する。

## Docker で起動する

### 前提

- Docker および Docker Compose が利用可能であること
- Claude Code の OAuth トークンを取得済みであること

### セットアップ

```bash
# 環境変数ファイルを作成
cp .env.example .env
# トークンは claude setup-token で生成できる
# .env を開き CLAUDE_CODE_OAUTH_TOKEN に生成したトークンを設定
vi .env

# 設定ファイルを作成（未作成の場合）
cp config/config.yaml.example config/config.yaml
vi config/config.yaml
```

### 起動・停止

```bash
# バックグラウンドで起動
docker compose up -d

# ログを確認
docker compose logs -f

# 停止
docker compose down
```

コンテナはリポジトリルートをそのままマウントするため、`config/` や `data/` への変更はコンテナ再起動なしに反映される。

## Kanboard カラム構成

| カラム | 役割 |
|---|---|
| **Inbox** | surfbot が重要と判断したアイテムを追加する |
| **Keep** | ユーザーが後で読むために保持するアイテム（surfbot は操作しない） |
| **Positive** | 有用だったアイテム → surfbot がフィードバック学習後にクローズ |
| **Negative** | 不要だったアイテム → surfbot がフィードバック学習後にクローズ |

## 設定ファイル

| ファイル | 用途 |
|---|---|
| `config/config.yaml` | Kanboard 接続情報・サイクル間隔・Inbox 最大件数 |
| `config/feeds.yaml` | フィード URL とフィードごとの指示 |
| `config/preferences.md` | 重要度評価に対する自然言語指示 |
| `config/format.md` | カード生成に対する自然言語指示 |

`data/feedback.md` と `data/state.yaml` は surfbot が自動管理する。`feedback.md` はユーザーが直接編集しても構わない。

## ドキュメント

- [ユーザーストーリー](doc/userstory.md)
- [アーキテクチャ設計](doc/arch.md)
