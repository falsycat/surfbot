# surfbot アーキテクチャ設計

## 1. システム概要

surfbot は定期実行型の情報収集ボットである。RSS フィードなどから情報を取得し、`claude` CLI を用いて重要度評価とカード内容生成を行い、Kanboard の Inbox へアイテムを追加する。また、ユーザーのフィードバック（Positive/Negative 列への移動）を `feedback.md` に蓄積し、以降の重要度評価に活用する。

```
[Feeds]  [Config Files]  [feedback.md]  [state.yaml]  [claude CLI]
    |           |               |              |             |
    v           v               v              v             v
+------------------------------------------------------------------+
|                            surfbot                               |
|          Fetcher -> Evaluator -> CardGenerator                   |
|             (FeedItems + Inbox Tasks を統合評価)                 |
|                                                                  |
|                      Cycle Orchestrator                          |
|                  (Feedback / Fetch+Rank+Update)                  |
+------------------------------------------------------------------+
                              |
                              v
                        [Kanboard API]
```

Kanboard はタスクの CRUD に使用する。フィードバック履歴は `feedback.md`、フィードごとの最終参照日時は `state.yaml` に保持する。

---

## 2. ディレクトリ構成

```
surfbot/
├── config/                # ユーザー設定ファイル（直接編集）
│   ├── config.yaml        # 接続情報・動作パラメータ
│   ├── feeds.yaml         # 情報源と個別指示
│   ├── format.md          # カード生成指示
│   └── preferences.md     # 重要度評価指示
├── data/
│   ├── feedback.md        # surfbot が自動更新するフィードバック蓄積ファイル
│   └── state.yaml         # surfbot が自動更新するフィード参照状態ファイル
├── src/
│   └── surfbot/           # アプリケーションソース
│       ├── __init__.py
│       ├── main.py        # エントリポイント・スケジューラ
│       ├── config.py      # 設定ファイルの読み込みとモデル定義
│       ├── fetcher.py     # フィード取得・パース
│       ├── kanboard.py    # Kanboard JSON-RPC クライアント
│       ├── llm.py         # claude CLI ラッパー
│       ├── state.py       # フィード参照状態の管理
│       └── cycle.py       # メインサイクルのオーケストレーション
└── pyproject.toml
```

---

## 3. コンポーネント詳細

### 3.1 Config Loader (`config.py`)

設定ファイルを読み込み、Pydantic モデルとして提供する。各サイクルの開始時にファイルのタイムスタンプを確認し、変更があった場合のみ再読み込みする。

| ファイル | 型 | 用途 |
|---|---|---|
| `config.yaml` | `AppConfig` | Kanboard URL/認証情報、サイクル間隔、Inbox 最大件数 |
| `feeds.yaml` | `list[FeedConfig]` | フィード URL とフィードごとの LLM 指示文 |
| `format.md` | `str` | カード生成時のグローバル指示（LLM プロンプトに挿入） |
| `preferences.md` | `str` | 重要度評価時の指示（LLM プロンプトに挿入） |
| `feedback.md` | `str` | surfbot が自動更新する興味関心プロファイル（LLM プロンプトに挿入） |

`feedback.md` はユーザーが直接編集しても構わない。

### 3.2 Feed Fetcher (`fetcher.py`)

- `feedparser` を使って RSS / Atom フィードを取得・パース
- 各アイテムを `FeedItem(url, title, published_at, content)` に正規化
- `feeds.yaml` で `fetch_content: true` が指定された場合は、`httpx` で元記事の本文も取得して `content` に格納する
- `since: datetime | None` が指定された場合、`published_at > since` のアイテムのみを返す（`published_at` が取得できないアイテムは常に含める）
- フィード取得エラーは警告ログを出して次のフィードへ継続

### 3.3 State Manager (`state.py`)

`data/state.yaml` にフィードごとの最終参照日時を読み書きする。重複排除はタイムスタンプベースで行うため、Kanboard への URL 検索は不要。

| メソッド | 説明 |
|---|---|
| `get_last_fetched(feed_name)` | フィードの最終参照日時を返す（未記録の場合は `None`） |
| `update_last_fetched(feed_name, dt)` | 最終参照日時を更新して `state.yaml` に書き込む |

### 3.4 Kanboard Client (`kanboard.py`)

Kanboard の JSON-RPC API をラップする薄いクライアント。

主要メソッド:

