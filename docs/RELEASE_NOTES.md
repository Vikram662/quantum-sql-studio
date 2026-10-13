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
