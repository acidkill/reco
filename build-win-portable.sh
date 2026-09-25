#!/usr/bin/env bash
# build-win-portable.sh — buduje portable Reco dla Windows 10/11 NA Linuksie.
# Odpowiednik build.ps1, ale bez Windowsa: Windows Python 3.12 + PyInstaller
# działają pod Wine; model small-int8 jest pobierany i trafia do artefaktu.
# Wynik: dist/Reco/ (onedir) oraz dist/Reco-portable-win11-x64.zip.
set -euo pipefail
cd "$(dirname "$0")"

WINEPREFIX="${WINEPREFIX:-$HOME/.wine-reco}"
export WINEARCH=win64 WINEDEBUG="${WINEDEBUG:--all}"
PY='C:\Python312\python.exe'
MODEL_REPO="OpenVINO/whisper-small-int8-ov"
MODEL_DIR="models/whisper-small-int8-ov"
ZIP="dist/Reco-portable-win11-x64.zip"

step() { printf '\n==> %s\n' "$*"; }
w() { WINEPREFIX="$WINEPREFIX" wine "$@"; }

step "0/6 Prefiks Wine + Windows Python"
if [ ! -d "$WINEPREFIX/drive_c/Python312" ]; then
  echo "Brak C:\\Python312 w $WINEPREFIX. Jednorazowo:"
  echo "  WINEPREFIX=$WINEPREFIX WINEARCH=win64 wineboot --init"
  echo "  curl -L -o /tmp/py.exe https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
  echo "  WINEPREFIX=$WINEPREFIX wine /tmp/py.exe /quiet InstallAllUsers=1 PrependPath=0 Include_launcher=0 'TargetDir=C:\\Python312'"
  exit 1
fi
w "$PY" -c "import sys; assert sys.platform == 'win32'; print('WINE-PY OK:', sys.version.split()[0])"

step "1/6 Zależności (koła win_amd64 z requirements.txt + PyInstaller)"
w "$PY" -m pip install --no-input -r requirements.txt "pyinstaller>=6.0"

step "2/6 Model wbudowany: $MODEL_REPO"
if [ ! -f "$MODEL_DIR/openvino_encoder_model.xml" ]; then
  w "$PY" -c "from huggingface_hub import snapshot_download, HfApi; r='$MODEL_REPO'; d='$MODEL_DIR'; snapshot_download(r, local_dir=d); open(d+'/.hf_revision', 'w').write(HfApi().model_info(r).sha or '')"
fi

step "3/6 PyInstaller (reco.spec, onedir)"
w "$PY" -m PyInstaller reco.spec --noconfirm
test -f dist/Reco/Reco.exe

step "4/6 Smoke test artefaktu (--selftest)"
w "dist/Reco/Reco.exe" --selftest && echo "SELFTEST OK (log: %TEMP%\\reco_selftest.txt)" || echo "UWAGA: selftest nie zwrócił 0"

step "5/6 Pakowanie $ZIP"
rm -f "$ZIP"
if command -v zip >/dev/null 2>&1; then
  ( cd dist && zip -qr "Reco-portable-win11-x64.zip" Reco )
elif command -v bsdtar >/dev/null 2>&1; then
  ( cd dist && bsdtar -a --format zip -cf "Reco-portable-win11-x64.zip" Reco )
else
  ( cd dist && python3 -m zipfile -c "Reco-portable-win11-x64.zip" Reco )
fi

step "6/6 Gotowe"
du -sh dist/Reco "$ZIP"
echo "Artefakt: $ZIP — rozpakuj na Windows 11 i uruchom Reco\\Reco.exe."