| メソッド | 説明 |
|---|---|
| `get_tasks(column)` | 指定列のオープンタスク一覧を取得（description を含む） |
| `get_task_comments(task_id)` | タスクのコメント一覧を取得（ユーザー記載の理由を拾うため） |
| `create_task(card)` | Inbox にカードを作成 |
| `add_comment(task_id, text)` | タスクにコメントを追加 |
| `close_task(task_id)` | タスクをクローズ |
| `update_task_position(task_id, column_id, position)` | タスクの列内ソート順を更新 |

### 3.5 LLM Processor (`llm.py`)

`claude` CLI をサブプロセスとして非同期に呼び出す（`asyncio.create_subprocess_exec`）。

提供する機能:

| 関数 | 入力 | 出力 |
|---|---|---|
| `evaluate_importance(items)` | `FeedItem` または Kanboard タスクのリスト | 重要度スコア付きのリスト |
| `generate_card(item, feed_instruction)` | FeedItem、フィード個別指示 | `dict`（Kanboard がサポートする任意のフィールド） |
| `update_interest_profile(positive_tasks, negative_tasks, current_profile)` | Positive タスク全件、Negative タスク全件、現在の `feedback.md` 全文 | `(新プロファイル全文, 学習内容サマリー)` |

`evaluate_importance` は新着 `FeedItem` と既存 Inbox タスクを混在したリストで受け取り、統合スコアリングを行う。入力の型（FeedItem / KanboardTask）に応じてプロンプトを構築するが、スコアリングのロジックは共通。

**フィードバック学習の仕組み**: Positive タスクと Negative タスクを区別して `update_interest_profile` に渡す。タスクの description とコメント（ユーザーが記載した理由）も入力に含める。LLM はプロファイル全文を書き直して返し、`feedback.md` を上書きする。重要度評価時は `preferences.md` と `feedback.md` をプロンプトに含める。

### 3.6 Cycle Orchestrator (`cycle.py`)

1サイクルの処理を 2 フェーズで実行する（後述「実行フロー」参照）。**Keep 列は surfbot の操作対象外**であり、`get_tasks` の呼び出し対象は Inbox / Positive / Negative の 3 列のみ。

---

## 4. 実行フロー

### 4.1 メインループ

`main.py` は asyncio で動作し、`config.yaml` に設定された間隔で `cycle.run()` を呼び出す。各サイクルの開始時に全設定ファイルのタイムスタンプを確認し、変更があれば再読み込みする。

SIGTERM / SIGINT を受信したら現在のサイクルが完了した後に終了する。サイクル間のスリープ中にシグナルを受け取った場合は即座に抜け出す。

```python
async def main():
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    while not shutdown.is_set():
        config.reload_if_changed()
        await cycle.run()
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=config.interval_seconds)
        except asyncio.TimeoutError:
            pass
```

### 4.2 サイクル処理（1回の実行）

#### Phase 1: フィードバック処理

Positive と Negative を別々に取得して極性を保持したまま LLM に渡す。タスクの description とコメント（ユーザーが記載した理由）も取得して入力に含める。

```
positive_tasks = await kanboard.get_tasks(Positive)
negative_tasks = await kanboard.get_tasks(Negative)

if positive_tasks or negative_tasks:
    # ユーザーが記載した理由をコメントから並列取得
    all_tasks = positive_tasks + negative_tasks
    comments_list = await asyncio.gather(*[
        kanboard.get_task_comments(task.id) for task in all_tasks
    ])
    for task, comments in zip(all_tasks, comments_list):
        task.comments = comments

    current_profile = read("data/feedback.md")
    new_profile, summary = await llm.update_interest_profile(
        positive_tasks, negative_tasks, current_profile
    )

    # タスクをクローズしてから feedback.md を書き込む
    # （書き込み前にクラッシュしても同タスクを再処理しない）
    for task in positive_tasks + negative_tasks:
        await kanboard.add_comment(task, f"Learned from feedback: {summary}")
        await kanboard.close_task(task)

    write("data/feedback.md", new_profile)
```

#### Phase 2: フィード取得・統合評価・Inbox 更新

新着アイテムと既存 Inbox タスクをまとめて評価し、クローズと作成を一括で決定する。

