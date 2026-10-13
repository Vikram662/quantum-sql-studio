# Quantum SQL 2.0

Desktop client for MySQL / MariaDB, in the style of HeidiSQL and MySQL Workbench, for Windows, macOS and Linux. Built with Python, PySide6 (Qt) and PyMySQL.

End-user documentation: [docs/USER_GUIDE.md](docs/USER_GUIDE.md) (also in the app under **Help → User guide**, F1).

## Features
- **Session manager**: saved connections with colour tags and a Test button. Passwords are encrypted with Windows DPAPI.
- **Object tree**: databases, tables (with sizes), views, procedures, functions and triggers. Includes a filter box and a right-click menu (create, drop, truncate, rename, duplicate, export, generate SQL).
- **Host tab**: databases, processes (with kill and auto refresh), server variables, status, and users (add, drop, change password, view grants).
- **Database tab**: every object with its rows, size, engine and collation; bulk export, empty and drop.
- **Table designer**: columns, primary key, indexes, foreign keys and CREATE code, with an SQL preview before anything is saved.
- **Data tab**: inline editing that saves as soon as you leave the cell, insert and delete rows, set NULL, WHERE filter, server-side sorting and paging. **Undo** reverses any saved edit, insert or delete.
- **Query tabs**: syntax highlighting, autocomplete for tables and columns, run all or just the current statement, Stop, multiple result sets, open and save .sql files.
- **SQL dump export** to .sql, .sql.gz or .zip, and **SQL import** with a progress bar. Grid rows export to CSV, Excel (.xlsx), TSV, JSON, SQL, Markdown or HTML.
- SQL log, query history, dark and light themes.

### Monitoring
- **Live monitor** (Ctrl+Shift+M): database load as average active sessions, sliced by wait state, SQL, application (host), user or database, like RDS Performance Insights. Also shows queries/sec, connections, network, buffer pool hit rate and slow queries.
- **Top SQL** sampled from live sessions (works on every server). With performance_schema on, exact per-statement statistics (calls, total and average time, rows examined, no-index flags) are shown as well.
- **Active sessions** with kill query or connection and Explain. **Connections by app** groups clients by host and user and flags connection leaks. **Hot tables**.
- **Health advisor**: connection headroom, buffer pool size, slow log, temp tables on disk, joins without indexes, lock waits, tables without a primary key, MyISAM tables.

### Safety
- **Environments**: a session can be marked Development, Staging or Production. Production sessions are shown in red and ask before every write.
- **Safe mode**: UPDATE or DELETE without a WHERE clause, DROP and TRUNCATE are caught before they run, with an estimate of the rows affected.
- **Safe transaction**: write statements run inside a transaction. You review the result, then Commit or Rollback.

### Productivity
- **Format SQL** (Ctrl+Shift+F), **snippets** (type `sel`, `ins`, `upd`, `ct`, `dup` … and press Tab), and **query parameters** (`:name` placeholders are asked for when you run).
- **Visual Explain** with an index advisor that suggests `CREATE INDEX` statements and flags functions on columns and leading `%` wildcards.
- **Result tools**: quick filter, Excel-style Sum/Avg/Min/Max of the selected cells in the status bar, and bar, line or pie **charts** with PNG export.
- **Command palette** (Ctrl+Shift+P) to jump to any command, database, table or saved query. **Saved queries** in folders, with JSON import and export for sharing with a team.
- **Search in database** (Ctrl+Alt+F): finds a value in every column of every table.
- **AI assistant** (Ctrl+I): writes SQL from a plain-language description (English, Hindi or Hinglish in, English out), explains queries, fixes errors and suggests optimisations. Choose the provider and model: Anthropic Claude (default `claude-opus-5-5`), OpenAI, Google Gemini, OpenRouter, Groq, DeepSeek, a local Ollama, or any OpenAI-compatible server. Only table and column names are sent, never row data. Generated SQL is inserted into the editor and never run automatically.

### Developer tools
- **Code generator**: PHP PDO repository, Laravel model and migration, Node.js (mysql2), Python dataclass, TypeScript, Go struct and JSON Schema from any table.
- **Test data generator**: realistic names, emails, Indian mobile numbers, cities, prices and dates, plus existing ids for foreign keys.
- **Schema compare & sync**: diff two databases (also across servers) and generate the ALTER script.
- **ER diagram** with draggable tables, exportable to PNG or SVG.
- **SSH tunnel** connections (password or private key) for servers that do not expose the MySQL port.

User data is stored in `%APPDATA%\QuantumSQL\` on Windows, `~/Library/Application Support/QuantumSQL/` on macOS and `~/.config/QuantumSQL/` on Linux.

## Download and first run
Get the installer for your system from [Releases](https://github.com/Vikram662/quantum-sql-studio/releases). The downloads are not code-signed yet, so the first launch shows a warning:
- **Windows**: on "Windows protected your PC", click **More info**, then **Run anyway**.
- **macOS**: on "cannot be opened" or "cannot verify the developer", open **System Settings → Privacy & Security** and click **Open Anyway**, or run `xattr -dr com.apple.quarantine "/Applications/Quantum SQL.app"` once.
- **Linux**: no warning. Extract the .tar.gz and run `QuantumSQL/QuantumSQL` (needs `libxcb-cursor0`).

## Run from source
```
pip install -r requirements.txt
python run.py
```

## Build the exe and the installer
Needs [Inno Setup 6](https://jrsoftware.org/isdl.php) (`winget install JRSoftware.InnoSetup`).
```
powershell -ExecutionPolicy Bypass -File build.ps1
```
- App: `dist\QuantumSQL\QuantumSQL.exe`
- Installer: `Output\QuantumSQL_Setup_<version>.exe`

To release a new version, change `APP_VERSION` in `quantumsql/__init__.py` and run `build.ps1` again. The installer upgrades an existing install in place.

### macOS and Linux
PyInstaller does not cross-compile, so build on each system:
```
./build.sh      # macOS -> dist/QuantumSQL-<version>-macos-<arch>.dmg, Linux -> dist/QuantumSQL-<version>-linux-<arch>.tar.gz
```
Or push a tag such as `v2.1.0` to GitHub. `.github/workflows/build.yml` builds the Windows installer, the macOS .dmg (Apple Silicon and Intel) and the Linux .tar.gz, and attaches them to the release. The macOS build is ad-hoc signed only. For distribution without the "unidentified developer" prompt, sign and notarize it with an Apple Developer ID.

## License
Quantum SQL is free and open source under the [MIT License](LICENSE). It bundles third-party open source components, listed in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

## Code signing policy
Windows builds are being set up for signing through the SignPath Foundation (application pending). Once approved: free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

- Every signed file is built by GitHub Actions ([`.github/workflows/build.yml`](.github/workflows/build.yml)) from the source in this repository.
- Team roles:
  - Authors and committers: [Vikram662](https://github.com/Vikram662)
  - Reviewers (review every pull request from outside contributors): [Vikram662](https://github.com/Vikram662)
  - Approvers (approve every signing request by hand): [Vikram662](https://github.com/Vikram662)

### Privacy
This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it. It connects only to the database servers (and SSH hosts) the user configures. The optional AI assistant sends the user's question and the related schema to the AI provider the user picked, with the user's own API key, and only when the user asks it something. There is no telemetry, analytics or update check. Saved connections and settings stay on the user's computer, with passwords and API keys encrypted.
