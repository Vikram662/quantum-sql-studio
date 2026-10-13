#!/usr/bin/env bash
# Builds Quantum SQL on macOS (dist/QuantumSQL-<version>-macos-<arch>.dmg) or Linux
# (dist/QuantumSQL-<version>-linux-<arch>.tar.gz) with PyInstaller. Windows uses build.ps1.
#   ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
VERSION=$($PY -c "import re;print(re.search(r'APP_VERSION = \"(.+)\"', open('quantumsql/__init__.py').read()).group(1))")
OS=$(uname -s)
ARCH=$(uname -m)
echo "== Quantum SQL $VERSION ($OS $ARCH) =="

$PY -m pip install --quiet -r requirements.txt pillow

# Qt modules the app never uses - keeps the build small. (QtDBus stays on Linux: the xcb plugin needs it.)
EXCLUDE=(PySide6.QtNetwork PySide6.QtQml PySide6.QtQuick PySide6.QtPdf PySide6.QtOpenGL PySide6.QtOpenGLWidgets
         PySide6.QtConcurrent PySide6.QtSql PySide6.QtTest PySide6.QtXml PySide6.QtPrintSupport PySide6.QtHelp
         PySide6.QtUiTools PySide6.QtDesigner tkinter unittest pydoc)
ARGS=()
for m in "${EXCLUDE[@]}"; do ARGS+=("--exclude-module=$m"); done

$PY -m PyInstaller --noconfirm --clean --windowed --onedir \
    --name QuantumSQL \
    --icon app_icon.ico \
    --add-data "app_icon.ico:." \
    --add-data "docs/USER_GUIDE.md:docs" \
    --osx-bundle-identifier com.quantumsql.studio \
    "${ARGS[@]}" \
    run.py

if [ "$OS" = "Darwin" ]; then
    APP="dist/QuantumSQL.app"
    rm -rf "$APP/Contents/Resources/PySide6/Qt/translations" "$APP/Contents/Frameworks/PySide6/Qt/translations" || true
    mv "$APP" "dist/Quantum SQL.app"
    # removing files invalidates PyInstaller's ad-hoc signature; Apple Silicon refuses unsigned code
    codesign --force --deep --sign - "dist/Quantum SQL.app"
    STAGE=$(mktemp -d)
    cp -R "dist/Quantum SQL.app" "$STAGE/"
    ln -s /Applications "$STAGE/Applications"
    hdiutil create -volname "Quantum SQL" -srcfolder "$STAGE" -ov -format UDZO "dist/QuantumSQL-$VERSION-macos-$ARCH.dmg"
    rm -rf "$STAGE"
    echo "Built dist/QuantumSQL-$VERSION-macos-$ARCH.dmg"
else
    rm -rf dist/QuantumSQL/_internal/PySide6/Qt/translations || true
    cp app_icon.ico dist/QuantumSQL/
    tar czf "dist/QuantumSQL-$VERSION-linux-$ARCH.tar.gz" -C dist QuantumSQL
    echo "Built dist/QuantumSQL-$VERSION-linux-$ARCH.tar.gz  (run: ./QuantumSQL/QuantumSQL)"
fi
