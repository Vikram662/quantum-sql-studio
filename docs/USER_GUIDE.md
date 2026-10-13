# Quantum SQL — User Guide

Quantum SQL is a desktop client for MySQL and MariaDB. It runs on Windows, macOS and Linux.

---

## 1. Install

| System | What to download | How to start |
|---|---|---|
| **Windows 10 / 11** | `QuantumSQL_Setup_<version>.exe` | Run the installer, then start *Quantum SQL* from the Start menu. |
| **macOS 12+** (Intel or Apple Silicon) | `QuantumSQL-<version>-macos-arm64.dmg` (Apple Silicon M1–M4) or `…-macos-x86_64.dmg` (Intel) | Open the .dmg and drag **Quantum SQL** to *Applications*. The first time, right-click the app → **Open** → **Open** (the app is not signed by Apple yet). |
| **Linux** (Ubuntu 22.04+, Debian 12+, Fedora 38+) | `QuantumSQL-<version>-linux-x86_64.tar.gz` | `tar xzf QuantumSQL-*.tar.gz` then `./QuantumSQL/QuantumSQL`. On Ubuntu/Debian you may first need `sudo apt install libxcb-cursor0`. |
| **Any system with Python 3.10+** | the source code | `pip install -r requirements.txt` then `python run.py` |

