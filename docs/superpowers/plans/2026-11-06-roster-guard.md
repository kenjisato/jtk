# 対応表の ID 埋めと jtk guard（Issue #1）

出典: https://github.com/kenjisato/jtk/issues/1 。パスは Issue のコメントの読み替えに合わせて直した（パッケージは `src/jtk/`、テストは `tests/` 直下、fixture は `tests/conftest.py`）。

卒論・修論の管理フォルダ（Thesis）で、学生の氏名を Claude（Anthropic）に送らずに作業するための jtk 側の改修です。Thesis 側の作業（移行・取り込み・記録の消去）はこの改修の後に別に進めます。

## 設計（jtk に関わる部分）

前提：送られるのは Claude が読んだものと書いたものだけ。名前を扱う処理は、名前を含まないコードにさせ、佐藤が Claude Code の外のターミナルで実行する。守りは二重（対応表の暗号化 ＋ PreToolUse フック）。


### ID の作り方

- 既存の `jtk table pii hash` と同じ方式にする。親鍵で鍵付きハッシュを作り、入力はメールアドレス、スコープは `Thesis`。
- ID は jtk が出す 16 桁の 16 進数をそのまま使う（例: `6ab75a450b998f0b`）。短くはしない。作り方を一つにして、取り違えを防ぐため（Claude の判断）。
- 同じメールアドレスでも、スコープが違えば授業の ID とは別の値になる。

### 対応表を扱う危険な操作

対応表は列 `学年, 姓, 名, メールアドレス, 別表記, 呼称, ID` の xlsx で、Excel で開けるパスワード付き（Office の標準の暗号化）にする。行は佐藤が Excel で書き足し、ID の列は jtk が埋める。

列の決めごと（Claude の判断）：
- 日本語の氏名は機械では確実に分けられないので、姓と名を別の列にする。フルネームは「姓＋名」とし、空白なし・半角空白・全角空白の 3 通りを名前とみなす。
- 「呼称」は、メモや会話で使う短い呼び名（例: `Aさん`）。佐藤が決めて書く。学年の中で重ならないようにする。実名の頭文字は使わない（Claude の判断。頭文字からも名前の手がかりが漏れるため）。
- 「別表記」は任意。読み（ひらがな・カタカナ）やローマ字など、名前として扱いたい書き方を `、` で区切って書く。元のファイル名にローマ字が入っている場合などに使う。

必要な操作は二つ。コマンド名とフラグ名は builder に任せるが、意図しなければ打たない名前にする（例: `--danger`）。

1. **ID を埋める**：暗号化された対応表を開き、ID が空の行にだけ ID を入れて、暗号化したまま保存する。主に使うのはこちら。
2. **新しく作る**：平文の表（学年・氏名・メールアドレス）から、暗号化された対応表を作る。最初の 1 回用。佐藤が Excel で直接パスワード付きの表を作って 1 を使うなら、なくてもよい。

どちらの操作にも、次の守りを付ける。

- **人の入力が要る**：標準入力が端末でなければ、何もせずに止める（パイプや AI エージェントの Bash からの実行を想定）。確認として、出力先のファイル名を打ち直してもらう。パスワードは画面で入力し（`getpass` など）、コマンドの引数や環境変数では受け取らない。
- **出力先を必須にする**：上書きの扱いは、既存の `--force` の流儀に合わせる。
- **フックの確認**：書き出す前に、出力先を含むプロジェクトの `.claude/settings.json`（と `settings.local.json`）に設定された PreToolUse フックを実際に呼ぶ。偽の呼び出しを少なくとも次の分だけ渡し、全部拒まれることを確かめる。一つでも通れば、理由を表示して止める。
  - 出力先を Read する
  - 出力先を `cat` する Bash
  - 出力先を含むフォルダへの Grep
- **平文を残さない**：途中で平文の一時ファイルを作るなら OS の一時フォルダに置き、終わったら消す。OneDrive の同期フォルダには置かない。
- **表示**：件数と ID だけを出し、名前とメールアドレスは出さない。
- パスワードは Bitwarden に保存する。親鍵とは別のパスワードにする。

### `jtk guard`（PreToolUse フック）

