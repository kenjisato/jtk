# `pii hash --keep-pii --encrypt` の設計

Issue #1 の対応表の ID 埋めを、専用コマンド（`jtk table roster fill --danger`）ではなく `jtk table pii hash` のオプションで行う。2026-11-06 に佐藤と決めた。

## 考え方

AI とのやりとりに使う hash 値の列を作る。人も見る表なので個人情報の列は残し、そのかわりファイル全体をパスワードで暗号化する。「残すなら暗号化が必須」をコマンドの上で読めるようにする。

## オプション

```
jtk table pii hash student-ids.xlsx --scope Thesis --hash メールアドレス --hash-column ID \
    --keep-pii --encrypt --out student-ids.xlsx --force
```

| オプション | 内容 |
|---|---|
| `--keep-pii` | PII の列を消さず、漏れの検査もしない。`--encrypt` が無ければ何もせずに止まる |
| `--encrypt` | 出力をパスワード付きの xlsx（Office 標準の暗号化）にする。パスワードは画面で入力する。値は取らない |

- `--keep-pii` を付けないときは、今までの `pii hash` と同じ動きをする。`--encrypt` も単独で使える（個人情報を消した表を暗号化する）。
- `--keep-pii` は設定ファイルでは指定できない。コマンドラインでだけ受け付ける。
- パスワードは引数・環境変数・設定ファイル・keyring では受け取らない。Claude が同じ PC で復号できないようにするため（暗号化は、フックが外れたときの二重目の守り）。

## 入力と出力

- 入力は平文でも、パスワード付きの xlsx でもよい。パスワード付きなら画面でパスワードを聞いて開く。`pii drop` でも同じ。
- `--encrypt` は `.xlsx` だけ。入力か出力が `.csv` なら止まる。
- 出力のパスワード：入力がパスワード付きなら、同じパスワードで暗号化する。平文の入力なら、2 回聞いて照合する。
- `--keep-pii --encrypt --force` のときに限り、`--out` を入力と同じファイルにしてよい（対応表を更新するたびに差し替えずに済むように）。このとき `--move-original` は使えない。
- 平文はメモリの中だけに置く。書き出しは同じフォルダの一時ファイル（中身は暗号化済み）から `os.replace` で置き換える。

## `--keep-pii` のときの hash 列

- `--hash-column` の名前の列がすでにあれば、その列の空欄だけを埋める。値が入っていて、メールアドレスから計算した値と食い違う行があれば、行番号だけを出して止まる。
- その列が無ければ、表の右端に足す。メールアドレスの列は残す。
- 入力が xlsx なら、ブックを保ったまま hash のセルだけを書き換える（他のシート・書式・数式・空行を残す）。hash のセルは文字列として書く。
- 埋める行が無ければ、ファイルを書き換えない。

## 守り（`--keep-pii` のとき）

順に確かめ、どれかで止まれば何も書かない。

1. `--encrypt` があるか
2. 標準入力が端末か（Git Bash の mintty では止まる。PowerShell か Windows Terminal で実行するよう案内する）
3. 出力先がプロジェクトの PreToolUse フックで守られているか（`verify_guard`）
4. 確認として、出力先のファイル名を打ち直してもらう

`--encrypt` だけのとき（PII を消す場合）は、2 だけを確かめる（パスワードの入力に端末が要るため）。

## ID の一覧（`--index`）

Claude が知る必要があるのは ID だけで、ID と氏名の対応は要らない。そこで `--keep-pii` の実行で、名前を消した一覧も一緒に書き出せるようにする。

```
jtk table pii hash private\student-ids.xlsx --scope Thesis --hash メールアドレス --hash-column ID \
    --pii 姓 --pii 名 --pii 別表記 --keep-pii --encrypt --out private\student-ids.xlsx --force \
    --index ids.xlsx
```

- `--index PATH` は `--keep-pii` のときだけ使える。
- 一覧は、ID を埋めた後の表から `--pii` の列と `--hash` の列（メールアドレス）を消したもの。上の例なら `学年, 呼称, ID`。平文で書く。形式は拡張子（`.xlsx` / `.csv`）で決める。
- 一覧には漏れの検査をかける（`pii drop` と同じ）。漏れがあれば、対応表も一覧も書かずに止まる。
- 一覧は、埋める行が無くて対応表を書き換えないときも書く。既存のファイルの上書きは `--force` の流儀に従う。入力・`--out` と同じ場所は拒む。
- 書く順番は、検査がすべて済んでから対応表、その後に一覧。

## 表示

件数・列名・スコープ・鍵の指紋だけを出す。名前とメールアドレスは出さない。`--keep-pii` のときは「漏れの検査: しませんでした（--keep-pii）」と出す。

## なくすもの

- `jtk table roster fill` と `--danger`
- 対応表だけに向けた検査（7 つの列の名前、呼称の重複、呼称が空）。`pii hash` は汎用の道具なので、Thesis の列の決まりは持ち込まない
- フックの目印の語は `--danger` から `--keep-pii` に替える（README の例と、そのテスト）

## そのまま使うもの

`secure_io`（暗号化した xlsx の読み書き、ブックを保つ書き換え）、`require_tty` と `prompt_password`、`jtk guard`、`verify_guard`。