A Windows `.exe` does not run on macOS or Linux. Each system needs its own build, so download the file for your system. Developers can produce all three builds with the steps in [Building for macOS and Linux](#13-building-for-macos-and-linux).

---

## 2. Connect to a server

1. **File → Session manager** (`Ctrl+N`) → **+** to add a session.
2. Fill in host, port, user and password. Leave *Database* empty to see every database.
3. **Environment**
   - *Development*: no extra checks.
   - *Staging*: shown in orange.
   - *Production*: shown in red with a **[PROD]** tag. Every write statement asks for confirmation and runs inside a transaction you can roll back.
4. **SSH tunnel** (optional), for servers that do not expose port 3306:
   - Tick *Connect through an SSH tunnel* and enter the SSH host, user, and a password **or** a private key file (`.pem`, `id_rsa`, `id_ed25519`).
   - *Hostname / IP* above is then the MySQL host **as seen from the SSH server**, usually `127.0.0.1`.
5. **Test connection**, then **Open**.

Passwords, SSH passwords and API keys are stored encrypted:
- **Windows:** with your Windows account (DPAPI).
- **macOS:** with a key held in the Keychain.
- **Linux:** with a key held in the Secret Service (GNOME Keyring / KWallet). If no keychain exists, a private key file is used instead.

---

## 3. Browse and edit data

- Click a table in the tree. The **Data** tab shows its rows.
- Use **WHERE filter**, **Limit** and the paging arrows to move through large tables. Click a column header to sort.
- **Edit a cell:** double-click it, type, and press Enter or click another cell. The change is **saved immediately**.
- **Insert a row:** press `Ins`. The new row is saved when you move to another row.
- **Delete rows:** `Ctrl+Del`. **Set NULL:** `Ctrl+Shift+N`.
- **Undo** (toolbar) reverses the last saved edit, insert or delete on this table. The undo history keeps up to 50 steps per table.

---

## 4. Run queries

Open a query tab with `Ctrl+T` and type your SQL.

| Action | Key |
|---|---|
| Run everything (or only the selected text) | `F9` |
| Run the statement under the cursor | `Ctrl+Enter` |
| Format / beautify SQL | `Ctrl+Shift+F` |
| Autocomplete | `Ctrl+Space` |
| Comment / uncomment lines | `Ctrl+/` |
| Save the query to the library | `Ctrl+D` |

- **Several statements** separated by `;` run one after another. Each `SELECT` opens its own result tab. `DELIMITER $$` blocks for procedures work too.
- **Snippets:** type an abbreviation and press `Tab`. For example `sel`, `ins`, `upd`, `del`, `cnt`, `grp`, `dup`, `ct` (create table), `idx`, `fk`, `proc`, `trg`, `size`. Edit them in **Query → Snippets…**.
- **Parameters:** write `:name` in a query (`WHERE id = :id`). When you run it, a form asks for the value. Numbers and `NULL` are used as-is and text is quoted. Tick *raw* to insert text unchanged.

### Safe mode
- An `UPDATE` or `DELETE` **without a WHERE clause**, a `DROP`, or a `TRUNCATE` is stopped before it runs. You see how many rows would be affected and must confirm.
- **Safe txn** (toolbar): write statements run inside a transaction. Check the result, then click **Commit** to keep it or **Rollback** to undo everything. Note that `CREATE`, `ALTER` and `DROP` commit automatically in MySQL and cannot be rolled back.

---

## 5. Work with results

- **Quick filter** above each result: type any text, or `column:text` to search one column.
- **Select cells** to see Count, Sum, Avg, Min and Max in the status bar, like in Excel.
- **Chart:** choose bar, line or pie, a label column and value columns, then *Save PNG*.
- **Export:** CSV, Excel (.xlsx), JSON, SQL INSERT, Markdown, HTML, or TSV.
- Right-click a cell for *Copy as CSV / JSON / SQL / Markdown*, *View value* (`Shift+Enter`) and *Chart*.

---

## 6. Explain and index advisor

Put the cursor on a `SELECT` and press **Explain**.

- **Visual explain** shows each step of the plan:
  - Red: full table scan.
  - Orange: full index scan.
  - Green: index lookup.
- **Advisor** suggests a `CREATE INDEX` statement when a table is scanned. It also warns about functions on indexed columns (`DATE(created_at) = …`) and leading wildcards (`LIKE '%abc'`).
- **Open in query tab** puts the suggested statement in the editor. Nothing is run until you run it.

---

## 7. Live monitor

**Tools → Live monitor** (`Ctrl+Shift+M`). It works much like *Performance Insights* in AWS RDS.

| Part | What it tells you |
|---|---|
| Tiles | Connections vs. `max_connections`, active sessions, queries/sec, slow queries, buffer-pool hit %, network, rows read, uptime |
| **Database load** chart | Average active sessions over time. *Slice load by* wait state, SQL, application (host), user or database to see who is causing load |
| **Top SQL** | The statements using most of the load, with executions seen and the longest run time. Double-click to open; **Explain** to analyse |
| **Active sessions** | What is running right now. Rows in red have been running longer than 5 s. **Kill query** stops the query; **Kill connection** closes the client |
| **Connections by app** | Every web app, API, cron job or tool grouped by host and user. A warning appears when an app keeps many idle connections open (a connection leak, or no pooling) |
| **Hot tables** | Tables touched by the busiest statements |
| **Statement statistics** | Exact time, calls, rows examined and *no index used* per statement. Needs `performance_schema = ON` (default on RDS / MySQL 8; on XAMPP add it to `my.ini` and restart) |
| **Health advisor** | Recommendations: connection headroom, buffer pool size, slow log, temp tables on disk, joins without indexes, lock waits, tables without a primary key, MyISAM tables |

Monitoring uses its own connection and samples once per interval (1 to 10 s). **Pause** stops it.

---

## 8. AI assistant

Open with `Ctrl+I`, or the **AI** button in a query tab. When a query fails, a **Fix with AI** button appears.

### Modes
- **Write SQL from a description.** Example: “top 10 customers by revenue last month”. You may type in English, Hindi or Hinglish. Answers are always in English.
- **Explain the query.** Uses the selected SQL or the statement under the cursor.
- **Fix the error.** Sends the failed statement and the server's error message.
- **Optimise / suggest indexes.**

Click **Insert SQL into editor** or **Replace editor / selection**. AI-generated SQL is **never run automatically**: you review it and press F9 yourself, and safe mode still applies.

### Choose a provider and model
Click **Provider / model** in the assistant, or use **Tools → AI assistant settings…**.

| Provider | Key | Notes |
|---|---|---|
| **Anthropic — Claude** (default) | console.anthropic.com → API keys | Models: `claude-opus-5-5` (default, best quality), `claude-sonnet-5-5` (faster, cheaper), `claude-haiku-5-5` (fastest, cheapest) |
| **OpenAI — ChatGPT** | platform.openai.com → API keys | Press **Refresh** to list the models your key can use |
| **Google — Gemini** | aistudio.google.com → Get API key | Press **Refresh** to list the available Gemini models |
| **OpenRouter** | openrouter.ai → Keys | One key gives you models from many vendors |
| **Groq** | console.groq.com → API keys | Very fast open models |
| **DeepSeek** | platform.deepseek.com | `deepseek-chat`, `deepseek-reasoner` |
| **Ollama — local** | no key | Install from ollama.com and run `ollama pull qwen2.5-coder`. **Everything stays on your computer.** |
| **Custom OpenAI-compatible** | depends on the server | LM Studio, vLLM, LocalAI, a company gateway… enter its *Base URL* (e.g. `http://localhost:1234/v1`) |

Steps:
1. Pick the **Provider**.
2. Paste the **API key**.
3. Press **Refresh** and choose a **Model**.
4. **Test connection**.
5. **Save**.

You can switch provider or model at any time. Keys are saved per provider. You can also set environment variables instead of saving a key: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY` or `DEEPSEEK_API_KEY`.

### What is sent
The provider receives three things:
- your question
- the selected SQL (or the failed statement and its error)
- the **names** of the current database's tables and columns, with their types, keys and approximate row counts

**Row data is never sent.** With Ollama nothing leaves your machine.

### AI error messages

| Message | What to do |
|---|---|
| *The API key was rejected* | Copy the key again, without spaces, and make sure it belongs to the selected provider. |
| *Model … not found* | Press **Refresh** and choose a model from the list. |
| *Rate limit or quota exceeded* | Wait a minute, or check the billing/credits on the provider's website. |
| *Could not reach …* | Check the internet connection, proxy or VPN. For Ollama, make sure the Ollama app is running. |
| *The model declined this request* | Rephrase the question. |

---

## 9. More tools

- **Command palette** (`Ctrl+Shift+P`): type part of any command, database, table or saved query and press Enter.
- **Search in database** (`Ctrl+Alt+F`): finds a value (email, order number, phone…) in every column of every table. Double-click a hit to open the matching rows.
- **Saved queries:** `Ctrl+D` saves the current query into a folder. The *Saved queries* panel at the bottom opens them. Use **Export / Import** (JSON) to share them with your team.
- **Generate code** (right-click a table): PHP PDO repository, Laravel model and migration, Node.js (mysql2), Python dataclass, TypeScript, Go, or JSON Schema.
- **Generate test data** (right-click a table): realistic names, emails, Indian mobile numbers, cities, prices and dates. Foreign-key columns get existing ids. Preview first, then insert up to millions of rows.
- **Compare schemas** (Tools, or right-click a database): compares two databases, even on different servers, such as local vs. live. Shows what differs and builds the `ALTER` script to bring the target in line. Untick changes you do not want and open the script in a query tab on the target.
- **ER diagram** (right-click a database): tables and foreign keys as a diagram. Drag tables, scroll to zoom, export PNG or SVG.

---

## 10. Keyboard shortcuts

| Key | Action |
|---|---|
| `Ctrl+N` | Session manager |
| `Ctrl+T` / `Ctrl+W` | New / close query tab |
| `F9` / `Ctrl+Enter` | Run all / current statement |
| `Ctrl+Shift+F` | Format SQL |
| `Ctrl+Space` | Autocomplete |
| `Tab` after an abbreviation | Expand snippet |
| `Ctrl+I` | AI assistant |
| `Ctrl+D` | Save query to library |
| `Ctrl+Shift+P` | Command palette |
| `Ctrl+Alt+F` | Search in database |
| `Ctrl+Shift+M` | Live monitor |
| `F5` | Refresh |
| `Ins` / `Ctrl+Del` / `Ctrl+Shift+N` | Insert row / delete rows / set NULL (Data tab) |
| `Shift+Enter` | View / edit cell value |
| `Ctrl+S` | Save SQL file (query tab) or pending changes (Data tab) |

On macOS use `Cmd` instead of `Ctrl`.

---

## 11. Where your data is stored

| System | Folder |
|---|---|
| Windows | `%APPDATA%\QuantumSQL\` |
| macOS | `~/Library/Application Support/QuantumSQL/` |
| Linux | `~/.config/QuantumSQL/` |

The folder holds `sessions.json`, `settings.json`, `history.json` and `saved_queries.json`. To move to another computer, copy `saved_queries.json`, or use Export / Import in the Saved queries panel. Passwords cannot be copied this way because they are encrypted for the current user and computer, so enter them again on the new machine.

---

## 12. Troubleshooting

| Problem | Fix |
|---|---|
| *Can't connect to MySQL server* | Is the server running? Are the host and port right? Does a firewall allow port 3306? For a remote server, try the SSH tunnel. |
| *Access denied for user* | Check the user and password, and that the user may connect from your IP (`'user'@'%'`). |
| Monitor shows *performance_schema OFF* | Sampling mode still works. For exact statistics enable `performance_schema` (needs a restart). |
| *Enable slow log* fails on AWS RDS | Change `slow_query_log` and `long_query_time` in the RDS parameter group instead. |
| macOS: *“Quantum SQL” can't be opened* | Right-click → Open, or run `xattr -dr com.apple.quarantine "/Applications/Quantum SQL.app"`. |
| Linux: *could not load the Qt platform plugin "xcb"* | `sudo apt install libxcb-cursor0 libxkbcommon-x11-0` (Fedora: `sudo dnf install xcb-util-cursor`). |
| The app crashed | Details are written to `error.log` in the data folder above. |

---

## 13. Building for macOS and Linux

PyInstaller cannot cross-compile, so each system builds its own package.

- **Windows:** `powershell -ExecutionPolicy Bypass -File build.ps1` produces the `.exe` and the installer.
- **macOS / Linux:** `./build.sh` (Python 3.10+). It produces a `.dmg` on macOS or a `.tar.gz` on Linux in `dist/`.
- **All three at once, without owning a Mac:** push a tag such as `v2.1.0` to GitHub. The workflow in `.github/workflows/build.yml` builds Windows, macOS and Linux packages and attaches them to the GitHub release.