- Claude Code の PreToolUse フックとして呼ばれ、標準入力の JSON を見て、守る対象に触れる呼び出しを拒む。
- 守る対象は、プロジェクトの設定ファイルにパスと glob で書く（形式は builder に任せる。例: `.claude/jtk-guard.toml`）。Thesis では次を守る。
  - 対応表
  - 提出物の原本（学年フォルダの下の `*.docx`・`*.pdf`）
  - 受け取り口 `Thesis/_inbox/` の下のすべて（フォルダ単位で拒む）。LMS から落とすファイルの名前や形は、Moodle の版や大学の設定、落とし方で変わる。フォルダ名に氏名が入ることもあるので、ファイル名では守らない
- 守りの範囲は、Thesis で起動した Claude Code だけにする。他のフォルダで起動したセッションまでは守らない（Claude の判断。消去の範囲を「このフォルダだけ」とした佐藤の判断に合わせた）。
- 拒む呼び出しは次のとおり。
  - Read・Edit・Write・MultiEdit・NotebookEdit：`file_path` が対象に当たるもの
  - Grep・Glob：`path` や `pattern` が対象に当たるもの
  - Bash・PowerShell：コマンドの文字列が対象のファイル名を含むもの、危険な操作のフラグを含むもの
- 拒むときの理由の文には、名前を含めない。
- 限界として、文字列の照合はワイルドカードやスクリプト経由ですり抜けられる。暗号化と組み合わせる前提なので、それで足りるとする。
- 他の PC でも動くように、jtk が入っていなければフックは「jtk がないので守れません」と表示して拒む側に倒す。



## 実装計画


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** jtk に、暗号化された対応表（名簿）の読み書きと ID 埋め（`jtk table roster fill`）を足す。あわせて、Claude Code の PreToolUse フック `jtk guard` と、そのフックが効いているかの検査を足す。

**Architecture:** 対応表は Office 標準の暗号化をかけた xlsx とする。復号も暗号化もメモリ上（`BytesIO`）で行い、平文をディスクに書かない。ID は既存の `crypto.hash_email` で作る。`jtk guard` は、プロジェクトの `.claude/jtk-guard.toml` に書かれた守る対象への呼び出しを、終了コード 2 で拒む。`fill` は書き出す前にプロジェクトのフック設定を実際に呼び、守られていることを確かめる。

**Tech Stack:** Python 3.11+、typer、openpyxl、keyring（既存）。msoffcrypto-tool（新規、5.1 以上。OOXML の暗号化を使う）。

**Spec:** この Issue の「設計」の節。実行するときは、計画を `docs/superpowers/plans/` に写す。

**Where:** https://github.com/kenjisato/jtk を手元に clone し、ブランチ `feat/roster-guard` で作業する。パッケージの置き場（`src/jtk/` か `jtk/` か）とテストの置き場は、リポジトリの今の形に合わせる。以下のパスはパッケージからの相対パスで書く。

## Global Constraints

- テストは架空のデータだけで行う。実在の学生の名前やメールアドレスを、コード・テスト・コミットに入れない。
- 例外文と表示には、セルの値（名前・メールアドレス）を入れない。出してよいのは行番号・列名・件数・ID だけ。既存の `JtkError` の約束に従う。
- 対応表の列名は `学年, 姓, 名, メールアドレス, 別表記, 呼称, ID`。この順で、この名前にする。
- ID は `crypto.hash_email(derive_scope_key(master, scope), email)`（16 桁の 16 進数）。Thesis のスコープは `Thesis`。
- パスワードは画面での入力だけで受け取る。引数・環境変数・設定ファイルでは受け取らない。
- 危険な操作は `--danger` を付けないと動かない。標準入力が端末でなければ、何もせずに止める。
- バージョンを 0.2.0 にし、タグ `v0.2.0` を打つ。Thesis 側はこのタグを依存に書く。

## Review Focus

1. **Git Bash（mintty）での実行**：`sys.stdin.isatty()` が False になる。「PowerShell か Windows Terminal で実行してください」と表示して止まることが期待される。→ Task 2 のテスト `test_require_tty_message_mentions_powershell`。
2. **Excel で対応表を開いたまま `fill` を実行する**：書き込めない。名前を含まない文で止まり、元のファイルは壊れないことが期待される。→ Task 3 のテスト `test_save_roster_keeps_original_on_failure`。
3. **ID 列にすでに値があり、メールアドレスを後から直した行**：黙って上書きせず、行番号つきで止まることが期待される。→ Task 3 のテスト `test_fill_rejects_mismatched_id`。
4. **パスワードの打ち間違い**：「パスワードが違います」とだけ出て止まることが期待される。→ Task 2 のテスト `test_open_roster_wrong_password`。
5. **OneDrive が作る競合コピー（`student-ids-PC名.xlsx`）**：守る対象をフォルダ単位（`private/**`）で書いておけば拒まれることが期待される。→ Task 4 のテスト `test_guard_denies_any_file_under_protected_dir`。

