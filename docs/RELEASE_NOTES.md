## What's new in 2.0.4

**Import log.** The import window now shows a live log of every statement: rows affected, size, time taken and server warnings. Errors appear in red with their line number and are listed again under the summary at the end. The window stays open when the import finishes, so you can read the log, copy it or save it to a file.

**Progress bar.** The bar now has a gradient in your theme's accent colour, with a soft highlight that moves across it while work is running. The percentage is white over the filled part and stays readable over the empty part.

**Fixes**
- Import errors showed a line number one too low.
- The progress bar kept animating after an import or search failed or was cancelled.
- Cancelling an import wrote an empty error line to the query log.
- Pressing Enter in the import window copied the log instead of pressing Cancel or Close.
- A long error message could stretch the progress window very wide.
- Database search started each new search with the bar at 100%, and showed long times only in seconds.
- Laravel migration code: a `$` or line break in a column comment or default produced invalid PHP.

## What's new in 2.0.3

**Faster import of large SQL files.** Statements start running at once instead of after the whole file has been read and split. Splitting is about 3× faster, and changes are committed every 2 seconds instead of after every statement, which makes files with many small statements about 3× faster. Work done before an error or Cancel is still saved.

**Clearer progress.** The bar follows your position in the file (MB done of total), and the elapsed time keeps counting while a long statement runs, shown as seconds, then minutes, hours and days.

## Download

| System | File |
|---|---|
| Windows 10 / 11 (64-bit) | `QuantumSQL_Setup_<version>.exe` |
| macOS (Apple Silicon) | `QuantumSQL-<version>-macos-arm64.dmg` |
| Linux (x86-64) | `QuantumSQL-<version>-linux-x86_64.tar.gz` |

## First run

Quantum SQL is free and open source, but the downloads are not code-signed yet, so Windows and macOS warn about an unknown publisher the first time. The app is safe to run. The full source code is in this repository and the files are built by GitHub Actions from it.

**Windows**: if "Windows protected your PC" appears, click **More info**, then **Run anyway**.

**macOS**: open the .dmg and drag Quantum SQL to Applications. If macOS says it "cannot be opened" or "cannot verify the developer", either:
- open **System Settings → Privacy & Security**, scroll down and click **Open Anyway**, or
- run this once in Terminal: `xattr -dr com.apple.quarantine "/Applications/Quantum SQL.app"`

**Linux**: no warning. Extract and run:
```
tar xzf QuantumSQL-<version>-linux-x86_64.tar.gz
./QuantumSQL/QuantumSQL
```
If it does not start, install the Qt cursor library: `sudo apt install libxcb-cursor0`.