```
# 最終参照日時以降の新着アイテムを取得
all_new_items = []
for feed in feeds:
    last_fetched = state.get_last_fetched(feed.name)
    items = await fetcher.fetch(feed, since=last_fetched)
    state.update_last_fetched(feed.name, now())  # フェッチ直後に更新（クラッシュ時の重複取得を防ぐ）
    all_new_items.extend(items)

# 既存 Inbox タスクと新着アイテムを統合評価
inbox_tasks = await kanboard.get_tasks(Inbox)
scored = await llm.evaluate_importance(all_new_items + inbox_tasks)
ranked = sorted(scored, key=lambda x: x.score, reverse=True)

top      = ranked[:config.max_inbox_items]
to_close = [x for x in ranked[config.max_inbox_items:] if isinstance(x, KanboardTask)]
to_create = [x for x in top if isinstance(x, FeedItem)]

# 劣後した既存アイテムをクローズ
for task in to_close:
    await kanboard.add_comment(task, "Closed: ranked below inbox limit")
    await kanboard.close_task(task)

# 上位の新着アイテムを並列でカード生成・作成（失敗したアイテムは無視）
cards = await asyncio.gather(*[
    llm.generate_card(item, item.feed.instruction) for item in to_create
], return_exceptions=True)
valid_cards = [c for c in cards if not isinstance(c, BaseException)]
await asyncio.gather(*[
    kanboard.create_task(card) for card in valid_cards
], return_exceptions=True)

# Inbox 全体をスコア順にソート（新規作成分を含め再取得）
score_map = {s.url: s.score for s in scored if isinstance(s, FeedItem)}
score_map |= {s.id: s.score for s in scored if isinstance(s, KanboardTask)}
inbox_final = await kanboard.get_tasks(Inbox)
inbox_sorted = sorted(inbox_final,
    key=lambda t: score_map.get(t.id, score_map.get(t.external_link, 0)),
    reverse=True)
await asyncio.gather(*[
    kanboard.update_task_position(task.id, task.column_id, position)
    for position, task in enumerate(inbox_sorted)
])

```

---

## 5. カードフォーマット

以下のフィールドは surfbot が常に固定値で設定する：

| Kanboard フィールド | 設定値 |
|---|---|
| `external_link` | フィードアイテムの URL |
| `date_started` | フィードアイテムの公開日時 |

それ以外のフィールドは `generate_card` が JSON オブジェクトとして返す。LLM は `format.md` と `feeds.yaml` の指示に従い、Kanboard がサポートする任意のフィールドを自由に設定できる（title、description、tags、color_id、date_due など）。

surfbot は LLM の出力を `createTask` に渡す前に **ホワイトリスト検証**を行う。許可フィールドは以下の通りで、それ以外のキーは除去する。

| 許可フィールド |
|---|
| `title` |
| `description` |
| `tags` |
| `color_id` |
| `date_due` |
| `priority` |
| `score` |

---

## 6. 技術スタック

| 用途 | ライブラリ / サービス |
|---|---|
| 言語 | Python 3.12+ |
| LLM | `claude` CLI（`asyncio.create_subprocess_exec` でサブプロセス呼び出し） |
| Kanboard | JSON-RPC over HTTP（`httpx` による非同期リクエスト） |
| フィード取得 | `feedparser` + `httpx`（本文取得時） |
| 設定読み込み | `PyYAML` + `pydantic` |
| パッケージ管理 | `uv` / `pyproject.toml` |

---

## 7. 設定ファイルスキーマ（参考）

### `config.yaml`

```yaml
kanboard:
  url: "https://kanboard.example.com/jsonrpc.php"
  api_token: "xxxxx"
  project_id: 1

cycle_interval_minutes: 60
max_inbox_items: 30
```

### `feeds.yaml`

```yaml
- name: example-rss
  url: "https://example.com/feed.rss"
  fetch_content: false
  instruction: |
    Use the original English title as-is.

- name: another-atom
  url: "https://another.com/atom.xml"
  fetch_content: true
  instruction: ""
```

`name` はフィードを一意に識別するキー。URL を変更しても参照履歴が引き継がれる。

### `state.yaml`（surfbot が自動管理）

フィードごとの最終参照日時を記録する。キーは `feeds.yaml` の `name`。サイクル完了後に更新される。

```yaml
last_fetched:
  example-rss: "2026-05-04T12:00:00+00:00"
  another-atom: "2026-05-03T08:30:00+00:00"
```

### `feedback.md`（surfbot が自動管理）

surfbot が Positive/Negative 処理のたびに LLM でプロファイル全文を書き直す。ユーザーが手動で編集しても構わない。初回は空ファイルから始まり、フィードバックが蓄積されるにつれて精度が上がっていく。

`preferences.md` と `feedback.md` が矛盾する場合は **`feedback.md` を優先**する。重要度評価プロンプトでは `feedback.md` を `preferences.md` の後に挿入し、上書き指示として機能させる。

```markdown
## User Interest Profile

### Interested in
- Technical deep-dives on programming languages (especially Rust, Go): performance comparisons, benchmarks
- Release notes and architecture write-ups for open source projects
- Technical analysis of security vulnerabilities

### Not interested in
- Entertainment, gossip, sports content
- Marketing-heavy press releases
```