---

### Task 1: 依存の追加と暗号化の入出力

**Files:**
- Modify: `pyproject.toml`（依存に `msoffcrypto-tool>=5.1` を足す。version を `0.2.0` にする）
- Create: `src/jtk/table/secure_io.py`
- Test: `tests/test_secure_io.py`

**Interfaces:**
- Produces:
  - `read_encrypted_xlsx(path: Path, password: str) -> Table`：最初のシートを読む。空行の扱いと行番号は `io._build` と同じにする。
  - `write_encrypted_xlsx(table: Table, path: Path, password: str) -> None`：同じフォルダの一時ファイルに暗号化したものを書き、`os.replace` で置き換える。平文は `BytesIO` の中だけに置く。
  - `WrongPassword(JtkError)`：メッセージは `"パスワードが違います"`。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_roundtrip(tmp_path):
    t = Table(["学年", "姓"], [["B25", "架空"]], [2])
    p = tmp_path / "r.xlsx"
    write_encrypted_xlsx(t, p, "pw")
    assert read_encrypted_xlsx(p, "pw").rows == [["B25", "架空"]]

def test_file_is_not_plain_zip(tmp_path):
    p = tmp_path / "r.xlsx"
    write_encrypted_xlsx(Table(["姓"], [["架空"]], [2]), p, "pw")
    assert not zipfile.is_zipfile(p)          # 暗号化された xlsx は OLE 形式
    assert "架空".encode() not in p.read_bytes()

def test_wrong_password(tmp_path):
    p = tmp_path / "r.xlsx"
    write_encrypted_xlsx(Table(["姓"], [["架空"]], [2]), p, "pw")
    with pytest.raises(WrongPassword):
        read_encrypted_xlsx(p, "nope")

def test_plain_xlsx_is_rejected(tmp_path):
    # パスワードなしの xlsx は対応表として受け付けない
    p = tmp_path / "plain.xlsx"; write_table(Table(["姓"], [["架空"]], [2]), p)
    with pytest.raises(JtkError, match="パスワードがかかっていません"):
        read_encrypted_xlsx(p, "pw")
```

- [ ] **Step 2: テストを実行し、失敗を確かめる**

Run: `uv run pytest tests/test_secure_io.py -v`
Expected: FAIL（`secure_io` がない）

- [ ] **Step 3: `secure_io.py` を実装する**

復号は `msoffcrypto.OfficeFile(fp)`、`is_encrypted()`、`load_key(password=...)`、`decrypt(BytesIO)` で行い、`openpyxl.load_workbook(BytesIO, data_only=True)` で読む。暗号化は、openpyxl で `BytesIO` に保存してから `msoffcrypto.format.ooxml.OOXMLFile(buf).encrypt(password, out)` で行う。msoffcrypto の鍵エラーは `WrongPassword` に変える。その他の例外は、既存の `io.read_table` と同じく値を連結しない `JtkError` にする。

- [ ] **Step 4: テストを実行し、通ることを確かめる**

Run: `uv run pytest tests/test_secure_io.py -v`
Expected: PASS（4 件）

- [ ] **Step 5: 書き出したファイルが Excel で開けることを確かめる（佐藤）**

`uv run python -c "..."` で架空の 1 行を `pw` で書き出し、佐藤が Excel で開く。パスワードを聞かれて中身が見えれば合格。逆に、佐藤が Excel でパスワードを付けて保存した架空の表が `read_encrypted_xlsx` で読めることも確かめる。

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/jtk/table/secure_io.py tests/test_secure_io.py
git commit -m "feat: 暗号化された xlsx の読み書き"
```

### Task 2: 対応表のモデルと、人の入力の確認

**Files:**
- Create: `src/jtk/roster.py`
- Test: `tests/test_roster.py`

**Interfaces:**
- Consumes: Task 1 の `read_encrypted_xlsx` と `WrongPassword`
- Produces:
  - `COLUMNS = ["学年", "姓", "名", "メールアドレス", "別表記", "呼称", "ID"]`
  - `@dataclass class Student: cohort: str; family: str; given: str; email: str; variants: list[str]; alias: str; id: str; row: int`
    - `variants` は「別表記」を `、` と `,` で区切り、前後の空白を除いたもの。`id` が空なら `""`。
  - `open_roster(path: Path, password: str) -> list[Student]`
  - `require_tty() -> None`：`sys.stdin.isatty()` が False なら、`JtkError("この操作は人の入力が要ります。PowerShell か Windows Terminal で実行してください")` を投げる。
  - `prompt_password(confirm: bool = False) -> str`：`require_tty()` のあと `getpass.getpass("対応表のパスワード: ")` で受け取る。`confirm=True` なら 2 回聞いて照合する。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_open_roster_parses_columns(roster_file):        # fixture: 架空 2 行を "pw" で暗号化
    s = open_roster(roster_file, "pw")
    assert s[0].family == "架空" and s[0].variants == ["かくう", "Kakuu"] and s[0].id == ""

def test_open_roster_missing_column(tmp_path): ...       # 「呼称」の列がない → JtkError("列 呼称 がありません")
def test_open_roster_wrong_password(roster_file):
    with pytest.raises(WrongPassword): open_roster(roster_file, "x")
def test_require_tty_message_mentions_powershell(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(JtkError, match="PowerShell"): require_tty()
```

- [ ] **Step 2: テストを実行し、失敗を確かめる**

Run: `uv run pytest tests/test_roster.py -v`
Expected: FAIL

- [ ] **Step 3: `roster.py` を実装する**

- [ ] **Step 4: テストを実行し、通ることを確かめる**

Run: `uv run pytest tests/test_roster.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jtk/roster.py tests/test_roster.py tests/conftest.py
git commit -m "feat: 対応表のモデルとパスワード入力"
```

### Task 3: ID を埋める処理と保存

**Files:**
- Modify: `src/jtk/roster.py`
- Test: `tests/test_roster.py`

**Interfaces:**
- Consumes: Task 1・2
- Produces:
  - `fill_ids(table: Table, scope_key: bytes) -> list[str]`：「ID」が空の行に ID を入れ、入れた ID の一覧を返す（`table` をその場で書き換える）。
  - `save_roster(table: Table, path: Path, password: str) -> None`：`write_encrypted_xlsx` を呼ぶ。失敗したら元のファイルを残す。

検査は次のとおり。どれも、行番号だけを含むメッセージで止まる。
- メールアドレスが空の行
- 同じメールアドレスが複数の行にある
- ID が入っている行で、メールアドレスから計算した値と食い違う（`"行 {n} の ID がメールアドレスと合いません"`）
- 同じ学年に同じ呼称がある
- 呼称が空の行

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_fill_only_empty(table_two_rows, scope_key):     # 1 行目は正しい ID 済み、2 行目は空
    filled = fill_ids(table_two_rows, scope_key)
    assert filled == [hash_email(scope_key, "kakuu2@example.com")]

def test_fill_rejects_mismatched_id(table_two_rows, scope_key):
    table_two_rows.rows[0][6] = "0000000000000000"
    with pytest.raises(JtkError, match="行 2 の ID がメールアドレスと合いません"):
        fill_ids(table_two_rows, scope_key)

def test_fill_rejects_duplicate_alias_in_cohort(...): ...
def test_fill_messages_have_no_values(...):               # どのエラーにも 姓・名・メールの値が入らない
def test_save_roster_keeps_original_on_failure(tmp_path, monkeypatch):
    # os.replace が PermissionError を投げる（Excel で開いている状態）→ JtkError、元のバイト列は変わらない、一時ファイルは残らない
```

- [ ] **Step 2: テストを実行し、失敗を確かめる**

Run: `uv run pytest tests/test_roster.py -v`
Expected: FAIL

- [ ] **Step 3: `fill_ids` と `save_roster` を実装する**

- [ ] **Step 4: テストを実行し、通ることを確かめる**

Run: `uv run pytest tests/test_roster.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jtk/roster.py tests/test_roster.py
git commit -m "feat: 対応表の ID を埋める"
```

### Task 4: `jtk guard`

**Files:**
- Create: `src/jtk/guard.py`
- Modify: `src/jtk/cli.py`（`app.command("guard")` を足す）
- Test: `tests/test_guard.py`

**Interfaces:**
- Produces:
  - `@dataclass class GuardConfig: root: Path; paths: list[str]; command_words: list[str]`
  - `load_guard_config(start: Path) -> GuardConfig | None`：`start` から親へたどって `.claude/jtk-guard.toml` を探す。`root` はその `.claude` の親。
  - `decide(payload: dict, config: GuardConfig) -> str | None`：拒むなら理由を返し、通すなら `None` を返す。
  - CLI `jtk guard`：標準入力の JSON を読み、`payload["cwd"]` から設定を探す。拒むなら理由を stderr に書いて終了コード 2、通すなら 0。設定が見つからなければ 0。

設定の形は次のとおり。

```toml
[jtk.guard]
paths = ["private/**", "_inbox/**", "B25/**/*.docx", "B25/**/*.pdf", "M25/**/*.docx", "M25/**/*.pdf"]
command_words = ["student-ids", "_inbox", "private/", "private\\", ".docx", ".pdf", "--danger"]
```

判定は次のとおり。パスは `root` からの相対パスにし、`/` にそろえ、大文字小文字を区別しない（Windows のため）。`**` を含む glob は `PurePosixPath.full_match`（3.13）か自前の照合で扱う。3.11 で動く方を選ぶ。

| tool_name | 見る値 | 拒む条件 |
|---|---|---|
| Read, Edit, Write, MultiEdit, NotebookEdit | `file_path` / `notebook_path` | `paths` に当たる |
| Grep, Glob | `path` | `paths` に当たる。または、`X/**` の形の対象について、その `X` 自身か `X` の下である |
| Grep, Glob | `pattern`, `glob` | `command_words` のどれかを含む |
| Bash, PowerShell | `command` | `command_words` のどれかを含む（大文字小文字を区別しない） |

理由の文は `"jtk guard: 守られたファイルです（{相対パスか、当たった語}）"` にする。名前は、相対パスにも語にも入らない（学生フォルダは ID の名前になっている前提）。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_guard_denies_read_of_roster(cfg): assert decide({"tool_name":"Read","tool_input":{"file_path":str(cfg.root/"private/student-ids.xlsx")}}, cfg)
def test_guard_denies_any_file_under_protected_dir(cfg): ...   # private/student-ids-PC1.xlsx も拒む
def test_guard_denies_grep_on_protected_dir(cfg): ...          # path = root/"private"
def test_guard_allows_txt_in_student_dir(cfg): ...             # B25/abcd/2026-09_研究計画書.txt は通す
def test_guard_denies_bash_cat(cfg): ...                       # "cat private/student-ids.xlsx"
def test_guard_denies_danger_flag(cfg): ...                    # "jtk table roster fill x --danger"
def test_guard_windows_paths_case_insensitive(cfg): ...        # "C:\\...\\PRIVATE\\student-ids.xlsx"
def test_cli_exit_code_2_on_deny(cli_runner, cfg): ...
def test_cli_exit_0_without_config(cli_runner, tmp_path): ...
```

- [ ] **Step 2: テストを実行し、失敗を確かめる**

Run: `uv run pytest tests/test_guard.py -v`
Expected: FAIL

- [ ] **Step 3: `guard.py` と CLI を実装する**

- [ ] **Step 4: テストを実行し、通ることを確かめる**

Run: `uv run pytest tests/test_guard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jtk/guard.py src/jtk/cli.py tests/test_guard.py
git commit -m "feat: Claude Code の PreToolUse フック jtk guard"
```

### Task 5: フックが効いているかの検査

**Files:**
- Modify: `src/jtk/guard.py`
- Test: `tests/test_guard_verify.py`

**Interfaces:**
- Consumes: Task 4
- Produces:
  - `verify_guard(target: Path) -> list[str]`：失敗の説明の一覧を返す。空なら合格。

手順は次のとおり。
1. `target` から親へたどって `.claude/settings.json` を探す（`settings.local.json` もあれば合わせる）。なければ「`.claude/settings.json` がありません」。
2. `hooks.PreToolUse` のうち、`matcher` が `Read`・`Bash`・`Grep` に当たるもの（`*`、空、正規表現）のコマンドを集める。一つもなければ失敗。
3. 次の 3 つの偽の呼び出しを、集めたコマンドに順に渡す（`input=json, cwd=root, timeout=20`、環境変数 `CLAUDE_PROJECT_DIR=root`）。コマンドは Claude Code と同じシェルで動かす。Windows では Git Bash の `bash -c`（`shutil.which("bash")`、なければ `C:\Program Files\Git\bin\bash.exe`）、それ以外では `/bin/sh -c` を使う。`shell=True` は使わない（Windows では cmd.exe になり、bash の書き方のフックを正しく動かせないため）。どれかのコマンドが終了コード 2 を返すか、stdout の JSON に `permissionDecision: "deny"` があれば、その呼び出しは拒まれたとみなす。
   - `{"tool_name":"Read","tool_input":{"file_path": target}, "cwd": root, "hook_event_name":"PreToolUse"}`
   - `{"tool_name":"Bash","tool_input":{"command": f"cat \"{target}\""}, ...}`
   - `{"tool_name":"Grep","tool_input":{"pattern":"a","path": target.parent}, ...}`
4. 拒まれなかった呼び出しごとに、「Read が拒まれません」のような説明を返す。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_verify_passes_with_guard_hook(project_with_guard): assert verify_guard(project_with_guard/"private/r.xlsx") == []
def test_verify_fails_without_settings(tmp_path): ...
def test_verify_fails_when_hook_allows(project_with_noop_hook): ...      # hook command が `exit 0`
def test_verify_fails_when_target_not_in_paths(project_with_guard): ...  # 守る対象の外へ書こうとした
```

フックのコマンドには、テスト環境で確実に動く `"{sys.executable}" -m jtk guard` を使う（`src/jtk/__main__.py` を足す）。

- [ ] **Step 2: テストを実行し、失敗を確かめる**

Run: `uv run pytest tests/test_guard_verify.py -v`
Expected: FAIL

- [ ] **Step 3: `verify_guard` と `src/jtk/__main__.py` を実装する**

- [ ] **Step 4: テストを実行し、通ることを確かめる**

Run: `uv run pytest tests/test_guard_verify.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jtk/guard.py src/jtk/__main__.py tests/test_guard_verify.py
git commit -m "feat: 出力先がフックで守られているかを検査する"
```

### Task 6: `jtk table roster fill`

**Files:**
- Modify: `src/jtk/table/cli.py`（`roster_app` を足し、`app.add_typer(roster_app, name="roster")`）
- Modify: `README.md`（使い方を足す）
- Test: `tests/test_roster_cli.py`

**Interfaces:**
- Consumes: Task 2・3・5
- Produces: `jtk table roster fill ROSTER --scope SCOPE --danger`

処理の順番は次のとおり。
1. `--danger` がなければ、「対応表を開く危険な操作です。意図して実行するなら --danger を付けてください」と表示して終了コード 1。
2. `require_tty()` を呼ぶ。
3. `verify_guard(ROSTER)` を呼び、失敗があれば一覧を表示して止める。
4. 確認として、`typer.prompt` でファイル名（`ROSTER.name`）を打ち直してもらう。違えば止める。
5. `prompt_password()` でパスワードを受け取り、`read_encrypted_xlsx` で読む。
6. `fill_ids` を呼び、`save_roster` で保存する。
7. 表示は、埋めた件数、埋めた ID の一覧、スコープ、鍵の指紋。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_fill_requires_danger(cli_runner, roster_in_guarded_project): ...   # exit 1、ファイルは変わらない
def test_fill_requires_tty(cli_runner, ..., monkeypatch): ...               # CliRunner は端末ではない → PowerShell の文
def test_fill_stops_when_unguarded(cli_runner, roster_in_plain_dir, monkeypatch): ...  # isatty を True にしても verify で止まる
def test_fill_end_to_end(cli_runner, roster_in_guarded_project, monkeypatch, fake_master_key):
    # isatty=True、prompt でファイル名、getpass で "pw" → 2 行目に ID が入り、出力に ID はあるが姓・メールはない
```

- [ ] **Step 2: テストを実行し、失敗を確かめる**

Run: `uv run pytest tests/test_roster_cli.py -v`
Expected: FAIL

- [ ] **Step 3: コマンドを実装し、README に使い方を足す**

- [ ] **Step 4: 全部のテストを実行する**

Run: `uv run pytest -v`
Expected: PASS（既存のテストを含めてすべて）

- [ ] **Step 5: Commit・タグ・インストール**

```bash
git add src/jtk/table/cli.py README.md tests/test_roster_cli.py
git commit -m "feat: jtk table roster fill"
git tag v0.2.0
git push origin feat/roster-guard --tags   # main への取り込みは佐藤が決める
uv tool install --force git+https://github.com/kenjisato/jtk@v0.2.0
```

- [ ] **Step 6: 佐藤の端末で動きを確かめる**

架空の対応表を、guard を設定した一時プロジェクトに置く。佐藤が PowerShell で `jtk table roster fill ... --scope Thesis --danger` を実行し、ID が入ることを確かめる。Claude の Bash から同じコマンドを実行すると止まることも確かめる。止まる理由は、`--danger` を含むので guard が拒む、または端末でないこと。

