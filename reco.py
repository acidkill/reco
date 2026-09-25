"""
Reco — record microphone + system audio (WASAPI loopback) and transcribe.

Audio capture uses the `soundcard` library (WASAPI):
  • Lists each physical device once (no per-host-API duplicates).
  • Separates microphones from speakers correctly.
  • Records system audio via real WASAPI loopback — does NOT depend on
    "Stereo Mix" being enabled.

Recordings are encoded to MP3 (PyAV/libmp3lame, streamed while recording so
stopping is instant) — ~6-12x smaller than WAV, plenty for
speech/meetings and transcription. Transcription runs locally via OpenVINO
GenAI (Whisper). UI is bilingual (PT/EN), auto-detected from the system.
"""

import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
import threading
import queue
import time
import datetime
import json
import os
import ctypes
import subprocess
import shutil
from fractions import Fraction
from pathlib import Path

IS_FROZEN = getattr(sys, "frozen", False)   # running as a PyInstaller .exe?
APP_NAME    = "Reco"
APP_TITLE   = "Reco"
APP_VERSION = "0.2.0"
GITHUB_REPO = "dosxnjos/reco"

# ── Theme ─────────────────────────────────────────────────────────────────────
# GREEN/AMBER/RED are fixed (VU meter); everything else derives from the chosen
# background + accent via apply_theme(), which auto-picks readable text colors.
GREEN  = "#30A46C"
AMBER  = "#F5A623"
RED_C  = "#E5484D"

DEFAULT_BG     = "#181A1B"
DEFAULT_ACCENT = "#E0825F"

BG = CARD = CARD_H = CARD_A = ACCENT = ACCENT_FG = TEXT = MUTED = SUBTLE = BORDER = ""


def _hex_to_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def _rgb_to_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb)

def _lum(h):
    r, g, b = _hex_to_rgb(h)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255

def _mix(h, target, amt):
    a, b = _hex_to_rgb(h), _hex_to_rgb(target)
    return _rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * amt for i in range(3)))

def _best_fg(bg_hex):
    """Readable text on bg_hex: dark-gray on light colors, white on dark ones."""
    return "#1A1A1C" if _lum(bg_hex) > 0.62 else "#FFFFFF"


def apply_theme(bg_color, accent_color):
    global BG, CARD, CARD_H, CARD_A, ACCENT, ACCENT_FG, TEXT, MUTED, SUBTLE, BORDER
    BG = bg_color
    ACCENT = accent_color
    ACCENT_FG = _best_fg(accent_color)
    if _lum(bg_color) < 0.5:                       # dark background
        TEXT, MUTED, SUBTLE = "#F5F5F7", "#B8BAC6", "#969CA4"
        CARD   = _mix(bg_color, "#FFFFFF", 0.06)
        CARD_H = _mix(bg_color, "#FFFFFF", 0.13)
        BORDER = _mix(bg_color, "#FFFFFF", 0.20)
    else:                                          # light background
        TEXT, MUTED, SUBTLE = "#1A1A1C", "#3C3C44", "#5C5C66"
        CARD   = _mix(bg_color, "#000000", 0.05)
        CARD_H = _mix(bg_color, "#000000", 0.11)
        BORDER = _mix(bg_color, "#000000", 0.18)
    CARD_A = _mix(bg_color, accent_color, 0.22)    # accent-tinted selection


apply_theme(DEFAULT_BG, DEFAULT_ACCENT)

SEG    = ("Segoe UI", 10)
SEG_SM = ("Segoe UI", 9)
SEG_XS = ("Segoe UI", 8)
SEG_SB = ("Segoe UI Semibold", 10)
SEG_LG = ("Segoe UI Semibold", 13)

def default_output_dir() -> Path:
    return Path.home() / "Documents" / "Reco"

# ── Config persistence ─────────────────────────────────────────────────────────
CONFIG_PATH = Path.home() / ".reco_config.json"

_CFG_DEFAULTS: dict = {
    "language":    None,      # "pt" | "en" | "pl" | None -> auto-detect from system
    "bg_color":    DEFAULT_BG,
    "accent_color": DEFAULT_ACCENT,
    # large-v3-turbo, not small: measured 29/07/2026, `small` locks into
    # repetition loops on low-energy windows (221 back-to-back repeats of one
    # phrase, compression 4.44) where turbo peaks at 3 (compression 1.85). Costs
    # ~25% speed and 828 MB, and is worth both. See
    # roadmap/2026-07-29-transcricao-precisa-rapida-e-aec.md § 1.2.
    "model":       "large-v3-turbo",
    "device":      "AUTO",    # OpenVINO device pref: AUTO | NPU | GPU | CPU
    "diarize":     True,      # channel-based diarization (mic = "Eu", system = others)
    "aec":         True,      # cancel PC-audio echo bleeding into the mic
    "output_dir":  None,      # save folder; None -> Documents\Reco
    "mic_device":  None,      # soundcard device id (str)
    "sys_device":  None,      # soundcard speaker id (str)
    "mic_gain":    1.0,       # linear gain applied to the mic channel (0 dB = 1.0)
    "sys_gain":    1.0,       # linear gain applied to the system channel
    # Rascunho de transcrição durante a gravação (Fase 3 do roadmap
    # 2026-07-29-melhoria-transcricao-ao-vivo-vad-diarizacao.md). Default False:
    # ocupa o acelerador durante a reunião inteira; o usuário liga quando quiser.
    "live":        False,
}

# Filename marker identifying a Reco dual-channel (mic + system) recording, so the
# transcribe screen only channel-diarizes / echo-cancels these — never arbitrary
# files. Travels with the file (survives moving); only lost on a manual rename.
RECO_TAG = "reco"

def is_reco_recording(path) -> bool:
    toks = Path(path).stem.lower().replace("-", "_").split("_")
    return RECO_TAG in toks

# Recording format is fixed (not user-configurable): 16 kHz stereo (L=mic,
# R=system) is exactly what transcription + channel diarization + echo
# cancellation need; 96 kbps ABR ≈ 48 kbps/channel keeps files small.
#
# ⚠️ 96, not the 128 this used to say: the old encoder ran LAME in vbr_mtrh, a
# mode that *ignores* the mean bitrate — measured, the files always came out at
# ~92 kbps no matter what the constant said. 96 kbps ABR reproduces that same
# bitrate and spectrum, and now the number actually means something.
OUT_SR = 16000
OUT_CH = 2
MP3_BR = 96

# Video/audio files we can pull an MP3 out of (PyAV decodes all of these).
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".ts"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".wma"}

def is_video(path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS

# Extraction reuses the recording format (16 kHz), but mono — an MP4 has no
# mic/system split to preserve, so a second channel would only double the size.
# 64 kbps mono ≈ the density the recorder writes per channel.
EXTRACT_CH = 1
EXTRACT_BR = 64

# Bump when a default changes in a way that must reach users who already have a
# saved config. Without this, changing _CFG_DEFAULTS is a no-op for everyone who
# has ever opened the app: load_config() lets the saved file win, by design.
CFG_MIGRACAO = 1


def _migra_config(cfg: dict) -> bool:
    """Carry saved configs forward when a default changes. Returns True if the
    file needs rewriting.

    Migration 1 (29/07/2026) — model small → large-v3-turbo and device NPU →
    AUTO. Both old values were defaults nobody chose: the model and device
    selectors had been removed from the UI, so whatever sat in the file got there
    automatically. `small` locks into repetition loops (measured: 221 identical
    repeats on one window) and NPU-first was picked without measuring; AUTO now
    resolves to the iGPU, which is ~55% faster on large-v3-turbo. Anyone who does
    want NPU can select it — the control is back."""
    v = int(cfg.get("_migracao", 0) or 0)
    if v >= CFG_MIGRACAO:
        return False
    if v < 1:
        if cfg.get("model") == "small":
            cfg["model"] = "large-v3-turbo"
        if cfg.get("device") == "NPU":
            cfg["device"] = "AUTO"
    cfg["_migracao"] = CFG_MIGRACAO
    return True


def load_config() -> dict:
    cfg = dict(_CFG_DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        try:
            CONFIG_PATH.replace(CONFIG_PATH.with_suffix(".corrupt"))
        except Exception:
            pass
    except Exception:
        pass
    if _migra_config(cfg):
        try:
            save_config(cfg)
        except Exception:
            pass          # migration re-runs next launch; not worth failing over
    return cfg

def save_config(cfg: dict):
    # Atomic write: temp file + replace, so a crash mid-write can't truncate it.
    try:
        tmp = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(cfg, indent=2, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        pass


def _asset(*parts) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def _icon_file() -> Path | None:
    p = _asset("logo", "logo_symbol_1x1.ico")
    return p if p.exists() else None


def set_dark_titlebar(win):
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


# ── i18n (PT default; EN translations keyed by the PT string) ──────────────────
def _system_lang() -> str:
    """Best-effort UI language from the system locale: 'pt', 'pl' or 'en'."""
    try:
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        primary = lcid & 0x3FF
        if primary == 0x16:          # LANG_PORTUGUESE
            return "pt"
        if primary == 0x15:          # LANG_POLISH
            return "pl"
        return "en"
    except Exception:
        pass
    try:
        import locale
        loc = (locale.getlocale()[0] or "")
        if loc.lower().startswith(("pt", "portug")):
            return "pt"
        if loc.lower().startswith(("pl", "polish")):
            return "pl"
    except Exception:
        pass
    return "en"


LANG = "pt"

_TR_EN = {
    # header / meters
    "MIC": "MIC",
    "SISTEMA": "SYSTEM",
    # buttons
    "Gravar": "Record",
    "Parar": "Stop",
    "Salvar": "Save",
    "Transcrever": "Transcribe",
    "Excluir": "Delete",
    "Transcrever + excluir": "Transcribe + delete",
    "Reproduzir": "Play",
    "Salvar + Transcrever": "Save + Transcribe",
    "Tema:": "Theme:",
    "Fundo": "Background",
    "Destaque": "Accent",
    "Padrão": "Default",
    "Cor de fundo": "Background color",
    "Cor de destaque": "Accent color",
    # links
    "Opções": "Options",
    "Ocultar opções": "Hide options",
    "Transcrever…": "Transcribe…",
    "← Gravar": "← Record",
    # advanced labels
    "Entrada:": "Input:",
    "Saída:": "Output:",
    "Pasta:": "Folder:",
    "Alterar…": "Change…",
    "Pasta de gravações": "Recordings folder",
    "Atualizar dispositivos": "Refresh devices",
    "Processar em:": "Run on:",
    "Idioma:": "Language:",
    "Criar atalho (Ctrl+Shift+R)": "Create shortcut (Ctrl+Shift+R)",
    "Remover atalho": "Remove shortcut",
    "Atalho criado — abra pelo Menu Iniciar ou com Ctrl+Shift+R.":
        "Shortcut created — open from the Start Menu or with Ctrl+Shift+R.",
    "Atalho removido.": "Shortcut removed.",
    "Não foi possível criar o atalho: {e}":
        "Couldn't create the shortcut: {e}",
    "Preparando '{size}' no {dev} pela primeira vez — isso leva alguns minutos "
    "e só acontece uma vez.":
        "Preparing '{size}' on {dev} for the first time — this takes a few "
        "minutes and happens only once.",
    # status — devices
    "Pronto para gravar.": "Ready to record.",
    "Buscando dispositivos…": "Searching for devices…",
    "Erro ao listar dispositivos: {m}": "Error listing devices: {m}",
    "Nenhum dispositivo de áudio encontrado.": "No audio devices found.",
    "Atenção: nenhuma saída de áudio para loopback.":
        "Warning: no audio output available for loopback.",
    "Não é possível atualizar dispositivos durante a gravação.":
        "Can't refresh devices while recording.",
    "Captura indisponível — instale soundcard, numpy e av.":
        "Capture unavailable — install soundcard, numpy and av.",
    "Nenhuma fonte de áudio — abra Opções.":
        "No audio source — open Options.",
    # status — recording
    "Gravando…  (mic + sistema)": "Recording…  (mic + system)",
    "microfone": "microphone",
    "áudio do sistema": "system audio",
    "Nenhuma fonte pôde ser capturada ({which}): {m}":
        "No source could be captured ({which}): {m}",
    "Falha ao capturar {which} (a outra fonte continua).":
        "Failed to capture {which} (the other source continues).",
    "Salvando…": "Saving…",
    "Erro ao salvar: {m}": "Error saving: {m}",
    "Nenhum áudio capturado — verifique as fontes selecionadas.":
        "No audio captured — check the selected sources.",
    "Salvo: {n}  —  Escolha o que fazer:": "Saved: {n}  —  Choose what to do:",
    "Gravação salva: {n}": "Recording saved: {n}",
    "Gravação descartada.": "Recording discarded.",
    "Não foi possível excluir: {e}": "Couldn't delete: {e}",
    "Não foi possível excluir.": "Couldn't delete.",
    "Rascunho ao vivo desativado — transcrição em andamento.":
        "Live draft disabled — a transcription is already running.",
    "Termine a gravação para trocar de tela.":
        "Finish the recording to switch screens.",
    "Termine a transcrição para trocar de tela.":
        "Finish the transcription to switch screens.",
    "Termine a conversão para trocar de tela.":
        "Finish the conversion to switch screens.",
    "Transcrição ao vivo (rascunho)": "Live transcription (draft)",
    "Fechando rascunho ao vivo…": "Closing live draft…",
    "Salvo: {n}  —  refinando a transcrição…":
        "Saved: {n}  —  refining the transcription…",
    "Rascunho mantido — passada final falhou: {e}":
        "Draft kept — final pass failed: {e}",
    "Transcrição final pronta: {n}": "Final transcription ready: {n}",
    "Transcrição final pronta (falha ao salvar o .txt).":
        "Final transcription ready (failed to save the .txt).",
    "Transcrição ao vivo atrasada — descartando áudio antigo do rascunho.":
        "Live transcription running behind — discarding old draft audio.",
    "Transcrição ao vivo parou (a gravação continua).":
        "Live transcription stopped (the recording continues).",
    # status — transcription
    "Nada para transcrever.": "Nothing to transcribe.",
    "Nada para reproduzir.": "Nothing to play.",
    "Arquivo não encontrado.": "File not found.",
    "Já há uma transcrição em andamento.": "A transcription is already running.",
    "Transcrição indisponível — instale openvino-genai.":
        "Transcription unavailable — install openvino-genai.",
    "Transcrevendo {n}…": "Transcribing {n}…",
    "Transcrevendo… {p}%": "Transcribing… {p}%",
    "Baixando modelo '{size}' (primeira vez)…":
        "Downloading model '{size}' (first time)…",
    "Sem internet — usando o modelo '{size}' embutido.":
        "No internet — using the bundled '{size}' model.",
    "Preparando modelo no {dev}…": "Preparing model on {dev}…",
    "Carregando áudio…": "Loading audio…",
    "Atualizando modelo…": "Updating model…",
    "Modelo atualizado.": "Model updated.",
    "Nova versão {tag}": "New version {tag}",
    "Erro na transcrição: {e}": "Transcription error: {e}",
    "Transcrito, mas falha ao salvar o .txt.":
        "Transcribed, but failed to save the .txt.",
    "Transcrição salva: {n}. Áudio excluído.":
        "Transcription saved: {n}. Audio deleted.",
    "Transcrição salva: {n}": "Transcription saved: {n}",
    # transcribe section
    "TRANSCRIÇÃO": "TRANSCRIPTION",
    "Escolher arquivo…": "Choose a file…",
    "Transcrição cancelada.": "Transcription cancelled.",
    "Salvo: {n}": "Saved: {n}",
    # tray
    "Abrir": "Open",
    "Sair": "Quit",
    "Reco — pronto": "Reco — ready",
    "Reco — gravando {d}": "Reco — recording {d}",
    "Reco — salvando gravação…": "Reco — saving recording…",
    "Salvando antes de sair…": "Saving before exit…",
    "Reco — pausado {d}": "Reco — paused {d}",
    # pause / resume
    "Pausar": "Pause",
    "Continuar": "Resume",
    "Pausado — {d} gravado.": "Paused — {d} recorded.",
    # convert section (video/heavy audio → light MP3)
    "Converter…": "Convert…",
    "CONVERSÃO": "CONVERSION",
    "Escolher vídeo ou áudio…": "Choose video or audio…",
    "Selecionar vídeo ou áudio": "Select video or audio",
    "Converter para MP3": "Convert to MP3",
    "MP3 leve: {sr} kHz mono, {br} kbps.":
        "Light MP3: {sr} kHz mono, {br} kbps.",
    "Origem: {a}": "Source: {a}",
    "Convertendo… {p}%": "Converting… {p}%",
    "MP3 salvo: {n}  ({a} → {b})": "MP3 saved: {n}  ({a} → {b})",
    "O arquivo não tem faixa de áudio.": "The file has no audio track.",
    "Falha ao converter: {e}": "Conversion failed: {e}",
    "Já há uma conversão em andamento.": "A conversion is already running.",
    "Conversão indisponível — instale av.":
        "Conversion unavailable — install av.",
    "Vídeo": "Video",
    "Abrir pasta": "Open folder",
    "Abrir transcrição": "Open transcription",
    # recordings library (view "Gravações…")
    "Gravações…": "Recordings…",
    "GRAVAÇÕES": "RECORDINGS",
    "Buscar:": "Search:",
    "Arquivo": "File",
    "Data": "Date",
    "Duração": "Length",
    "Atualizar": "Refresh",
    "Lendo gravações…": "Reading recordings…",
    "Nenhuma gravação encontrada.": "No recordings found.",
    "Selecione uma gravação.": "Select a recording.",
    "Enviada para a Lixeira: {n}": "Sent to the Recycle Bin: {n}",
    # AI summary (library ✦ action, via the user's Claude Code CLI)
    "Resumo IA": "AI summary",
    "Gerando resumo com o Claude…": "Generating the summary with Claude…",
    "Resumo salvo: {n}": "Summary saved: {n}",
    "Falha ao gerar o resumo: {e}": "Summary failed: {e}",
    "Resumo indisponível — instale o Claude Code (comando 'claude').":
        "Summary unavailable — install Claude Code (the 'claude' command).",
    "Já há um resumo em andamento.": "A summary is already running.",
    "Selecione um arquivo e clique em Transcrever.":
        "Select a file and click Transcribe.",
    "Selecione um arquivo válido.": "Select a valid file.",
    "Erro: {e}": "Error: {e}",
    "Selecionar áudio": "Select audio",
    "Áudio": "Audio", "Todos": "All files",
    # sentinel de transcrição vazia (antes era EN fixo no código)
    "(nenhum conteúdo reconhecido)": "(no content recognized)",
    # dependency messagebox
    "Dependências ausentes": "Missing dependencies",
    "Para gravar áudio, instale as dependências:\n\n  pip install {pkgs}\n\n"
    "Abra um terminal e rode o comando acima. Depois, reinicie o {app}.":
        "To record audio, install the dependencies:\n\n  pip install {pkgs}\n\n"
        "Open a terminal and run the command above. Then restart {app}.",
}

# Traduções PT->PL, chave = frase PT (polonês, 25/09/2026).
_TR_PL: dict = {
    # header / meters
    "MIC": "MIKROFON",
    "SISTEMA": "SYSTEM",
    # buttons
    "Gravar": "Nagrywaj",
    "Parar": "Zatrzymaj",
    "Salvar": "Zapisz",
    "Transcrever": "Transkrybuj",
    "Excluir": "Usuń",
    "Transcrever + excluir": "Transkrybuj + usuń",
    "Reproduzir": "Odtwórz",
    "Salvar + Transcrever": "Zapisz + transkrybuj",
    "Tema:": "Motyw:",
    "Fundo": "Tło",
    "Destaque": "Akcent",
    "Padrão": "Domyślny",
    "Cor de fundo": "Kolor tła",
    "Cor de destaque": "Kolor akcentu",
    # links
    "Opções": "Opcje",
    "Ocultar opções": "Ukryj opcje",
    "Transcrever…": "Transkrybuj…",
    "← Gravar": "← Nagrywaj",
    # advanced labels
    "Entrada:": "Wejście:",
    "Saída:": "Wyjście:",
    "Pasta:": "Folder:",
    "Alterar…": "Zmień…",
    "Pasta de gravações": "Folder nagrań",
    "Atualizar dispositivos": "Odśwież urządzenia",
    "Processar em:": "Uruchom na:",
    "Idioma:": "Język:",
    "Criar atalho (Ctrl+Shift+R)": "Utwórz skrót (Ctrl+Shift+R)",
    "Remover atalho": "Usuń skrót",
    "Atalho criado — abra pelo Menu Iniciar ou com Ctrl+Shift+R.":
        "Skrót utworzony — otwórz go z Menu Start lub za pomocą "
        "Ctrl+Shift+R.",
    "Atalho removido.": "Skrót usunięty.",
    "Não foi possível criar o atalho: {e}":
        "Nie udało się utworzyć skrótu: {e}",
    "Preparando '{size}' no {dev} pela primeira vez — isso leva alguns minutos "
    "e só acontece uma vez.":
        "Przygotowywanie '{size}' na {dev} — pierwsza konfiguracja może "
        "potrwać kilka minut i dzieje się tylko raz.",
    # status — devices
    "Pronto para gravar.": "Gotowe do nagrywania.",
    "Buscando dispositivos…": "Wyszukiwanie urządzeń…",
    "Erro ao listar dispositivos: {m}":
        "Błąd podczas pobierania listy urządzeń: {m}",
    "Nenhum dispositivo de áudio encontrado.": "Nie znaleziono urządzeń audio.",
    "Atenção: nenhuma saída de áudio para loopback.":
        "Uwaga: brak wyjścia audio dla loopbacku.",
    "Não é possível atualizar dispositivos durante a gravação.":
        "Nie można odświeżyć urządzeń podczas nagrywania.",
    "Captura indisponível — instale soundcard, numpy e av.":
        "Przechwytywanie niedostępne — zainstaluj soundcard, numpy i av.",
    "Nenhuma fonte de áudio — abra Opções.":
        "Brak źródła dźwięku — otwórz Opcje.",
    # status — recording
    "Gravando…  (mic + sistema)": "Nagrywanie…  (mikrofon + system)",
    "microfone": "mikrofon",
    "áudio do sistema": "dźwięk systemowy",
    "Nenhuma fonte pôde ser capturada ({which}): {m}":
        "Nie udało się przechwycić żadnego źródła ({which}): {m}",
    "Falha ao capturar {which} (a outra fonte continua).":
        "Nie udało się przechwycić {which} (pozostałe źródło działa nadal).",
    "Salvando…": "Zapisywanie…",
    "Erro ao salvar: {m}": "Błąd podczas zapisywania: {m}",
    "Nenhum áudio capturado — verifique as fontes selecionadas.":
        "Nie przechwycono żadnego dźwięku — sprawdź wybrane źródła.",
    "Salvo: {n}  —  Escolha o que fazer:":
        "Zapisano: {n}  —  Wybierz, co dalej:",
    "Gravação salva: {n}": "Zapisano nagranie: {n}",
    "Gravação descartada.": "Nagranie odrzucone.",
    "Não foi possível excluir: {e}": "Nie udało się usunąć: {e}",
    "Não foi possível excluir.": "Nie udało się usunąć.",
    "Rascunho ao vivo desativado — transcrição em andamento.":
        "Wyłączono szkic na żywo — transkrypcja już trwa.",
    "Termine a gravação para trocar de tela.":
        "Zakończ nagrywanie, aby zmienić ekran.",
    "Termine a transcrição para trocar de tela.":
        "Zakończ transkrypcję, aby zmienić ekran.",
    "Termine a conversão para trocar de tela.":
        "Zakończ konwersję, aby zmienić ekran.",
    "Transcrição ao vivo (rascunho)": "Transkrypcja na żywo (szkic)",
    "Fechando rascunho ao vivo…": "Zamykanie szkicu na żywo…",
    "Salvo: {n}  —  refinando a transcrição…":
        "Zapisano: {n}  —  dopracowywanie transkrypcji…",
    "Rascunho mantido — passada final falhou: {e}":
        "Szkic zachowano — ostateczne dopracowanie nie powiodło się: {e}",
    "Transcrição final pronta: {n}": "Ostateczna transkrypcja gotowa: {n}",
    "Transcrição final pronta (falha ao salvar o .txt).":
        "Ostateczna transkrypcja gotowa (nie udało się zapisać pliku .txt).",
    "Transcrição ao vivo atrasada — descartando áudio antigo do rascunho.":
        "Transkrypcja na żywo nie nadąża — odrzucanie starszego audio "
        "ze szkicu.",
    "Transcrição ao vivo parou (a gravação continua).":
        "Transkrypcja na żywo się zatrzymała (nagrywanie trwa nadal).",
    # status — transcription
    "Nada para transcrever.": "Nic do transkrypcji.",
    "Nada para reproduzir.": "Nic do odtworzenia.",
    "Arquivo não encontrado.": "Nie znaleziono pliku.",
    "Já há uma transcrição em andamento.": "Transkrypcja już trwa.",
    "Transcrição indisponível — instale openvino-genai.":
        "Transkrypcja niedostępna — zainstaluj openvino-genai.",
    "Transcrevendo {n}…": "Transkrybowanie {n}…",
    "Transcrevendo… {p}%": "Transkrybowanie… {p}%",
    "Baixando modelo '{size}' (primeira vez)…":
        "Pobieranie modelu '{size}' (pierwszy raz)…",
    "Sem internet — usando o modelo '{size}' embutido.":
        "Brak internetu — używany jest wbudowany model '{size}'.",
    "Preparando modelo no {dev}…": "Przygotowywanie modelu na {dev}…",
    "Carregando áudio…": "Wczytywanie audio…",
    "Atualizando modelo…": "Aktualizowanie modelu…",
    "Modelo atualizado.": "Model zaktualizowany.",
    "Nova versão {tag}": "Nowa wersja {tag}",
    "Erro na transcrição: {e}": "Błąd transkrypcji: {e}",
    "Transcrito, mas falha ao salvar o .txt.":
        "Ztranskrybowano, ale nie udało się zapisać pliku .txt.",
    "Transcrição salva: {n}. Áudio excluído.":
        "Zapisano transkrypcję: {n}. Plik audio usunięty.",
    "Transcrição salva: {n}": "Zapisano transkrypcję: {n}",
    # transcribe section
    "TRANSCRIÇÃO": "TRANSKRYPCJA",
    "Escolher arquivo…": "Wybierz plik…",
    "Transcrição cancelada.": "Transkrypcja anulowana.",
    "Salvo: {n}": "Zapisano: {n}",
    # tray
    "Abrir": "Otwórz",
    "Sair": "Zakończ",
    "Reco — pronto": "Reco — gotowe",
    "Reco — gravando {d}": "Reco — nagrywanie {d}",
    "Reco — salvando gravação…": "Reco — zapisywanie nagrania…",
    "Salvando antes de sair…": "Zapisywanie przed zakończeniem…",
    "Reco — pausado {d}": "Reco — pauza {d}",
    # pause / resume
    "Pausar": "Wstrzymaj",
    "Continuar": "Wznów",
    "Pausado — {d} gravado.": "Wstrzymano — nagrano {d}.",
    # convert section (video/heavy audio → light MP3)
    "Converter…": "Konwertuj…",
    "CONVERSÃO": "KONWERSJA",
    "Escolher vídeo ou áudio…": "Wybierz wideo lub audio…",
    "Selecionar vídeo ou áudio": "Wybierz plik wideo lub audio",
    "Converter para MP3": "Konwertuj na MP3",
    "MP3 leve: {sr} kHz mono, {br} kbps.":
        "Lekki MP3: {sr} kHz mono, {br} kbps.",
    "Origem: {a}": "Źródło: {a}",
    "Convertendo… {p}%": "Konwertowanie… {p}%",
    "MP3 salvo: {n}  ({a} → {b})": "Zapisano MP3: {n}  ({a} → {b})",
    "O arquivo não tem faixa de áudio.": "Plik nie zawiera ścieżki dźwiękowej.",
    "Falha ao converter: {e}": "Błąd konwersji: {e}",
    "Já há uma conversão em andamento.": "Konwersja już trwa.",
    "Conversão indisponível — instale av.":
        "Konwersja niedostępna — zainstaluj av.",
    "Vídeo": "Wideo",
    "Abrir pasta": "Otwórz folder",
    "Abrir transcrição": "Otwórz transkrypcję",
    # recordings library (view "Gravações…")
    "Gravações…": "Nagrania…",
    "GRAVAÇÕES": "NAGRANIA",
    "Buscar:": "Szukaj:",
    "Arquivo": "Plik",
    "Data": "Data",
    "Duração": "Czas trwania",
    "Atualizar": "Odśwież",
    "Lendo gravações…": "Wczytywanie nagrań…",
    "Nenhuma gravação encontrada.": "Nie znaleziono nagrań.",
    "Selecione uma gravação.": "Zaznacz nagranie.",
    "Enviada para a Lixeira: {n}": "Przeniesiono do Kosza: {n}",
    # AI summary (library ✦ action, via the user's Claude Code CLI)
    "Resumo IA": "Podsumowanie AI",
    "Gerando resumo com o Claude…": "Generowanie podsumowania za pomocą Claude…",
    "Resumo salvo: {n}": "Zapisano podsumowanie: {n}",
    "Falha ao gerar o resumo: {e}":
        "Nie udało się wygenerować podsumowania: {e}",
    "Resumo indisponível — instale o Claude Code (comando 'claude').":
        "Podsumowanie niedostępne — zainstaluj Claude Code "
        "(polecenie 'claude').",
    "Já há um resumo em andamento.": "Generowanie podsumowania już trwa.",
    "Selecione um arquivo e clique em Transcrever.":
        "Wybierz plik i kliknij Transkrybuj.",
    "Selecione um arquivo válido.": "Wybierz prawidłowy plik.",
    "Erro: {e}": "Błąd: {e}",
    "Selecionar áudio": "Wybierz plik audio",
    "Áudio": "Audio", "Todos": "Wszystkie pliki",
    # dependency messagebox
    "Dependências ausentes": "Brakujące zależności",
    "Para gravar áudio, instale as dependências:\n\n  pip install {pkgs}\n\n"
    "Abra um terminal e rode o comando acima. Depois, reinicie o {app}.":
        "Aby nagrywać dźwięk, zainstaluj wymagane zależności:\n\n"
        "  pip install {pkgs}\n\n"
        "Otwórz terminal i wykonaj powyższe polecenie. "
        "Następnie uruchom ponownie {app}.",
    # sentinel de transcrição vazia (dodany razem z obsługą PL)
    "(nenhum conteúdo reconhecido)": "(brak rozpoznanej mowy)",
}


def t(s: str) -> str:
    """Translate a PT string to the current language (PT = identity)."""
    if LANG == "pt":
        return s
    return (_TR_PL if LANG == "pl" else _TR_EN).get(s, s)


def tf(s: str, **kw) -> str:
    return t(s).format(**kw)


def _init_lang():
    global LANG
    try:
        LANG = load_config().get("language") or _system_lang()
    except Exception:
        LANG = _system_lang()

_init_lang()


# ── Optional deps ─────────────────────────────────────────────────────────────
try:
    import numpy as np
    HAS_NP = True
except ImportError:
    np = None; HAS_NP = False

try:
    import soundcard as sc
    import warnings
    warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
    HAS_SC = True
except Exception:
    sc = None; HAS_SC = False

try:
    import tray as _tray
    HAS_TRAY = (os.name == "nt")
except Exception:
    _tray = None; HAS_TRAY = False

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except Exception:
    Image = ImageTk = None; HAS_PIL = False


# ── Transcription backend: OpenVINO GenAI (in-process, NPU / iGPU / CPU) ───────
# One backend for everything. OpenVINO runs the whole Whisper model natively on
# the Intel NPU ("AI Boost"), the iGPU (Arc), or any x86-64 CPU — so the .exe is
# fully plug-n-play (no Python, no ffmpeg) once the runtime is bundled. The model
# (pre-converted INT8 IR) is downloaded from Hugging Face on first use.
import importlib.util as _ilu


def _no_window_kwargs() -> dict:
    return {"creationflags": 0x08000000} if os.name == "nt" else {}


def _ps_quote(s: str) -> str:
    """Quote a string as a PowerShell single-quoted literal."""
    return "'" + s.replace("'", "''") + "'"


# Cheap availability probes (don't import the heavy runtime at startup).
import platform as _platform
HAS_OV = _ilu.find_spec("openvino_genai") is not None
HAS_AV = _ilu.find_spec("av") is not None
# macOS on Apple Silicon → MLX backend (uses the Apple GPU; OpenVINO would be
# CPU-only there). Everything else (Windows/Linux x86) → OpenVINO.
HAS_MLX = (sys.platform == "darwin"
           and _platform.machine() in ("arm64", "aarch64")
           and _ilu.find_spec("mlx_whisper") is not None)


def _user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def _bundled_models_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "models"


def _abrir_arquivo(path) -> None:
    """Abre arquivo/pasta/URL com o handler padrão do SO (Explorer, Finder,
    navegador…). `os.startfile` é Windows-only; no darwin não existe."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        os.startfile(str(path))


def _excluir_gravacao(path) -> bool:
    """Exclui um arquivo mandando-o para a Lixeira (Windows); fora do nt, ou se
    o shell recusar, faz unlink direto. Retorna True se o arquivo saiu do disco."""
    path = Path(path)
    if os.name == "nt":
        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", ctypes.c_void_p),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p),
            ]
        FO_DELETE = 0x0003
        FOF_ALLOWUNDO = 0x0040
        FOF_NOCONFIRMATION = 0x0010
        FOF_SILENT = 0x0004
        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = str(path) + "\0\0"   # double-null terminated
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc == 0 and not op.fAnyOperationsAborted:
            return not path.exists()
        # shell recusou (ex.: caminho de rede sem suporte a Lixeira) — fallback.
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return not path.exists()


# ── Audio decoding (PyAV → 16 kHz float32; no external ffmpeg binary) ───────────
def decode_16k(path: Path, split: bool = False) -> list:
    """Decode any audio file to 16 kHz float32 channels.

    Returns [mono] normally, or [left, right] when split=True and the source is
    stereo (channel-based diarization: L = mic = "Eu", R = system = others)."""
    import av
    with av.open(str(path)) as cont:
        st = cont.streams.audio[0]
        nch = getattr(st, "channels", 1) or 1
        stereo = bool(split and nch >= 2)
        rs = av.audio.resampler.AudioResampler(
            format="fltp", layout="stereo" if stereo else "mono", rate=16000)
        buf = []
        for frame in cont.decode(audio=0):
            for r in rs.resample(frame):
                buf.append(r.to_ndarray())          # planar: (layout_ch, n)
        for r in rs.resample(None):                 # flush
            buf.append(r.to_ndarray())
    if not buf:
        return [np.zeros(0, np.float32)] * (2 if stereo else 1)
    arr = np.concatenate(buf, axis=1).astype(np.float32)
    if stereo and arr.shape[0] >= 2:
        return [np.ascontiguousarray(arr[0]), np.ascontiguousarray(arr[1])]
    return [np.ascontiguousarray(arr[0])]


# ── VAD e agrupamento (transcrição por segmento de fala, não janela cega) ──────
# Portado de tools/exp_contexto.py — validado em três arquivos (roadmap
# 2026-07-29-melhoria-transcricao-ao-vivo-vad-diarizacao.md § E2/E3). Cortar às
# cegas a 30 s parte frases no meio; o VAD corta no silêncio.
_VAD_FRAME_S = 0.03


def segmentar_por_vad(audio: "np.ndarray", sr: int = 16000, sil_s: float = 0.8,
                       max_s: float = 28.0, min_s: float = 0.3) -> list:
    """Detecta segmentos de fala por energia. Retorna [(ini, fim)] em amostras.

    Limiar adaptativo (piso = percentil 20 da energia dos quadros de 30 ms; fala
    = max(piso × 3, 0.0035)) — um limiar fixo não sobrevive a mudança de
    ambiente/microfone. max_s força partição: acima de 30 s o Whisper trunca em
    silêncio, sem erro nem aviso (D6)."""
    frame = int(_VAD_FRAME_S * sr)
    nf = len(audio) // frame
    if nf == 0:
        return []
    e = np.array([np.sqrt(np.mean(audio[i*frame:(i+1)*frame] ** 2)) for i in range(nf)])
    piso = max(float(np.percentile(e, 20)), 1e-5)
    lim = max(piso * 3.0, 0.0035)
    fala = e >= lim
    min_sil = max(1, int(sil_s / _VAD_FRAME_S))
    segs, ini, ult, sil = [], None, None, 0
    for i, f in enumerate(fala):
        if f:
            if ini is None:
                ini = i
            ult, sil = i, 0
        elif ini is not None:
            sil += 1
            if sil >= min_sil:
                segs.append((ini*frame, (ult+1)*frame))
                ini = None
    if ini is not None:
        segs.append((ini*frame, (ult+1)*frame))
    out = []
    for s, t in segs:
        if t - s < int(min_s * sr):
            continue
        while t - s > int(max_s * sr):
            out.append((s, s + int(max_s * sr)))
            s += int(max_s * sr)
        out.append((s, t))
    return out


def agrupar_segmentos(segs: list, sr: int, alvo_s: float) -> list:
    """Junta segmentos consecutivos do VAD até somar `alvo_s` de fala.

    alvo_s=0 devolve cada segmento sozinho (latência mínima, sem agrupar)."""
    if alvo_s <= 0:
        return [[s] for s in segs]
    grupos, atual, acc = [], [], 0.0
    for s, t in segs:
        atual.append((s, t))
        acc += (t - s) / sr
        if acc >= alvo_s:
            grupos.append(atual)
            atual, acc = [], 0.0
    if atual:
        grupos.append(atual)
    return grupos


# ── MP3 encoding (PyAV / libmp3lame) ───────────────────────────────────────────
# ⚠️ Always encode through a *container*, never by concatenating raw encoder
# output. A bare VBR/ABR MP3 carries no Xing/Info header, so every player has to
# *guess* the duration from the bitrate of the first frames. A recording that
# starts in silence begins at 8 kbps and then climbs to ~90 — the guess came out
# ~8x too long, and VLC's remaining time jumped around instead of ticking down
# (the bug this replaced). The mp3 muxer writes that header on close().
#
# ABR (not qscale): PyAV's global_quality path low-passes everything above
# ~4 kHz — measured at −46 dB / −72 dB in the top two bands, which wrecks voice
# and Whisper alike. See roadmap/2026-07-28-duracao-mp3-e-salvamento-instantaneo.md.
def _open_mp3(path, sr: int, channels: int, bitrate_kbps: int):
    """Open an MP3 container + encoder stream. Caller must close the container."""
    import av
    cont = av.open(str(path), "w")
    st = cont.add_stream("mp3", rate=sr,
                         layout="stereo" if channels == 2 else "mono")
    st.codec_context.bit_rate = int(bitrate_kbps) * 1000
    st.codec_context.options  = {"abr": "1"}
    return cont, st


class MP3Writer:
    """Encodes the recording to MP3 *while* it is being captured.

    The old path buffered every sample at 48 kHz and only encoded on stop():
    ~11 s of waiting for 20 min of audio (growing linearly), plus ~2.8 GB of RAM
    for a 2-hour meeting. Encoding as the audio arrives makes stopping a flush —
    which is why OBS saves instantly while doing far more work.

    feed() takes equal-length float32 blocks of both channels at `in_sr`. Gain is
    applied per block, so moving a slider changes the audio from that moment on
    (it used to be baked in at save time, i.e. retroactively over the whole file)."""

    def __init__(self, path, in_sr: int, out_sr: int, channels: int,
                 bitrate_kbps: int):
        import av
        self.path    = Path(path)
        self.samples = 0                 # input frames fed (detects "nothing captured")
        self._ch     = channels
        self._in_sr  = in_sr
        self._layout = "stereo" if channels == 2 else "mono"
        self._cont, self._stream = _open_mp3(self.path, out_sr, channels,
                                             bitrate_kbps)
        # Stateful on purpose: resampling block by block with scipy would ring at
        # every block edge; swresample carries the filter state across calls.
        self._rs  = av.audio.resampler.AudioResampler(
            format="fltp", layout=self._layout, rate=out_sr)
        self._pts = 0

    def feed(self, mic, sys_, mic_gain=1.0, sys_gain=1.0):
        import av
        n = min(len(mic), len(sys_))
        if n <= 0:
            return
        if self._ch == 2:
            planar = np.empty((2, n), dtype=np.float32)
            planar[0] = np.clip(mic[:n] * np.float32(mic_gain), -1.0, 1.0)
            planar[1] = np.clip(sys_[:n] * np.float32(sys_gain), -1.0, 1.0)
        else:
            mixed  = mic[:n] * np.float32(mic_gain) + sys_[:n] * np.float32(sys_gain)
            planar = np.clip(mixed, -1.0, 1.0).astype(np.float32).reshape(1, n)
        frame = av.AudioFrame.from_ndarray(planar, format="fltp",
                                           layout=self._layout)
        frame.sample_rate = self._in_sr
        frame.pts         = self._pts
        frame.time_base   = Fraction(1, self._in_sr)
        self._pts    += n
        self.samples += n
        for r in self._rs.resample(frame):
            self._mux(r)

    def _mux(self, frame):
        for pkt in self._stream.encode(frame):
            self._cont.mux(pkt)

    def close(self):
        """Flush resampler + encoder, close the container (this writes the Xing
        header — without it the file lies about its duration)."""
        if self._cont is None:
            return self.path
        try:
            for r in self._rs.resample(None):
                self._mux(r)
            self._mux(None)
        finally:
            self._cont.close()
            self._cont = None
        return self.path

    def discard(self):
        """Close and delete — aborted recording or nothing captured."""
        try:
            if self._cont is not None:
                self._cont.close()
        except Exception:
            pass
        self._cont = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


# ── MP3 extraction (MP4/MKV/… → MP3; also re-encodes heavy audio) ──────────────
class NoAudioStream(Exception):
    pass


def extract_mp3(src: Path, dst: Path | None = None, sr: int = OUT_SR,
                channels: int = EXTRACT_CH, bitrate: int = EXTRACT_BR,
                progress=None) -> Path:
    """Pull the audio out of a video (or re-encode an audio file) as MP3.

    Streams frame-by-frame through the encoder instead of decoding the whole file
    into memory first: a 3-hour meeting would otherwise sit in RAM as float32."""
    import av
    src = Path(src)
    dst = Path(dst) if dst else src.with_suffix(".mp3")
    if dst.resolve() == src.resolve():          # re-encoding an .mp3 onto itself
        dst = src.with_name(f"{src.stem}_{sr // 1000}k.mp3")

    with av.open(str(src)) as cont:
        if not cont.streams.audio:
            raise NoAudioStream()
        st = cont.streams.audio[0]
        total = float(st.duration * st.time_base) if st.duration else (
            float(cont.duration) / av.time_base if cont.duration else 0.0)
        rs = av.audio.resampler.AudioResampler(
            format="fltp", layout="mono" if channels == 1 else "stereo", rate=sr)
        dst.parent.mkdir(parents=True, exist_ok=True)
        out, out_st = _open_mp3(dst, sr, channels, bitrate)
        fed, last_pct = 0, -1
        try:
            for frame in cont.decode(audio=0):
                for r in rs.resample(frame):
                    fed += r.samples
                    for pkt in out_st.encode(r):
                        out.mux(pkt)
                if progress and total and frame.pts is not None:
                    pct = min(99, int(float(frame.pts * st.time_base) / total * 100))
                    if pct != last_pct:
                        last_pct = pct
                        progress(pct)
            for r in rs.resample(None):          # flush the resampler
                fed += r.samples
                for pkt in out_st.encode(r):
                    out.mux(pkt)
            for pkt in out_st.encode(None):      # flush the encoder
                out.mux(pkt)
        finally:
            out.close()                          # writes the Xing header
    if not fed:
        dst.unlink(missing_ok=True)
        raise NoAudioStream()
    return dst


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


# ── Acoustic echo cancellation (offline, least squares over short blocks) ──────
# What this can and cannot do — measured 29/07/2026 on Gabriel's own recordings,
# so nobody re-litigates it from intuition:
#
#   acoustic coupling (speaker -> mic):  -17.9 dB (5.7 min) / -23.7 dB (80 min)
#   ERLE, previous implementation:       +3.2 dB / +3.7 dB   (in the real audio)
#   ERLE, this implementation:           +7.2 dB, costing 0.3 dB of near-end voice
#
# The previous version's docstring claimed ~37 dB, and that was honest — on
# *synthetic* echo, linear and time-invariant. It collapsed to ~3 dB on real
# audio because it estimated ONE complex gain per frequency bin for a whole 30 s
# window, which assumes the acoustic path is invariant for 30 s and fits inside a
# single FFT frame (64 ms). Neither holds in a room.
#
# ⚠️ Do not expect 20-40 dB here, and do not "fix" this by making the filter
# longer. The ceiling is CLOCK DRIFT: mic and loopback run off independent
# hardware clocks. Measured on the 80-minute recording, the optimal alignment
# drifts -65.8 ppm (~237 ms per hour, sigma = 2314 samples); on the 5.7-minute
# one it is rock stable (sigma = 0). No linear filter tracks that without
# continuous resampling. Getting past ~10 dB means compensating drift AND the
# speaker's non-linear distortion — expensive, and unnecessary, because channel
# diarization is better served by energy dominance between channels than by
# perfect cancellation. Full analysis in
# roadmap/2026-07-29-transcricao-precisa-rapida-e-aec.md § 1.5.
def _alinhar_canais(mic: "np.ndarray", ref: "np.ndarray",
                    sr: int = 16000, maxlag_s: float = 0.5):
    """Alinha `ref` a `mic` por correlação cruzada (busca ±maxlag_s).

    ⚠️ `maxlag_s` era 0.2 e **saturava** (19/08/2026). O atraso entre mic e
    loopback não é acústico (eco de caixa a 1 m são ~3 ms): é latência de buffer
    entre os dois streams do WASAPI. Medido em 8 janelas do arquivo de 19/08
    11:00, vai de 2142 a **3370 amostras** (134 a 211 ms), com jitter de 24 ms e
    deriva de +21 ppm. Com o limite em 0.2 s (3200 amostras), as janelas acima de
    200 ms batiam na borda: na janela de 31,5 min o alinhamento saía em 3143
    amostras em vez de 3370 — 14 ms de erro, com o filtro de eco tentando cobrir a
    diferença. 0.5 s dá folga de 2,4× sobre o pior atraso visto, e a correlação é
    por FFT (custo desprezível). Ver
    roadmap/2026-08-19-melhoria-antieco-de-verdade.md § 1.2.

    Retorna (mic_pad, ref_alinhado), ambos do mesmo tamanho — paddados ao maior
    dos dois. Extraído de dentro de `cancel_echo` (era só usado ali) para ser
    reusado por `dominancia_sistema` (Fase 2 do roadmap
    2026-07-29-melhoria-transcricao-ao-vivo-vad-diarizacao.md) sem duplicar a
    busca de atraso."""
    from scipy.signal import correlate
    n = max(len(mic), len(ref))
    mic_p = np.pad(mic, (0, n - len(mic)))
    ref_p = np.pad(ref, (0, n - len(ref)))
    maxlag = int(maxlag_s * sr)
    c = correlate(mic_p, ref_p, mode="full", method="fft")
    lags = np.arange(-len(ref_p) + 1, len(mic_p))
    win = np.abs(lags) <= maxlag
    d = int(lags[win][np.argmax(np.abs(c[win]))])
    ref_al = np.roll(ref_p, d)
    if d > 0:
        ref_al[:d] = 0
    elif d < 0:
        ref_al[d:] = 0
    return mic_p, ref_al


# Dominância de energia entre canais — não cancelamento de eco — é o que resolve
# a diarização errada (D3): o AEC entrega só ~7 dB (teto de deriva de clock, ver
# acima), o que não impede o eco residual de aparecer como fala do "Eu". Aqui
# não se tenta limpar o áudio, só decidir de quem é a fala em cada bloco.
# Calibrado em tools/calibrar_dominancia.py (Fase 2.3 do roadmap
# 2026-07-29-melhoria-transcricao-ao-vivo-vad-diarizacao.md) contra dois
# arquivos reais, usando os blocos "só o sistema fala"/"só o mic fala" que
# tools/medir_eco.py já identifica.
# ⚠️ k_db=12 (primeira calibração, só contra so_mic) APAGOU fala real medida
# numa gravação de 20 min: "Onde é que tá esse manual?" e "eu estou vendo
# isso...", em double-talk (os dois canais acima do limiar ao mesmo tempo —
# so_mic sozinho não pega esse caso, porque nele o sistema está mudo por
# definição). k_db=15 é o menor valor que também mantém o flag em double-talk
# (`ambos`) ≤ 10% — as duas falas voltam a aparecer nesse valor, confirmado por
# transcrição real, não só pela métrica de bloco. Recall de eco cai pra 24,7%;
# "na dúvida, manter o segmento" (roadmap § Riscos) pesa mais que pegar todo
# eco. Não afinar por chute; rodar tools/calibrar_dominancia.py de novo com mais
# gravações se houver motivo, e conferir contra transcrição real antes de
# baixar o valor — a métrica de bloco sozinha já mascarou uma perda real uma
# vez.
K_DB_DOMINANCIA = 15.0


def dominancia_sistema(mic: "np.ndarray", sistema: "np.ndarray",
                       sr: int = 16000, bloco_s: float = 0.1,
                       k_db: float = K_DB_DOMINANCIA, histerese: int = 3,
                       realinhar_s: float = 30.0) -> "np.ndarray":
    """Marca (booleano, por amostra) onde o canal do sistema domina o do mic —
    provável eco/interlocutor vazando, não fala do usuário.

    Compara energia por bloco de `bloco_s`; `histerese` blocos consecutivos
    discordando do estado atual são necessários pra trocar de estado (evita
    picotar blocos isolados em fala de double-talk).

    ⚠️ Realinha a cada `realinhar_s` — um único alinhamento global (testado e
    descartado nesta sessão) ignora a deriva de clock mic↔loopback (ARMADILHAS
    "o teto do AEC é deriva de clock"): medido no arquivo de 80 min, o atraso
    ótimo vai de 48 ms a 155 ms e chega a inverter de sinal perto dos 60 min —
    um alinhamento só, calculado no início, erraria a decisão de dominância no
    resto do arquivo inteiro. `cancel_echo` já reestima por isso; aqui o custo é
    bem menor (uma correlação cruzada por bloco, não um STFT+solve)."""
    passo = max(1, int(realinhar_s * sr))
    n0 = min(len(mic), len(sistema))
    partes_m, partes_s = [], []
    for i in range(0, n0, passo):
        m_al, s_al = _alinhar_canais(mic[i:i + passo], sistema[i:i + passo], sr)
        partes_m.append(m_al[:passo])
        partes_s.append(s_al[:passo])
    mic_al = np.concatenate(partes_m)
    sys_al = np.concatenate(partes_s)
    n = len(mic_al)
    bloco = max(1, int(bloco_s * sr))
    nb = -(-n // bloco)
    dom = np.zeros(nb, dtype=bool)
    for i in range(nb):
        s, e = i * bloco, min(n, (i + 1) * bloco)
        em = float(np.sqrt(np.mean(mic_al[s:e] ** 2))) + 1e-12
        es = float(np.sqrt(np.mean(sys_al[s:e] ** 2))) + 1e-12
        dom[i] = 20 * np.log10(es / em) > k_db
    out = dom.copy()
    estado, contagem = (bool(dom[0]) if nb else False), 0
    for i in range(nb):
        if dom[i] == estado:
            contagem = 0
        else:
            contagem += 1
            if contagem >= histerese:
                estado = dom[i]
                contagem = 0
        out[i] = estado
    return np.repeat(out, bloco)[:n]


def cancel_echo(mic: "np.ndarray", ref: "np.ndarray",
                sr: int = 16000, nfft: int = 1024, hop: int = 256,
                taps: int = 6, bloco_s: float = 2.0,
                residual: bool = True, beta: float = 0.1,
                lam: float = 1e-3, maxlag_s: float = 0.5) -> "np.ndarray":
    """Remove the echo of `ref` (system loopback) bleeding into `mic`.

    On speakers, the PC audio leaks acoustically into the microphone, duplicating
    the other party's voice across both channels and confusing channel
    diarization. We hold a perfect far-end reference (the loopback), so we
    estimate the echo path by least squares and subtract it.

    Two things differ from the previous version, and both are load-bearing:
      `taps` > 1  — the filter spans taps*hop ≈ 96 ms of reverberation instead of
                    assuming the echo fits in one FFT frame;
      `bloco_s`   — re-estimated every 2 s instead of once per 30 s window, which
                    tracks you moving, the volume changing, and slow clock drift.

    Least squares (closed form, Tikhonov-regularised) rather than an adaptive
    NLMS on purpose: an NLMS prototype diverged to -38 dB here, because its step
    normalisation explodes whenever the reference channel goes quiet. `lam` is
    relative to each block's own energy, which is what keeps the normal equations
    solvable during silence. `beta` floors the residual-suppression gain at
    -20 dB; suppressing to zero makes the silence sound robotic.

    Offline, pure numpy/scipy. A near no-op if `ref` carries no energy."""
    if mic.size == 0 or ref.size == 0:
        return mic
    try:
        from scipy.signal import stft, istft
    except Exception:
        return mic
    n0 = len(mic)
    mic, ref_al = _alinhar_canais(mic, ref, sr, maxlag_s=maxlag_s)

    _, _, M = stft(mic, fs=sr, nperseg=nfft, noverlap=nfft - hop)
    _, _, S = stft(ref_al, fs=sr, nperseg=nfft, noverlap=nfft - hop)
    nb, nt = M.shape
    taps = max(1, min(taps, nt))

    # X[k, t, p] = S[k, t-p] — the reference delayed by p frames.
    X = np.zeros((nb, nt, taps), np.complex128)
    for p in range(taps):
        X[:, p:, p] = S[:, :nt - p]

    E = np.empty_like(M)
    Yh = np.empty_like(M)
    passo = max(1, int(bloco_s * sr / hop))
    olho = np.eye(taps)
    for t0 in range(0, nt, passo):
        t1 = min(nt, t0 + passo)
        Xb, Mb = X[:, t0:t1, :], M[:, t0:t1]
        # Normal equations per bin: (X^H X + lam*tr*I) h = X^H m
        A = np.einsum("btp,btq->bpq", np.conj(Xb), Xb)
        b = np.einsum("btp,bt->bp", np.conj(Xb), Mb)
        tr = np.trace(A, axis1=1, axis2=2).real / taps
        A += (lam * np.maximum(tr, 1e-12))[:, None, None] * olho
        try:
            # b needs the explicit trailing axis: numpy 2.x only treats the right
            # hand side as a stack of vectors when it is shaped (..., n, 1).
            h = np.linalg.solve(A, b[:, :, None])[:, :, 0]
        except np.linalg.LinAlgError:
            h = np.zeros((nb, taps), np.complex128)
        y = np.einsum("btp,bp->bt", Xb, h)
        Yh[:, t0:t1] = y
        E[:, t0:t1] = Mb - y

    if residual:
        # Where the estimated echo dominates the residual, attenuate. This is the
        # part that catches the speaker's NON-LINEAR distortion, which no linear
        # filter reaches.
        pe = 0.7 * np.abs(Yh) ** 2
        pr = np.abs(E) ** 2
        E = E * np.maximum(beta, 1.0 - pe / (pr + pe + 1e-12))

    _, out = istft(E, fs=sr, nperseg=nfft, noverlap=nfft - hop)
    out = out[:n0].astype(np.float32)
    # Safety net: if the "cleaned" signal came out louder than the input, the
    # estimate is garbage — hand back the original rather than damage the audio.
    r_in = float(np.sqrt(np.mean(mic[:n0] ** 2))) if n0 else 0.0
    r_out = float(np.sqrt(np.mean(out ** 2))) if out.size else 0.0
    if r_out > r_in * 1.05:
        return mic[:n0].astype(np.float32)
    return out


# ── OpenVINO device + model management ──────────────────────────────────────────
def ov_available_devices() -> list:
    try:
        import openvino as ov
        return list(ov.Core().available_devices)
    except Exception:
        return ["CPU"]


def resolve_device(pref: str) -> str:
    """Map a preferred device to one that exists (pref → GPU → NPU → CPU).

    ⚠️ The order is GPU-first, and that is a measured decision, not a hunch
    (29/07/2026 — it used to be NPU-first, chosen without measuring). With
    large-v3-turbo on this machine: iGPU 11.1x realtime, NPU 5.5x, CPU 4.4x —
    i.e. 2 h of audio in ~12, ~19 and ~65 minutes. CPU is last for a second
    reason: with `small` it also hallucinated far more than the accelerators.

    The NPU stays a first-class option (selectable in the UI) because it is
    immune to contention in a way the iGPU is not: under concurrent load the
    iGPU lost 52% of its speed and the NPU lost 3%. Someone transcribing during
    a video call should pick NPU — the iGPU is the only one of the three that
    competes with drawing the screen."""
    avail = ov_available_devices()
    def has(d):                       # available_devices may report 'GPU.0' etc.
        return any(a == d or a.startswith(d + ".") for a in avail)
    if pref and has(pref):
        return pref
    for d in ("GPU", "NPU", "CPU"):
        if has(d):
            return d
    return "CPU"


MODEL_SENTINEL = "openvino_encoder_model.xml"


def ov_model_repo(size: str) -> str:
    return f"OpenVINO/whisper-{size}-int8-ov"


def _repo_for_dir(d: Path) -> str:
    """The HF repo a local model folder came from (folder name is the repo name)."""
    return f"OpenVINO/{d.name}"


def _find_model_dir(size: str | None = None) -> Path | None:
    """Locate the active OV model folder dynamically (the one holding the encoder
    XML). A downloaded update in the user-data dir wins over the bundled copy."""
    cands = []
    user = _user_data_dir() / "models"
    if user.is_dir():
        cands += sorted(p for p in user.iterdir() if p.is_dir())
    bundled = _bundled_models_dir()
    if bundled.is_dir():
        cands += sorted(p for p in bundled.iterdir() if p.is_dir())
    valid = [d for d in cands if (d / MODEL_SENTINEL).exists()]
    if size:
        for d in valid:
            if f"whisper-{size}-" in d.name:
                return d
        return None    # pedido explícito sem match — NÃO cai silenciosamente
                       # no primeiro modelo válido (F5): quem pediu turbo não
                       # pode acabar preso no small bundlado sem saber (era o
                       # bug: máquina nova nunca baixava o modelo pedido).
    return valid[0] if valid else None


def _write_revision(d: Path, repo: str):
    try:
        from huggingface_hub import HfApi
        sha = HfApi().model_info(repo).sha
        if sha:
            (d / ".hf_revision").write_text(sha, encoding="utf-8")
    except Exception:
        pass


def ensure_ov_model(size: str, progress=None) -> Path:
    """Local dir with the OV IR model; bundled, updated, or downloaded on demand.

    F5: `_find_model_dir(size)` só acha um match exato — se o pedido for
    'large-v3-turbo' e só houver 'small' no disco, baixa de verdade em vez de
    usar o 'small' em silêncio. Se o download falhar (offline), cai pro melhor
    modelo válido existente, mas com status explícito — o usuário sabe que não
    é o modelo pedido, em vez de achar que é."""
    d = _find_model_dir(size)
    if d is not None:
        return d
    if progress:
        progress(tf("Baixando modelo '{size}' (primeira vez)…", size=size))
    from huggingface_hub import snapshot_download
    repo = ov_model_repo(size)
    dest = _user_data_dir() / "models" / f"whisper-{size}-int8-ov"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(repo, local_dir=str(dest))
        _write_revision(dest, repo)
        return dest
    except Exception:
        fallback = _find_model_dir(None)
        if fallback is None:
            raise
        if progress:
            progress(tf("Sem internet — usando o modelo '{size}' embutido.",
                        size=fallback.name.replace("whisper-", "").replace("-int8-ov", "")))
        return fallback


def _dir_writable(d: Path) -> bool:
    try:
        t = d / ".w_test"
        t.write_text("x", encoding="utf-8"); t.unlink()
        return True
    except Exception:
        return False


def update_model_if_newer(status_cb=None):
    """Check HF for a newer revision of the active model and replace it in place
    (atomic-ish), or in the user-data dir if the install folder is read-only.
    Fail-safe: any error (offline, API down…) leaves the current model intact."""
    try:
        d = _find_model_dir()
        if d is None:
            return
        repo = _repo_for_dir(d)
        from huggingface_hub import HfApi, snapshot_download
        latest = HfApi().model_info(repo).sha
        rev_file = d / ".hf_revision"
        local = rev_file.read_text(encoding="utf-8").strip() if rev_file.exists() else None
        if not latest or latest == local:
            return
        if status_cb:
            status_cb(t("Atualizando modelo…"))
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="reco_model_"))
        snapshot_download(repo, revision=latest, local_dir=str(tmp))
        target = d if _dir_writable(d.parent) else _user_data_dir() / "models" / d.name
        target.parent.mkdir(parents=True, exist_ok=True)
        bak = target.with_name(target.name + ".old")
        if bak.exists():
            shutil.rmtree(bak, ignore_errors=True)
        if target.exists():
            target.rename(bak)
        shutil.move(str(tmp), str(target))
        (target / ".hf_revision").write_text(latest, encoding="utf-8")
        if bak.exists():
            shutil.rmtree(bak, ignore_errors=True)
        if status_cb:
            status_cb(t("Modelo atualizado."))
    except Exception:
        pass


# ── App update check (notify only — opens the download page) ────────────────────
def _ver_tuple(s: str) -> tuple:
    import re
    nums = re.findall(r"\d+", s or "")
    return tuple(int(x) for x in nums[:3]) if nums else (0,)


def check_app_update():
    """Return (tag, url) if a newer GitHub release exists, else None. Fail-safe."""
    try:
        import urllib.request, json
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "Reco"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        tag = data.get("tag_name") or ""
        if _ver_tuple(tag) > _ver_tuple(APP_VERSION):
            return tag, (data.get("html_url")
                         or f"https://github.com/{GITHUB_REPO}/releases/latest")
    except Exception:
        pass
    return None


CANCELLED = "__cancelled__"   # sentinel: transcription stopped by the user


PROMPTS = {
    "pt": ("Reunião de trabalho em português brasileiro com alguns termos "
           "técnicos em inglês. Nomes de marcas e siglas em inglês são comuns."),
    "en": ("Work meeting in English with some product names and acronyms."),
}

# Prompt do resumo IA (ação ✦ da biblioteca). Roda via `claude -p` — o CLI do
# Claude Code que o usuário já tem — com a transcrição no stdin: sem chave de
# API no app, sem rede própria, e tudo segue funcional sem o CLI (decisão de
# 02/08/2026, hub do projeto: camada de IA pessoal via claude -p, nunca LLM
# embarcado no bundle). A saída vira <gravação>.resumo.md ao lado do áudio.
PROMPT_RESUMO = {
    "pt": ("Você recebe no stdin a transcrição de uma reunião. As falas podem "
           "vir rotuladas por 'Eu' (quem gravou) e 'Interlocutor(es)' (o outro "
           "lado). Escreva em português, em markdown puro, exatamente estas "
           "seções: '## Resumo' (3-6 frases), '## Decisões' (bullets; 'nenhuma "
           "registrada' se não houver) e '## Pendências e próximos passos' "
           "(bullets, com o dono quando o texto disser). Seja fiel ao texto; "
           "não invente nomes nem fatos. Responda só com o markdown."),
    "en": ("You receive a meeting transcript on stdin. Lines may be labeled "
           "'Me' (the recorder) and 'Speaker(s)' (the other side). Write in "
           "English, plain markdown, exactly these sections: '## Summary' "
           "(3-6 sentences), '## Decisions' (bullets; 'none recorded' if "
           "empty) and '## Action items' (bullets, with owners when stated). "
           "Stay faithful to the text; invent no names or facts. Reply with "
           "the markdown only."),
    "pl": ("Otrzymujesz na stdin transkrypcję spotkania. Wypowiedzi mogą być "
           "opatrzone etykietami 'Ja' (osoba, która nagrywała) i 'Rozmówca(y)' "
           "(druga strona). Pisz po polsku, w czystym markdownie, dokładnie te "
           "sekcje: '## Podsumowanie' (3-6 zdań), '## Decyzje' (lista "
           "punktowana; 'brak zarejestrowanych', jeśli lista jest pusta) oraz "
           "'## Zadania i następne kroki' (lista punktowana, z podaniem osoby "
           "odpowiedzialnej, gdy wynika to z tekstu). Trzymaj się tekstu; nie "
           "wymyślaj nazwisk ani faktów. Odpowiedz wyłącznie markdownem."),
}

# Speaker labels for channel-based diarization (mic = you; system loopback = who-
# ever is on the call — count unknown, hence the plural).
def _spk_me() -> str:
    return {"pt": "Eu", "pl": "Ja"}.get(LANG, "Me")

def _spk_them() -> str:
    return {"pt": "Interlocutor(es)", "pl": "Rozmówca(y)"}.get(LANG, "Speaker(s)")


# ── Settings ──────────────────────────────────────────────────────────────────
# Model (small), device (AUTO → NPU→iGPU→CPU), diarization and echo cancellation
# are all automatic — config keys still exist for power-user overrides, but there
# are no UI controls for them.


# ── Device helpers (soundcard / WASAPI) ────────────────────────────────────────
def list_capture_devices():
    if not HAS_SC:
        return [], []
    mics, spks = [], []
    try:
        for m in sc.all_microphones(include_loopback=False):
            if getattr(m, "isloopback", False):
                continue
            mics.append((m.id, m.name))
    except Exception as e:
        print(f"[scan mic] {e}")
    try:
        for s in sc.all_speakers():
            spks.append((s.id, s.name))
    except Exception as e:
        print(f"[scan spk] {e}")
    return mics, spks


def default_mic_id() -> str | None:
    if not HAS_SC:
        return None
    try:
        return sc.default_microphone().id
    except Exception:
        return None


def default_speaker_id() -> str | None:
    if not HAS_SC:
        return None
    try:
        return sc.default_speaker().id
    except Exception:
        return None


def pick_device(devs: list, saved_id: str | None, default_id: str | None) -> str | None:
    ids = [d[0] for d in devs]
    if saved_id and saved_id in ids:
        return saved_id
    if default_id and default_id in ids:
        return default_id
    return devs[0][0] if devs else None


def name_for_id(devs: list, dev_id: str | None) -> str | None:
    return next((n for i, n in devs if i == dev_id), None)


def id_for_name(devs: list, name: str | None) -> str | None:
    return next((i for i, n in devs if n == name), None)


# ── Dual Recorder ─────────────────────────────────────────────────────────────
CAPTURE_SR = 48000
CHUNK = 1024


class NoAudioCaptured(Exception):
    pass


# ── Alinhamento dos canais na gravação (Fase 1, 19/08/2026) ────────────────────
# Os dois streams do WASAPI começam juntos (o Barrier garante isso), mas o
# loopback entrega o primeiro bloco com um offset próprio, e `_pump` pareia por
# CONTAGEM DE AMOSTRAS — então o offset inicial ficava gravado no arquivo para
# sempre. Medido em gravações reais: 203 ms num arquivo, −399 ms noutro (o SINAL
# varia: às vezes o loopback vem depois do mic). Acima de ~50 ms o ouvido para de
# integrar a cópia como reverberação e passa a ouvir eco separado — foi a queixa
# que abriu o roadmap 2026-08-19-melhoria-antieco-de-verdade.md.
#
# ⚠️ Alinhar NÃO é processar o áudio (decisão 3 do § 2 daquele roadmap): não há
# filtro nem ganho aqui, só descarte/inserção de amostras para que t=0 dos dois
# canais seja o mesmo instante real. O MP3 continua sendo mic cru | loopback cru.
ALIGN_COLETA_S   = 10.0    # quanto acumular antes de estimar o offset inicial
ALIGN_DESISTE_S  = 20.0    # ⚠️ teto da RETENÇÃO. Sem eco medível (gravação de fone,
                           # caixa muda, ninguém falou ainda) a correlação não fecha,
                           # e prender o áudio esperando por ela deixaria o MP3
                           # vazio e o rascunho ao vivo mudo por todo esse tempo.
                           # Passado o teto, grava normalmente e segue tentando o
                           # offset completo nas reestimativas (estado "tentando")
ALIGN_RECHECK_S  = 60.0    # reestimativa periódica. ⚠️ Era 300 s, e não bastava:
                           # a deriva de clock é lenta (~76 ms/hora) mas o atraso
                           # tem JITTER de ~24 ms entre trechos, e com 5 min entre
                           # checagens o residual medido numa gravação real ficou
                           # em 18 ms (gate: 10 ms). A 60 s o custo é uma correlação
                           # de 20 s decimados por minuto — ~30 ms de CPU.
ALIGN_JANELA_S   = 20.0    # janela (decimada a 16k) usada nas reestimativas
ALIGN_MAXLAG_S   = 0.5     # busca de atraso — mesmo teto de _alinhar_canais
ALIGN_Q_MIN      = 0.15    # correlação normalizada mínima para confiar no pico
ALIGN_MIN_AJUSTE = 48      # 1 ms @48k — abaixo disso não vale mexer
ALIGN_MAX_AJUSTE = 2400    # 50 ms @48k — teto por reestimativa, contra estimativa
                           # ruim fazer estrago de uma vez


def estimar_offset(mic, sistema, sr=CAPTURE_SR, maxlag_s=ALIGN_MAXLAG_S):
    """Atraso do mic em relação ao loopback, em amostras de `sr`, + qualidade.

    Positivo = o conteúdo comum (o eco do que a caixa tocou) aparece no mic
    DEPOIS de aparecer no loopback, que é o caso normal. Negativo = o loopback é
    que está atrasado; acontece de verdade (medido em 18/08/2026), então quem
    consome isto tem que tratar os dois sinais.

    A qualidade é a correlação cruzada normalizada no pico (0..1): sem eco
    audível — gravação de fone, caixa muda — não existe conteúdo comum, o pico é
    ruído e o offset não deve ser aplicado. Correlaciona em 16 kHz (decima por 3,
    razão exata) porque 62 µs de resolução é muito mais do que o suficiente e a
    FFT fica 3× menor.
    """
    try:
        from scipy.signal import correlate, resample_poly
    except Exception:
        return 0, 0.0
    dec = sr // 16000 if sr % 16000 == 0 else 1
    a = np.asarray(mic, np.float64)
    b = np.asarray(sistema, np.float64)
    if dec > 1:
        a, b = resample_poly(a, 1, dec), resample_poly(b, 1, dec)
    n = min(len(a), len(b))
    if n < 16000:                       # menos de 1 s decimado: não dá pra confiar
        return 0, 0.0
    a, b = a[:n], b[:n]
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-6 or nb < 1e-6:          # um dos canais está mudo
        return 0, 0.0
    c = correlate(a, b, mode="full", method="fft")
    lags = np.arange(-n + 1, n)
    janela = np.abs(lags) <= int(maxlag_s * (sr // dec))
    cw, lw = c[janela], lags[janela]
    k = int(np.argmax(np.abs(cw)))
    return int(lw[k]) * dec, float(np.abs(cw[k]) / (na * nb))


class DualRecorder:
    """Captures mic + system in lockstep and encodes the MP3 as it goes.

    Three threads: one reader per channel (they only append raw blocks) and one
    encoder that pairs the two channels sample-for-sample and feeds MP3Writer.
    Nothing is buffered for the whole session, so stop() is a flush and memory
    stays flat regardless of how long the meeting runs."""

    ENC_TICK = 0.2          # how often the encoder drains the capture buffers (s)

    def __init__(self):
        self._pause_ev   = threading.Event()   # set = paused (audio is dropped)
        self._stop_ev    = threading.Event()   # set = capture threads wind down
        self._drain_ev   = threading.Event()   # set = encoder does its final pass
        self._mic_chunks = []
        self._sys_chunks = []
        self._buf_mic    = np.zeros(0, np.float32)   # encoder-side leftovers: the
        self._buf_sys    = np.zeros(0, np.float32)   # part of one channel whose
        self._lk_mic     = threading.Lock()          # peer hasn't arrived yet
        self._lk_sys     = threading.Lock()
        self._lk_state   = threading.Lock()
        self._on_level   = None
        self._on_error   = None
        self._on_pair    = None
        self._threads    = []
        self._enc_thread = None
        self._writer     = None
        self._enc_err    = None
        self._barrier    = None
        self._n_requested = 0
        self._n_errors    = 0
        self.recording   = False
        self.mic_ok      = False
        self.sys_ok      = False
        # A requested channel stops being "live" once its stream fails; from that
        # point the encoder fills it with silence instead of waiting for blocks
        # that will never come (otherwise one dead device stalls the pairing).
        self._mic_live   = False
        self._sys_live   = False
        # Alinhamento dos canais (Fase 1). "coletando" retém o pareamento até dar
        # para estimar o offset; "ok" já alinhou; "off" desistiu (sem eco medível
        # — fone, ou caixa muda — ou canal único).
        self._al_estado  = "coletando"
        self._al_off     = 0        # offset inicial aplicado, em amostras @48k
        self._al_ajustes = 0        # quantas correções de deriva já foram feitas
        self._al_deriva  = 0        # soma das correções de deriva, em amostras
        self._al_jan_m   = np.zeros(0, np.float32)   # janela decimada (16k) do que
        self._al_jan_s   = np.zeros(0, np.float32)   # já foi pareado, p/ reestimar
        self._al_desde   = 0        # amostras @48k pareadas desde a última checagem
        # Per-channel linear gain, applied as each block is encoded. Live-adjustable:
        # a change applies from that moment on (it used to be baked in at save
        # time, over the whole file — impossible once we encode while recording).
        self.mic_gain    = 1.0
        self.sys_gain    = 1.0

    def set_gain(self, mic=None, sys=None):
        if mic is not None:
            self.mic_gain = float(mic)
        if sys is not None:
            self.sys_gain = float(sys)

    def start(self, mic_id, sys_id, on_level=None, on_error=None, on_pair=None,
              out_sr=OUT_SR, out_channels=OUT_CH, bitrate=MP3_BR, out_dir=None):
        if self.recording:
            return
        # Reap any leftover threads from a previous session before touching state.
        self._stop_ev.set()
        self._drain_ev.set()
        if self._barrier is not None:
            try: self._barrier.abort()
            except Exception: pass
        for t_ in self._threads + ([self._enc_thread] if self._enc_thread else []):
            t_.join(timeout=3.0)
        self._threads    = []
        self._enc_thread = None

        # Fresh per-session lists: a leaked thread can only append to the old
        # list, never pollute the current recording.
        mic_chunks, sys_chunks = [], []
        with self._lk_mic:
            self._mic_chunks = mic_chunks
        with self._lk_sys:
            self._sys_chunks = sys_chunks
        # Same reason: an aborted encode can leave paired-but-unwritten audio
        # behind, which would otherwise open the next recording.
        self._buf_mic = np.zeros(0, np.float32)
        self._buf_sys = np.zeros(0, np.float32)
        self._al_estado  = "coletando" if (mic_id is not None and sys_id is not None)                            else "off"      # canal único não tem o que alinhar
        self._al_off     = 0
        self._al_ajustes = 0
        self._al_deriva  = 0
        self._al_jan_m   = np.zeros(0, np.float32)
        self._al_jan_s   = np.zeros(0, np.float32)
        self._al_desde   = 0

        self._on_level    = on_level
        self._on_error    = on_error
        self._on_pair     = on_pair
        self.mic_ok       = False
        self.sys_ok       = False
        self._n_errors    = 0
        self._enc_err     = None
        self._mic_live    = mic_id is not None
        self._sys_live    = sys_id is not None
        self._n_requested = (mic_id is not None) + (sys_id is not None)
        self._barrier     = threading.Barrier(max(1, self._n_requested))
        self._stop_ev.clear()
        self._drain_ev.clear()
        self._pause_ev.clear()

        # The file is created now, not at stop(): its timestamp is the start of
        # the recording, and a crash mid-meeting leaves a playable partial MP3
        # instead of nothing.
        try:
            self._writer = self._new_writer(out_sr, out_channels, bitrate, out_dir)
        except Exception as e:
            self._writer      = None
            self._n_requested = 1                  # so all_failed() is True
            self._fail("save", str(e))
            return

        self.recording    = True
        if mic_id is not None:
            th = threading.Thread(target=self._rec_mic,
                                  args=(mic_id, mic_chunks, self._lk_mic),
                                  daemon=True)
            th.start(); self._threads.append(th)
        if sys_id is not None:
            th = threading.Thread(target=self._rec_sys,
                                  args=(sys_id, sys_chunks, self._lk_sys),
                                  daemon=True)
            th.start(); self._threads.append(th)
        self._enc_thread = threading.Thread(target=self._encode_loop, daemon=True)
        self._enc_thread.start()

    @staticmethod
    def _new_writer(out_sr, out_channels, bitrate, out_dir) -> "MP3Writer":
        folder = Path(out_dir) if out_dir else default_output_dir()
        folder.mkdir(parents=True, exist_ok=True)
        # 'reco' marks this as a dual-channel (mic+system) recording (see RECO_TAG).
        prefix = {"pt": "gravacao_reco", "pl": "nagranie_reco"}.get(LANG, "recording_reco")
        ts     = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return MP3Writer(folder / f"{prefix}_{ts}.mp3",
                         CAPTURE_SR, out_sr, out_channels, bitrate)

    def all_failed(self) -> bool:
        return self._n_requested > 0 and self._n_errors >= self._n_requested

    # Pausing keeps reading the WASAPI streams and throws the frames away, instead
    # of stopping the readers: an unread capture buffer overruns, and restarting a
    # stream would re-open the device (a click, and a fresh clock to re-align).
    # Both channels drop in lockstep, so L/R stay in sync across the cut.
    def pause(self):
        if self.recording:
            self._pause_ev.set()

    def resume(self):
        self._pause_ev.clear()

    @property
    def paused(self) -> bool:
        return self._pause_ev.is_set()

    def _await_peer(self):
        if self._barrier is None:
            return
        try:
            self._barrier.wait(timeout=3.0)
        except Exception:
            pass

    def _fail(self, kind, msg):
        with self._lk_state:
            self._n_errors += 1
        # Stop pairing against a channel that will never deliver again — the
        # encoder pads it with silence from here on.
        if kind == "mic":
            self._mic_live = False
        elif kind == "sys":
            self._sys_live = False
        if self._barrier is not None:
            try: self._barrier.abort()
            except Exception: pass
        print(f"[{kind}] {msg}")
        if self._on_error:
            self._on_error(kind, msg)

    def stop(self, progress=None) -> Path:
        """Wind the threads down and close the file. Everything is already
        encoded, so this only flushes — it does not depend on how long the
        recording was."""
        self._wind_down(timeout=3.0)
        writer, self._writer = self._writer, None
        if writer is None:
            raise NoAudioCaptured()
        if not writer.samples:
            writer.discard()
            raise NoAudioCaptured()
        if progress:
            progress(t("Salvando…"))
        path = writer.close()
        if self._enc_err:                      # partial file, but better than none
            print(f"[encode] {self._enc_err}")
        return path

    def abort(self):
        self._wind_down(timeout=2.0)
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.discard()

    def _wind_down(self, timeout: float):
        """Stop the capture threads, then let the encoder drain what they left."""
        self._stop_ev.set()
        if self._barrier is not None:
            try: self._barrier.abort()
            except Exception: pass
        for t_ in self._threads:
            t_.join(timeout=timeout)
        self._threads = []
        # Only now: the capture threads are done appending, so the encoder's final
        # pass sees every block there will ever be.
        self._drain_ev.set()
        if self._enc_thread is not None:
            self._enc_thread.join(timeout=timeout)
            self._enc_thread = None
        self.recording = False

    def _rec_mic(self, dev_id, chunks, lock):
        # WASAPI capture starts at __enter__ (Start); the barrier comes BEFORE it
        # so both streams start as synchronized as possible.
        try:
            rec = sc.get_microphone(dev_id, include_loopback=False).recorder(
                samplerate=CAPTURE_SR, channels=1, blocksize=CHUNK)
        except Exception as e:
            self._fail("mic", str(e)); return
        self._await_peer()
        try:
            with rec as r:
                self.mic_ok = True
                while not self._stop_ev.is_set():
                    data = r.record(numframes=CHUNK)
                    if self._pause_ev.is_set():
                        continue                     # keep draining, keep nothing
                    arr = data[:, 0] if data.ndim > 1 else data
                    arr = np.ascontiguousarray(arr, dtype=np.float32)
                    with lock:
                        chunks.append(arr)
                    if self._on_level and arr.size:
                        self._on_level("mic",
                                       float(np.sqrt(np.mean(arr ** 2))) * self.mic_gain)
        except Exception as e:
            self._fail("mic", str(e))

    def _rec_sys(self, dev_id, chunks, lock):
        try:
            rec = sc.get_microphone(dev_id, include_loopback=True).recorder(
                samplerate=CAPTURE_SR, channels=2, blocksize=CHUNK)
        except Exception as e:
            self._fail("sys", str(e)); return
        self._await_peer()
        try:
            with rec as r:
                self.sys_ok = True
                while not self._stop_ev.is_set():
                    data = r.record(numframes=CHUNK)
                    if self._pause_ev.is_set():
                        continue                     # keep draining, keep nothing
                    mono = data.mean(axis=1) if data.ndim > 1 else data
                    mono = np.ascontiguousarray(mono, dtype=np.float32)
                    with lock:
                        chunks.append(mono)
                    if self._on_level and mono.size:
                        self._on_level("sys",
                                       float(np.sqrt(np.mean(mono ** 2))) * self.sys_gain)
        except Exception as e:
            self._fail("sys", str(e))

    # ── encoder thread ─────────────────────────────────────────────────────────
    def _encode_loop(self):
        # Encoding runs here, never in the capture threads: a slow encode there
        # would leave the WASAPI buffer unread and overrun it (dropped audio).
        while not self._drain_ev.is_set():
            self._pump()
            self._drain_ev.wait(self.ENC_TICK)
        self._pump(final=True)

    def _drain_chunks(self):
        """Take everything captured so far. Empties the lists in place — the
        capture threads hold a reference to them, so they must not be replaced."""
        with self._lk_mic:
            mic = self._mic_chunks[:]
            del self._mic_chunks[:]
        with self._lk_sys:
            sys_ = self._sys_chunks[:]
            del self._sys_chunks[:]
        return mic, sys_

    # ── alinhamento dos canais (Fase 1) ────────────────────────────────────────
    def _al_estimar_inicial(self, final: bool) -> bool:
        """Tenta fixar o offset inicial. Devolve True se o pareamento pode seguir.

        Enquanto retorna False, `_pump` NÃO escreve nada: o áudio fica retido nos
        buffers. É de propósito — alinhar depois de já ter escrito os primeiros
        segundos deixaria um salto no meio do arquivo. O preço é o MP3 só começar
        a crescer ~10 s depois do start (e um crash antes disso perder esses 10 s).
        """
        n = min(len(self._buf_mic), len(self._buf_sys))
        precisa = int(ALIGN_COLETA_S * CAPTURE_SR)
        if n < precisa and not final:
            if n >= int(ALIGN_DESISTE_S * CAPTURE_SR):
                self._al_estado = "tentando"      # para de reter; tenta mais tarde
                print("[align] sem par nos dois canais — gravando e tentando depois")
                return True
            return False
        if n <= 0:
            return bool(final)
        d, q = estimar_offset(self._buf_mic[:n], self._buf_sys[:n])
        if q < ALIGN_Q_MIN:
            # Sem eco medível não há como (nem por que) alinhar: gravação de fone,
            # caixa muda, ou ninguém falou ainda. Espera mais material até o teto.
            if n >= int(ALIGN_DESISTE_S * CAPTURE_SR) or final:
                self._al_estado = "tentando"
                print(f"[align] sem correlação confiável (q={q:.3f}) — gravando e "
                      f"tentando depois")
                return True
            return False
        d = int(np.clip(d, -int(ALIGN_MAXLAG_S * CAPTURE_SR),
                        int(ALIGN_MAXLAG_S * CAPTURE_SR)))
        if d > 0:                                # mic atrasado: adianta o mic
            self._buf_mic = self._buf_mic[d:]
        elif d < 0:                              # loopback atrasado: adianta ele
            self._buf_sys = self._buf_sys[-d:]
        self._al_off = d
        self._al_estado = "ok"
        print(f"[align] offset inicial {d:+d} amostras "
              f"({d / CAPTURE_SR * 1000:+.0f} ms), q={q:.3f}")
        return True

    def _al_acumular(self, mic, sistema):
        """Guarda o trecho pareado (decimado a 16k) para a próxima reestimativa."""
        try:
            from scipy.signal import resample_poly
        except Exception:
            return
        dec = CAPTURE_SR // 16000
        m = resample_poly(mic, 1, dec).astype(np.float32)
        t = resample_poly(sistema, 1, dec).astype(np.float32)
        teto = int(ALIGN_JANELA_S * 16000)
        self._al_jan_m = np.concatenate([self._al_jan_m, m])[-teto:]
        self._al_jan_s = np.concatenate([self._al_jan_s, t])[-teto:]

    def _al_corrigir_deriva(self):
        """Reestima o offset a cada ALIGN_RECHECK_S e corrige o que derivou.

        Mic e loopback correm em relógios independentes: medido, +21 ppm (≈76 ms
        por hora) num arquivo e −65,8 ppm noutro. Um alinhamento só, no início, não
        sobrevive a uma reunião de duas horas.
        """
        if self._al_desde < int(ALIGN_RECHECK_S * CAPTURE_SR):
            return
        self._al_desde = 0
        if len(self._al_jan_m) < int(5 * 16000):
            return
        d16, q = estimar_offset(self._al_jan_m, self._al_jan_s, sr=16000)
        if q < ALIGN_Q_MIN:
            return
        d = int(d16 * (CAPTURE_SR // 16000))
        if abs(d) < ALIGN_MIN_AJUSTE:
            if self._al_estado == "tentando":     # já estava alinhado por sorte
                self._al_estado = "ok"
            return
        # Quem ainda não alinhou pode aplicar o offset INTEIRO (é a primeira
        # correção, não deriva); quem já alinhou só corrige o que derivou — um
        # salto de 200 ms no meio de uma gravação alinhada seria estimativa ruim,
        # não deriva de clock.
        teto = (int(ALIGN_MAXLAG_S * CAPTURE_SR) if self._al_estado == "tentando"
                else ALIGN_MAX_AJUSTE)
        d = int(np.clip(d, -teto, teto))
        if d > 0:
            self._buf_mic = self._buf_mic[d:] if len(self._buf_mic) > d else                             np.zeros(0, np.float32)
        else:
            # Loopback atrasou: inserir silêncio no mic é o inverso barato de
            # descartar, e alguns milissegundos são inaudíveis.
            self._buf_mic = np.concatenate([np.zeros(-d, np.float32), self._buf_mic])
        if self._al_estado == "tentando":
            self._al_estado = "ok"
            self._al_off = d
            print(f"[align] offset {d:+d} amostras ({d / CAPTURE_SR * 1000:+.0f} ms) "
                  f"aplicado tarde (q={q:.3f}) — há um salto neste ponto do arquivo")
        else:
            self._al_ajustes += 1
            self._al_deriva += d
            print(f"[align] deriva corrigida {d:+d} amostras "
                  f"({d / CAPTURE_SR * 1000:+.1f} ms), q={q:.3f}")

    def alinhamento(self) -> dict:
        """Estado do alinhamento, para log/relatório e para os testes."""
        return {"estado": self._al_estado,
                "offset_amostras": self._al_off,
                "offset_ms": self._al_off / CAPTURE_SR * 1000.0,
                "ajustes_deriva": self._al_ajustes,
                "deriva_amostras": self._al_deriva}

    def _pump(self, final: bool = False):
        """Pair the two channels sample-for-sample and hand the pair to the writer.

        Only the overlap is encoded; the tail of whichever channel is ahead stays
        buffered until its peer catches up, which is what keeps L and R in sync."""
        if self._writer is None or self._enc_err:
            return
        new_mic, new_sys = self._drain_chunks()
        if new_mic:
            self._buf_mic = np.concatenate([self._buf_mic, *new_mic])
        if new_sys:
            self._buf_sys = np.concatenate([self._buf_sys, *new_sys])

        # Alinhamento (Fase 1): retém o pareamento até saber o offset, depois
        # corrige a deriva periodicamente. Roda ANTES do padding por canal morto —
        # senão o silêncio do canal ausente entra na correlação e a envenena.
        if self._al_estado == "coletando" and self._mic_live and self._sys_live:
            if not self._al_estimar_inicial(final):
                return
        elif self._al_estado in ("ok", "tentando"):
            self._al_corrigir_deriva()

        nm, ns = len(self._buf_mic), len(self._buf_sys)
        # A channel that is gone (never requested, failed, or done capturing) can
        # never catch up — pad it with silence so the survivor keeps flowing.
        if (final or not self._mic_live) and ns > nm:
            self._buf_mic = np.concatenate([self._buf_mic,
                                            np.zeros(ns - nm, np.float32)])
        if (final or not self._sys_live) and nm > ns:
            self._buf_sys = np.concatenate([self._buf_sys,
                                            np.zeros(nm - ns, np.float32)])

        n = min(len(self._buf_mic), len(self._buf_sys))
        if n <= 0:
            return
        try:
            self._writer.feed(self._buf_mic[:n], self._buf_sys[:n],
                              self.mic_gain, self.sys_gain)
        except Exception as e:                   # disk full, codec error…
            self._enc_err = e
            print(f"[encode] {e}")
            return
        # Modo ao vivo (Fase 3): mesmo trecho pareado que acabou de ir pro MP3,
        # já com o ganho aplicado (E1 — o VAD tem que ver o mesmo nível que o
        # Gabriel ouve e que vai pro arquivo). ⚠️ O callback só pode enfileirar
        # — qualquer trabalho real aqui reintroduz o bug que a arquitetura de
        # três threads existe para evitar (WASAPI atrasa, buffer estoura).
        if self._on_pair is not None:
            try:
                self._on_pair(self._buf_mic[:n] * np.float32(self.mic_gain),
                              self._buf_sys[:n] * np.float32(self.sys_gain))
            except Exception as e:
                print(f"[live] on_pair falhou: {e}")
        if self._al_estado in ("ok", "tentando"):
            self._al_acumular(self._buf_mic[:n], self._buf_sys[:n])
            self._al_desde += n
        self._buf_mic = self._buf_mic[n:]
        self._buf_sys = self._buf_sys[n:]


# ── Transcriber: OpenVINO GenAI, in-process (NPU / iGPU / CPU) ──────────────────
# Quanto de fala consecutiva (do VAD) juntar antes de mandar transcrever, no modo
# lote e no modo ao vivo (Dec4 do roadmap 2026-07-29-melhoria... — preset único).
# Medido em três arquivos, com a regra de decisão congelada ANTES de olhar o
# resultado (§ E3): "agrupar ≫ não agrupar" é o achado robusto — 6,9% WER médio
# contra 9,9% sem agrupar — mas o valor exato entre 3 e 10 s é RUÍDO (os
# vencedores por arquivo foram 3s, 5s e 10s, um cada). Não afinar por intuição;
# se houver motivo para mexer, rodar tools/exp_alvo.py com mais gravações.
ALVO_ACUMULO_S = 3.0


class OVTranscriber:
    WIN = 30.0      # seconds per window — legacy blind-window fallback only
                    # (segmentar_por_vad + agrupar_segmentos drive the real path
                    # below); also the progress + cancellation granularity.

    def __init__(self):
        self._pipe    = None
        self._key     = None        # (size, device) the live pipeline was built for
        self._size    = _CFG_DEFAULTS["model"]
        self._devpref = "AUTO"
        self._lock    = threading.Lock()
        self._cancel  = threading.Event()

    def set_model(self, size: str):
        with self._lock:
            self._size = size

    def set_device(self, pref: str):
        with self._lock:
            self._devpref = pref

    def cancel(self):
        self._cancel.set()

    def _pipeline(self, progress_cb):
        with self._lock:
            size, devpref = self._size, self._devpref
        device = resolve_device(devpref)
        key = (size, device)
        if self._pipe is not None and self._key == key:
            return self._pipe
        model_dir = ensure_ov_model(size, progress=progress_cb)
        import openvino_genai as og
        cache = _user_data_dir() / "ovcache"
        cache.mkdir(parents=True, exist_ok=True)
        # CACHE_DIR persists the compiled blob so the first-run compile happens
        # only once per (model, device) — but that first run is *long*: measured
        # 415 s for large-v3-turbo on the NPU (29/07/2026). Without a warning the
        # window just sits there and the user assumes it hung, so we track which
        # combinations have already been compiled and say so up front.
        marca = cache / f".compilado-{size}-{device}"
        if progress_cb:
            if marca.exists():
                progress_cb(tf("Preparando modelo no {dev}…", dev=device))
            else:
                progress_cb(tf("Preparando '{size}' no {dev} pela primeira vez — "
                               "isso leva alguns minutos e só acontece uma vez.",
                               size=size, dev=device))
        pipe = og.WhisperPipeline(str(model_dir), device, CACHE_DIR=str(cache))
        try:
            marca.touch()
        except Exception:
            pass          # cosmetic only: at worst the warning shows again
        self._pipe, self._key = pipe, key
        return pipe

    # Near-silent 30 s windows are skipped: Whisper has no built-in VAD and tends
    # to hallucinate repeated tokens ("BUSH BUSH BUSH…") on silence/noise.
    SILENCE_RMS = 0.0035

    def _gen_cfg(self, pipe, lang):
        cfg = pipe.get_generation_config()
        cfg.language = "<|%s|>" % lang
        cfg.task = "transcribe"
        cfg.return_timestamps = True
        # ⚠️ Do NOT add no_repeat_ngram_size here expecting it to stop repetition
        # loops. The attribute exists on WhisperGenerationConfig, so setting it
        # raises nothing — but WhisperPipeline ignores it. Measured 29/07/2026:
        # generating with it off and set to 4 produced byte-identical text
        # (2590/2590 and 1427/1427 chars). Loop defence lives in _degenerado()
        # below, in our own code, because openvino_genai 2026.2.1 exposes neither
        # compression_factor_threshold nor logprob_threshold — the pair reference
        # Whisper uses to detect a degenerate window and redo it.
        #
        # NOTE: initial_prompt / hotwords overflow the NPU's static decoder
        # ("roi_end <= max_dim"), so we deliberately don't set them — language is
        # already forced, and channel diarization keeps each voice clean.
        return cfg

    # Whisper's classic failure: instead of emitting nothing on a near-empty
    # window, the decoder locks into a loop. A real case from Gabriel's own
    # 18/07 recording: "o que é" repeated 147 times in a row — 1.3k characters of
    # garbage, 4.5% of the file. Reproduced with `small` on CPU.
    COMPRESSAO_MAX = 2.4    # reference Whisper's compression_ratio_threshold
    REPETICOES_MAX = 3      # same n-gram back-to-back more than this = degenerate

    @staticmethod
    def _degenerado(txt: str) -> bool:
        """True when the text looks like a decoder loop rather than speech.

        Two independent signals, because each misses cases the other catches:
        zlib compression ratio (a loop compresses far better than prose) and
        back-to-back repetition of any 1..8-word n-gram (catches short loops in
        an otherwise long, healthy window)."""
        if not txt or len(txt) < 40:
            return False
        import zlib
        b = txt.encode("utf-8")
        if len(b) / max(1, len(zlib.compress(b))) > OVTranscriber.COMPRESSAO_MAX:
            return True
        ws = txt.split()
        for n in range(1, 9):
            i = 0
            while i + n <= len(ws):
                g = ws[i:i + n]
                c = 1
                while ws[i + c * n:i + (c + 1) * n] == g:
                    c += 1
                if c > OVTranscriber.REPETICOES_MAX:
                    return True
                i += 1 if c == 1 else c * n
        return False

    @staticmethod
    def _texto_de(res) -> str:
        ch = getattr(res, "chunks", None)
        if ch:
            return " ".join((c.text or "").strip() for c in ch).strip()
        return (" ".join(res.texts).strip()
                if getattr(res, "texts", None) else "")

    # Temperatures tried in order when a window comes back degenerate. This is
    # reference Whisper's temperature fallback, reimplemented here because the
    # GenAI pipeline doesn't provide it.
    TEMPERATURAS = (0.2, 0.4, 0.6)

    def _generate_sem_loop(self, pipe, cfg, window):
        """Transcribe one window, redoing it with rising temperature while the
        result looks like a loop. Returns (res, texto) — texto empty if every
        attempt degenerated, in which case dropping the window is the right
        outcome: no text beats 147 repetitions."""
        res = pipe.generate(window, cfg)
        txt = self._texto_de(res)
        if not self._degenerado(txt):
            return res, txt
        for temp in self.TEMPERATURAS:
            if self._cancel.is_set():
                break
            try:
                cfg.do_sample = True
                cfg.temperature = temp
                alt = pipe.generate(window, cfg)
                alt_txt = self._texto_de(alt)
            except Exception as e:
                print(f"[transcribe] retry T={temp} falhou: {e}")
                break
            finally:
                cfg.do_sample = False
                cfg.temperature = 1.0
            if not self._degenerado(alt_txt):
                return alt, alt_txt
        print("[transcribe] janela descartada: degenerada em todas as tentativas")
        return None, ""

    def _transcribe_channel_legacy(self, pipe, cfg, audio, win_done, win_total,
                                   progress_cb, ref=None):
        """Fallback cego de 30 s — usado só quando o VAD não acha nenhum segmento
        de fala (arquivo degenerado, ou canal só-silêncio). Nunca deixar um
        arquivo sem transcrição por causa do VAD (roadmap § Fase 1.3).

        ⚠️ Cancela eco (`ref`) mas NÃO aplica `dominancia_sistema` (Fase 2) —
        só o caminho por VAD faz isso. É o lado conservador (sem VAD não há
        grupo pra recortar), mas fica documentado pra não assumir dominância
        incondicional sempre que `diarize+aec` estão ligados."""
        segs = []
        step = int(self.WIN * 16000)
        n = len(audio)
        i = 0
        while i < n:
            if self._cancel.is_set():
                return segs, win_done
            window = audio[i:i + step]
            if ref is not None:
                window = cancel_echo(window, ref[i:i + step])
            off = i / 16000.0
            rms = float(np.sqrt(np.mean(window ** 2))) if window.size else 0.0
            if rms >= self.SILENCE_RMS:        # skip near-silence (no hallucinations)
                res, txt_ok = self._generate_sem_loop(pipe, cfg, window)
                if res is None:                # degenerate in every attempt
                    win_done += 1
                    if progress_cb and win_total:
                        progress_cb(tf("Transcrevendo… {p}%",
                                       p=min(99, int(win_done / win_total * 100))))
                    i += step
                    continue
                chunks = getattr(res, "chunks", None)
                if chunks:
                    for c in chunks:
                        txt = (c.text or "").strip()
                        if txt:
                            segs.append((off + float(c.start_ts), txt))
                elif txt_ok:
                    segs.append((off, txt_ok))
            win_done += 1
            if progress_cb and win_total:
                progress_cb(tf("Transcrevendo… {p}%",
                               p=min(99, int(win_done / win_total * 100))))
            i += step
        return segs, win_done

    def _transcribe_channel(self, pipe, cfg, audio, win_done, win_total,
                            progress_cb, ref=None):
        """Return ([(abs_start, text), …], windows_done); report progress per window.

        Segmenta por VAD (não janela cega) e agrupa até ALVO_ACUMULO_S de fala
        antes de mandar (E2/E3 do roadmap 2026-07-29-melhoria...). Passa as
        últimas ~30 palavras já transcritas como initial_prompt do envio
        seguinte (E4) — só na iGPU/CPU: initial_prompt estoura o decoder
        estático da NPU (roi_end <= max_dim), então o contexto é desligado
        quando o device resolvido é NPU.

        Se `ref` é dado (canal do sistema), o eco de `ref` é cancelado por grupo,
        só na fala selecionada pelo VAD — não no arquivo/janela inteira. Medido
        nesta sessão: limpar o canal inteiro em blocos de 30 s antes do VAD (como
        fazia a janela cega) roda `cancel_echo` sobre o silêncio também, o que sai
        mais caro que limpar só a fala; limpar por grupo é o que ficou mais
        barato que a janela cega de hoje.

        `ref` também dispara a dominância de canal (Fase 2, D3): grupos onde o
        sistema domina em energia sobre o mic são descartados sem transcrever —
        é provável eco/interlocutor vazando, não fala do usuário. Só se aplica
        ao canal do mic (quem chama passa `ref` só nesse caso)."""
        sr = 16000
        n = len(audio)
        if n == 0:
            return [], win_done
        vad_segs = segmentar_por_vad(audio, sr)
        if not vad_segs:
            return self._transcribe_channel_legacy(
                pipe, cfg, audio, win_done, win_total, progress_cb, ref=ref)

        device = self._key[1] if self._key else resolve_device(self._devpref)
        usar_contexto = (device != "NPU")
        dom_mask = dominancia_sistema(audio, ref, sr) if ref is not None else None

        def partes_livres(s, t):
            """Sub-trechos de [s,t) onde o sistema NÃO domina — recorta o eco
            fora do grupo em vez de descartar o grupo inteiro: um grupo longo
            pode ter só alguns segundos de eco embutidos no meio de fala real,
            e um voto por maioria do grupo inteiro perde justamente esse caso
            (validado no caso concreto do D3, "É sempre nove")."""
            if dom_mask is None:
                return [(s, t)]
            livre = ~dom_mask[s:t]
            if livre.all():
                return [(s, t)]
            if not livre.any():
                return []
            corte = np.flatnonzero(np.diff(livre.astype(np.int8)) != 0) + 1
            bordas = np.concatenate(([0], corte, [len(livre)]))
            return [(s + int(bordas[i]), s + int(bordas[i+1]))
                    for i in range(len(bordas) - 1) if livre[bordas[i]]]

        segs = []
        contexto = None
        grupos = agrupar_segmentos(vad_segs, sr, ALVO_ACUMULO_S)
        for g in grupos:
            if self._cancel.is_set():
                return segs, win_done
            ini = g[0][0]
            off = ini / sr
            dur = sum((t - s) for s, t in g) / sr
            partes = [p for s, t in g for p in partes_livres(s, t)]
            if not partes:                     # grupo inteiro é eco/interlocutor
                win_done += dur / self.WIN
                if progress_cb and win_total:
                    progress_cb(tf("Transcrevendo… {p}%",
                                   p=min(99, int(win_done / win_total * 100))))
                continue
            # Concatenar só as partes de fala do grupo, não o span ini:fim —
            # incluir o silêncio entre segmentos manda áudio morto pro Whisper e
            # o eco cancelado é medido só na fala (comentário acima).
            window = np.concatenate([audio[s:t] for s, t in partes])
            if ref is not None:
                window = cancel_echo(window, np.concatenate([ref[s:t] for s, t in partes]))
            rms = float(np.sqrt(np.mean(window ** 2))) if window.size else 0.0
            if rms >= self.SILENCE_RMS:        # skip near-silence (no hallucinations)
                cfg.initial_prompt = (contexto or "") if usar_contexto else ""
                res, txt_ok = self._generate_sem_loop(pipe, cfg, window)
                if res is None:                # degenerate in every attempt
                    contexto = None            # não propagar contexto envenenado
                else:
                    chunks = getattr(res, "chunks", None)
                    novo = []
                    if chunks:
                        for c in chunks:
                            txt = (c.text or "").strip()
                            if txt:
                                segs.append((off + float(c.start_ts), txt))
                                novo.append(txt)
                    elif txt_ok:
                        segs.append((off, txt_ok))
                        novo.append(txt_ok)
                    if usar_contexto and novo:
                        palavras = ((contexto or "") + " " + " ".join(novo)).split()
                        contexto = " ".join(palavras[-30:])
            win_done += dur / self.WIN
            if progress_cb and win_total:
                progress_cb(tf("Transcrevendo… {p}%",
                               p=min(99, int(win_done / win_total * 100))))
        return segs, win_done

    def transcribe(self, path, lang="pt", diarize=False, aec=False,
                   progress_cb=None, done_cb=None):
        self._cancel.clear()

        def run():
            try:
                pipe = self._pipeline(progress_cb)
                cfg  = self._gen_cfg(pipe, lang)
                if progress_cb:
                    progress_cb(t("Carregando áudio…"))
                chans = decode_16k(path, split=diarize)

                step = int(self.WIN * 16000)
                win_total = max(1, sum(max(1, -(-len(c) // step))
                                       for c in chans if len(c)))

                # Echo of the system (R) is cancelled from the mic (L) per-window
                # inside _transcribe_channel — keeps diarization honest without a
                # whole-file FFT (which would use many GB on long recordings).
                ref = chans[1] if (diarize and aec and len(chans) >= 2) else None

                if diarize and len(chans) >= 2:
                    me, done = self._transcribe_channel(
                        pipe, cfg, chans[0], 0, win_total, progress_cb, ref=ref)
                    if self._cancel.is_set():
                        if done_cb: done_cb(None, CANCELLED)
                        return
                    them, done = self._transcribe_channel(
                        pipe, cfg, chans[1], done, win_total, progress_cb)
                    if self._cancel.is_set():
                        if done_cb: done_cb(None, CANCELLED)
                        return
                    text = self._merge(me, them)
                else:
                    segs, _ = self._transcribe_channel(
                        pipe, cfg, chans[0], 0, win_total, progress_cb)
                    if self._cancel.is_set():
                        if done_cb: done_cb(None, CANCELLED)
                        return
                    text = "\n".join(tx for _, tx in segs)

                if done_cb:
                    done_cb(text or t("(nenhum conteúdo reconhecido)"), None)
            except Exception as e:
                if done_cb:
                    done_cb(None, str(e))

        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _merge(me_segs, them_segs):
        """Interleave two channels by time; group consecutive same-speaker lines."""
        me, them = _spk_me(), _spk_them()
        tagged = ([(t0, me, tx) for t0, tx in me_segs] +
                  [(t0, them, tx) for t0, tx in them_segs])
        tagged.sort(key=lambda x: x[0])
        lines, cur, buf = [], None, []
        for _, spk, tx in tagged:
            if spk != cur:
                if buf:
                    lines.append(f"{cur}: " + " ".join(buf))
                cur, buf = spk, [tx]
            else:
                buf.append(tx)
        if buf:
            lines.append(f"{cur}: " + " ".join(buf))
        return "\n".join(lines)


# ── Transcrição ao vivo (Fase 3) ────────────────────────────────────────────────
# Segmento fechado, não janela deslizante (Dec5 do roadmap
# 2026-07-29-melhoria-transcricao-ao-vivo-vad-diarizacao.md): o VAD detecta o
# fim da fala, transcreve uma vez, o texto nunca muda depois — mais barato e
# mais legível que reprocessar os últimos N segundos continuamente. O texto ao
# vivo é RASCUNHO (Dec2): a passada final ao parar (Fase 3.7) roda o preset de
# lote de verdade — com dominância de canal (Fase 2) — e substitui.
class LiveTranscriber:
    ESPERA_MAX_S = 20.0   # corte por tempo de espera — fala arrastada não trava o texto
    FILA_MAX_S = 60.0     # acima disso, descarta o mais antigo (rascunho atrasado
                          # não vale perder áudio — a passada final cobre tudo)

    def __init__(self, transcriber: OVTranscriber, lang: str = "pt"):
        self._tr = transcriber          # pipeline JÁ carregado — nunca uma 2a instância (828 MB)
        self._lang = lang
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._pend_amostras = 0         # amostras (48 kHz) na fila, p/ política de descarte
        self._thread = None
        self._stop_ev = threading.Event()
        self._discard_ev = threading.Event()
        self._on_text = None
        self._on_warn = None
        # Estado por canal: cauda de áudio a 16 kHz ainda não enviada, contexto
        # (initial_prompt) das últimas ~30 palavras, e o relógio do corte por espera.
        self._cauda = {"mic": np.zeros(0, np.float32), "sys": np.zeros(0, np.float32)}
        self._contexto = {"mic": None, "sys": None}
        # Por canal, não um relógio só — um relógio global fazia o mic falando
        # sem parar resetar o corte por espera do sys (e vice-versa), atrasando
        # um canal quieto indefinidamente enquanto o outro segue ativo (achado
        # nesta sessão medindo um atraso real de ~24s no canal do sistema).
        self._ultimo_envio = {"mic": time.monotonic(), "sys": time.monotonic()}
        # Total de amostras (16 kHz) já empilhadas na cauda de cada canal desde
        # o início — não decrementa quando a cauda é aparada. É o que permite
        # calcular o fim absoluto (em amostras) de um grupo enviado, para medir
        # latência de verdade (fim do trecho -> texto), não o avanço do laço de
        # alimentação (tools/test_live.py media a coisa errada até 29/07 — ver
        # roadmap/2026-07-29-melhoria-transcricao-ao-vivo-vad-diarizacao.md § 3.2).
        self._total_amostras = {"mic": 0, "sys": 0}
        self._on_group = None

    def start(self, on_text=None, on_warn=None, on_group=None):
        """on_text(canal, texto) por trecho transcrito; on_warn(msg) se descartar
        fila; on_group(canal, fim_absoluto_amostras_16k) — opcional, só para
        instrumentação/teste de latência real (ver tools/test_live.py)."""
        self._on_text = on_text
        self._on_warn = on_warn
        self._on_group = on_group
        self._stop_ev.clear()
        self._discard_ev.clear()
        self._ultimo_envio = {"mic": time.monotonic(), "sys": time.monotonic()}
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def feed_pair(self, mic_48k: "np.ndarray", sys_48k: "np.ndarray"):
        """Único método chamado pelo `on_pair` do `DualRecorder` — SÓ enfileira,
        nenhum trabalho real aqui (mesma regra do próprio `on_pair`)."""
        if self._thread is None or not self._thread.is_alive():
            return
        with self._lock:
            self._pend_amostras += len(mic_48k)
            descartou = False
            while self._pend_amostras / CAPTURE_SR > self.FILA_MAX_S:
                try:
                    velho_mic, _ = self._q.get_nowait()
                except queue.Empty:
                    break
                self._pend_amostras -= len(velho_mic)
                descartou = True
        if descartou and self._on_warn:
            self._on_warn(t("Transcrição ao vivo atrasada — descartando áudio antigo do rascunho."))
        self._q.put((mic_48k, sys_48k))

    def stop(self, wait: bool = True, timeout: float = 15.0, discard: bool = False):
        """Sinaliza parada. `wait=True` (default) bloqueia até a fila esvaziar —
        é o que permite a passada final (Fase 3.7) nunca rodar `pipe.generate`
        ao mesmo tempo que este worker (drain-then-start).

        `discard=True` esvazia a fila pendente sem transcrevê-la (só o grupo já
        em `_processar_par` termina) — a passada final substitui o backlog
        inteiro, então drená-lo pro rascunho é trabalho jogado fora, e esperar
        até FILA_MAX_S=60s por isso é o que fazia o `join` estourar o timeout
        de 15s e a passada final começar com o worker ainda gerando (F4)."""
        self._stop_ev.set()
        if discard:
            self._discard_ev.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=timeout)

    def _loop(self):
        try:
            while not self._stop_ev.is_set() or not self._q.empty():
                if self._discard_ev.is_set():
                    with self._lock:
                        self._pend_amostras = 0
                    while True:
                        try:
                            self._q.get_nowait()
                        except queue.Empty:
                            break
                    break
                try:
                    mic_48k, sys_48k = self._q.get(timeout=1.0)
                    with self._lock:
                        self._pend_amostras -= len(mic_48k)
                except queue.Empty:
                    mic_48k = sys_48k = None
                if mic_48k is not None:
                    self._processar_par(mic_48k, sys_48k)
                self._checar_corte_por_espera()
        except Exception as e:
            # nunca derruba a gravação — mas o build shipado usa runw.exe
            # (janela, sem console), então print() sozinho é invisível pro
            # usuário: sem isso o painel só fica vazio, sem explicação.
            print(f"[live] worker morreu: {e}")
            if self._on_warn:
                self._on_warn(t("Transcrição ao vivo parou (a gravação continua)."))

    @staticmethod
    def _resample_16k(x: "np.ndarray") -> "np.ndarray":
        from scipy.signal import resample_poly
        # CAPTURE_SR (48000) / 16000 = 3 — razão exata, sem necessidade de ratio geral.
        return resample_poly(x, 1, CAPTURE_SR // 16000).astype(np.float32)

    def _processar_par(self, mic_48k, sys_48k):
        try:
            mic_16k = self._resample_16k(mic_48k)
            sys_16k = self._resample_16k(sys_48k)
        except Exception as e:
            print(f"[live] resample falhou: {e}")
            return
        self._cauda["mic"] = np.concatenate([self._cauda["mic"], mic_16k])
        self._cauda["sys"] = np.concatenate([self._cauda["sys"], sys_16k])
        self._total_amostras["mic"] += len(mic_16k)
        self._total_amostras["sys"] += len(sys_16k)
        self._enviar_grupos_fechados("mic")
        self._enviar_grupos_fechados("sys")

    def _enviar_grupos_fechados(self, canal: str):
        """Manda transcrever todo grupo de VAD já FECHADO (seguido de silêncio
        real ou de corte forçado por `max_s`) — nunca o último grupo, que ainda
        pode estar em andamento. Segmento fechado, não janela deslizante (Dec5)."""
        audio = self._cauda[canal]
        if audio.size == 0:
            return
        segs = segmentar_por_vad(audio, 16000)
        if not segs:
            return
        grupos = agrupar_segmentos(segs, 16000, ALVO_ACUMULO_S)
        if len(grupos) < 2:
            return                      # só o último grupo existe — ainda em andamento
        for g in grupos[:-1]:
            self._transcrever_grupo(canal, audio, g)
            self._ultimo_envio[canal] = time.monotonic()
        # mantém só o que ainda não foi enviado (o último grupo + o que veio depois)
        corte = grupos[-2][-1][1]       # fim do penúltimo grupo == início do que sobra
        self._cauda[canal] = audio[corte:]

    def _checar_corte_por_espera(self):
        """Fala arrastada sem pausa >=0,8s nunca fecha um grupo sozinha — sem
        isso o texto ficaria parado indefinidamente (roadmap 3.2).

        ⚠️ Relógio POR CANAL, não um só global — um relógio compartilhado faz o
        mic falando sem parar resetar o corte do sys (e vice-versa), deixando
        um canal quieto parado indefinidamente enquanto o outro segue ativo
        (medido nesta sessão: ~24s de atraso real no canal do sistema)."""
        agora = time.monotonic()
        for canal in ("mic", "sys"):
            if agora - self._ultimo_envio[canal] < self.ESPERA_MAX_S:
                continue
            audio = self._cauda[canal]
            if audio.size == 0:
                self._ultimo_envio[canal] = agora
                continue
            # Grupo = a lista de segmentos do VAD, não o span inteiro (0, fim) —
            # incluir o silêncio entre segmentos é o mesmo bug medido e corrigido
            # na Fase 1 (_transcribe_channel). Se o VAD não achar nada, manda o
            # áudio inteiro mesmo (mesma regra de fallback do modo lote).
            segs = segmentar_por_vad(audio, 16000)
            grupo = segs if segs else [(0, audio.size)]
            self._transcrever_grupo(canal, audio, grupo)
            self._cauda[canal] = np.zeros(0, np.float32)
            self._ultimo_envio[canal] = agora

    def _transcrever_grupo(self, canal: str, audio: "np.ndarray", grupo: list):
        window = np.concatenate([audio[s:t] for s, t in grupo])
        rms = float(np.sqrt(np.mean(window ** 2))) if window.size else 0.0
        if rms < OVTranscriber.SILENCE_RMS:
            return
        try:
            pipe = self._tr._pipeline(None)
            device = self._tr._key[1] if self._tr._key else resolve_device(self._tr._devpref)
            cfg = self._tr._gen_cfg(pipe, self._lang)
            usar_contexto = (device != "NPU")   # initial_prompt estoura o decoder da NPU
            cfg.initial_prompt = (self._contexto[canal] or "") if usar_contexto else ""
            res, txt = self._tr._generate_sem_loop(pipe, cfg, window)
        except Exception as e:
            print(f"[live] transcrição falhou, rascunho segue sem esse trecho: {e}")
            return
        if res is None:                 # degenerado em todas as tentativas
            self._contexto[canal] = None
            return
        chunks = getattr(res, "chunks", None)
        texto = " ".join((c.text or "").strip() for c in chunks).strip() if chunks else txt
        if not texto:
            return
        if usar_contexto:
            palavras = ((self._contexto[canal] or "") + " " + texto).split()
            self._contexto[canal] = " ".join(palavras[-30:])
        if self._on_group:   # instrumentação/teste de latência — ver __init__
            fim_abs = self._total_amostras[canal] - len(audio) + grupo[-1][1]
            self._on_group(canal, fim_abs)
        if self._on_text:
            spk = _spk_me() if canal == "mic" else _spk_them()
            self._on_text(spk, texto)


# ── Transcriber: MLX (Apple Silicon GPU) — macOS only ──────────────────────────
# Same interface as OVTranscriber so the App is backend-agnostic. mlx-whisper runs
# the model on the Apple GPU via Apple's MLX framework; the device selector and
# CACHE_DIR don't apply. NOTE: this path is only exercised on macOS arm64 — the
# mlx_whisper import lives inside the methods so the module still loads on Windows.
class MLXTranscriber:
    WIN = 30.0

    def __init__(self):
        self._size   = _CFG_DEFAULTS["model"]
        self._lock   = threading.Lock()
        self._cancel = threading.Event()

    def set_model(self, size: str):
        with self._lock:
            self._size = size

    def set_device(self, pref: str):
        pass                         # MLX always uses the Apple GPU

    def cancel(self):
        self._cancel.set()

    @staticmethod
    def _repo(size: str) -> str:
        # large-v3-turbo keeps its name; the rest are whisper-<size>-mlx.
        return f"mlx-community/whisper-{size}-mlx"

    def _transcribe_channel(self, repo, audio, lang, win_done, win_total,
                            progress_cb, ref=None):
        import mlx_whisper
        segs = []
        step = int(self.WIN * 16000)
        n = len(audio)
        i = 0
        while i < n:
            if self._cancel.is_set():
                return segs, win_done
            window = audio[i:i + step]
            if ref is not None:                       # per-window echo cancel
                window = cancel_echo(window, ref[i:i + step])
            off = i / 16000.0
            rms = float(np.sqrt(np.mean(window ** 2))) if window.size else 0.0
            if rms >= OVTranscriber.SILENCE_RMS:
                r = mlx_whisper.transcribe(
                    window, path_or_hf_repo=repo, language=lang,
                    task="transcribe", verbose=None)
                chunks = r.get("segments") or []
                if chunks:
                    for s in chunks:
                        txt = (s.get("text") or "").strip()
                        if txt:
                            segs.append((off + float(s.get("start", 0.0)), txt))
                else:
                    txt = (r.get("text") or "").strip()
                    if txt:
                        segs.append((off, txt))
            win_done += 1
            if progress_cb and win_total:
                progress_cb(tf("Transcrevendo… {p}%",
                               p=min(99, int(win_done / win_total * 100))))
            i += step
        return segs, win_done

    def transcribe(self, path, lang="pt", diarize=False, aec=False,
                   progress_cb=None, done_cb=None):
        self._cancel.clear()

        def run():
            try:
                with self._lock:
                    repo = self._repo(self._size)
                if progress_cb:
                    progress_cb(tf("Preparando modelo no {dev}…", dev="Apple GPU"))
                if progress_cb:
                    progress_cb(t("Carregando áudio…"))
                chans = decode_16k(path, split=diarize)
                step = int(self.WIN * 16000)
                win_total = max(1, sum(max(1, -(-len(c) // step))
                                       for c in chans if len(c)))
                ref = chans[1] if (diarize and aec and len(chans) >= 2) else None

                if diarize and len(chans) >= 2:
                    me, done = self._transcribe_channel(
                        repo, chans[0], lang, 0, win_total, progress_cb, ref=ref)
                    if self._cancel.is_set():
                        if done_cb: done_cb(None, CANCELLED)
                        return
                    them, done = self._transcribe_channel(
                        repo, chans[1], lang, done, win_total, progress_cb)
                    if self._cancel.is_set():
                        if done_cb: done_cb(None, CANCELLED)
                        return
                    text = OVTranscriber._merge(me, them)
                else:
                    segs, _ = self._transcribe_channel(
                        repo, chans[0], lang, 0, win_total, progress_cb)
                    if self._cancel.is_set():
                        if done_cb: done_cb(None, CANCELLED)
                        return
                    text = "\n".join(tx for _, tx in segs)

                if done_cb:
                    done_cb(text or t("(nenhum conteúdo reconhecido)"), None)
            except Exception as e:
                if done_cb:
                    done_cb(None, str(e))

        threading.Thread(target=run, daemon=True).start()


def make_transcriber():
    """Pick the transcription backend for this platform."""
    if HAS_MLX:
        return MLXTranscriber()        # macOS arm64 → Apple GPU
    if HAS_OV:
        return OVTranscriber()         # Windows/Linux x86 → NPU/iGPU/CPU
    return None


# ── Per-channel gain mapping (linear multiplier) ────────────────────────────────
# The handle slides on a multiplier axis with unity (1.0×) at the visual center:
# the left half maps 0×..1× (attenuate/mute), the right half 1×..10× (boost). Two
# linear segments, so "no change" reads naturally in the middle. Values snap to
# GAIN_STEP for clean readouts (1.0×, 2.5×…) and persist as-is in the config.
GAIN_MIN   = 0.0
GAIN_UNITY = 1.0
GAIN_MAX   = 10.0
GAIN_STEP  = 0.5

def gain_to_frac(g: float) -> float:
    g = max(GAIN_MIN, min(g, GAIN_MAX))
    if g <= GAIN_UNITY:
        return 0.5 * (g - GAIN_MIN) / (GAIN_UNITY - GAIN_MIN)
    return 0.5 + 0.5 * (g - GAIN_UNITY) / (GAIN_MAX - GAIN_UNITY)

def frac_to_gain(f: float) -> float:
    f = max(0.0, min(f, 1.0))
    if f <= 0.5:
        g = GAIN_MIN + (f / 0.5) * (GAIN_UNITY - GAIN_MIN)
    else:
        g = GAIN_UNITY + ((f - 0.5) / 0.5) * (GAIN_MAX - GAIN_UNITY)
    g = round(g / GAIN_STEP) * GAIN_STEP          # snap to clean steps (…1.0, 1.5…)
    return max(GAIN_MIN, min(g, GAIN_MAX))

def fmt_gain(g: float) -> str:
    return f"{g:.1f}x".replace(".", ",")          # 1.0 -> "1,0x" (pt-BR decimal)


# ── VU Meter + gain handle ──────────────────────────────────────────────────────
# Horizontal bar = live (gained) level; the vertical handle over it sets the
# per-channel record gain (0×..1× left, 1×..10× right). Drag to attenuate/boost;
# the multiplier readout under the bar shows the current value.
class VuMeter(tk.Canvas):
    DECAY    = 0.82
    H        = 20            # tall enough to grab the handle
    BAR_H    = 5            # level-bar thickness
    HANDLE_W = 7

    def __init__(self, parent, on_gain=None, on_release=None, **kw):
        kw.setdefault("width", 90)
        super().__init__(parent, height=self.H, bg=BG, bd=0,
                         highlightthickness=0, cursor="sb_h_double_arrow", **kw)
        self._on_gain    = on_gain
        self._on_release = on_release
        self._peak    = 0.0
        self._gain    = 1.0
        self._track   = self.create_rectangle(0, 0, 0, 0, fill=BORDER, outline="")
        self._bar     = self.create_rectangle(0, 0, 0, 0, fill=GREEN, outline="")
        self._unity   = self.create_line(0, 0, 0, 0, fill=SUBTLE)
        self._handle  = self.create_rectangle(0, 0, 0, 0, fill=ACCENT, outline="")
        self.bind("<Configure>", lambda _: self._draw())
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        # Persistência do ganho só no solto do botão (F15) — o recorder segue
        # recebendo o ganho em tempo real a cada evento de arrasto (_drag), só
        # o save_config (fsync síncrono) é que esperava dezenas de vezes por
        # arrasto e agora espera o gesto terminar.
        self.bind("<ButtonRelease-1>", self._release)

    def update_level(self, rms):
        self._peak = max(self._peak * self.DECAY, min(rms * 3.0, 1.0))
        self._draw()

    def reset(self):
        self._peak = 0.0
        self._draw()

    def set_gain(self, g):
        self._gain = max(GAIN_MIN, min(float(g), GAIN_MAX))
        self._draw()

    @property
    def gain(self) -> float:
        return self._gain

    def _drag(self, e):
        w = max(self.winfo_width(), 1)
        self._gain = frac_to_gain(e.x / w)
        self._draw()
        if self._on_gain:
            self._on_gain(self._gain)

    def _release(self, e):
        if self._on_release:
            self._on_release(self._gain)

    def _draw(self):
        w  = max(self.winfo_width(), 1)
        yc = self.H / 2
        y0, y1 = yc - self.BAR_H / 2, yc + self.BAR_H / 2
        # scale track + live level fill
        self.coords(self._track, 0, y0, w, y1)
        bw = int(self._peak * w)
        color = (GREEN if self._peak < 0.45
                 else AMBER if self._peak < 0.75
                 else RED_C)
        self.coords(self._bar, 0, y0, bw, y1)
        self.itemconfig(self._bar, fill=color)
        # unity tick (0 dB) at the center
        ux = int(gain_to_frac(1.0) * w)
        self.coords(self._unity, ux, 2, ux, self.H - 2)
        # draggable gain handle
        hx   = int(gain_to_frac(self._gain) * w)
        half = self.HANDLE_W / 2
        self.coords(self._handle, hx - half, 0, hx + half, self.H)


# ── App states ────────────────────────────────────────────────────────────────
IDLE, RECORDING, PAUSED, STOPPED, BUSY = (
    "idle", "recording", "paused", "stopped", "busy")

LANG_LABELS = {"pt": "Português", "en": "English", "pl": "Polski"}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.overrideredirect(True)            # frameless — the header is the title bar
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        _ico = _icon_file()
        if _ico:
            try:
                self.iconbitmap(default=str(_ico))
            except Exception:
                pass

        self._cfg          = load_config()
        self._out_dir      = (Path(self._cfg["output_dir"])
                              if self._cfg.get("output_dir") else default_output_dir())
        apply_theme(self._cfg.get("bg_color") or DEFAULT_BG,
                    self._cfg.get("accent_color") or DEFAULT_ACCENT)
        self.configure(bg=BG)
        self._state        = IDLE
        self._recorder     = DualRecorder() if (HAS_SC and HAS_NP and HAS_AV) else None
        if self._recorder:
            self._recorder.set_gain(mic=self._cfg.get("mic_gain", 1.0),
                                    sys=self._cfg.get("sys_gain", 1.0))
        self._transcriber  = make_transcriber()
        if self._transcriber:
            self._transcriber.set_model(self._cfg.get("model", _CFG_DEFAULTS["model"]))
            self._transcriber.set_device(self._cfg.get("device", "AUTO"))
        self._transcribing = False
        self._live         = None     # LiveTranscriber ativo durante a gravação (Fase 3)
        self._live_was_on  = False    # travado no início da gravação — não muda no meio
        self._last_rec     = None
        self._mic_devs     = []
        self._sys_devs     = []
        self._start_ts     = 0.0
        self._accum        = 0.0      # captured seconds before the current segment
        self._final_dur    = "00:00:00"
        self._timer_after_id = None   # ids do `after` pendente (F16, guard duplicação)
        self._dot_after_id   = None
        self._adv_shown    = False
        self._tr_sel       = None
        self._cv_sel       = None
        self._extracting   = False
        self._view         = "rec"    # "rec" | "tr" | "cv" | "lib" (exclusive views)
        # Biblioteca de gravações: caches sobrevivem ao _rebuild_ui (são dados,
        # não widgets). Duração por (path, mtime) evita reabrir o MP3 no PyAV a
        # cada visita; o texto dos .txt alimenta a busca por conteúdo.
        self._lib_all      = []
        self._lib_rows     = {}
        self._lib_scanning = False
        self._lib_search_after = None
        self._dur_cache    = {}
        self._txt_cache    = {}
        self._resumindo    = False
        self._pop          = None     # floating hover popup (STOPPED actions)
        self._pop_after    = None
        self._pop_anchor   = None
        self._ui_q         = queue.Queue()
        self._closing      = False
        self._quitting     = False
        self._tray         = None
        self._pinned       = True     # hover-shown windows auto-hide; clicked ones don't
        self._hidden       = False
        self._hover_after  = None
        self._icon_cache   = {}   # (nome, cor, tamanho) -> ImageTk.PhotoImage

        self._apply_style()
        self._build()
        self._init_tray()
        self.bind("<Map>", self._on_restore)
        self.bind("<Button-1>", self._pin, add="+")   # clicking pins a hover-shown window

        # center on screen
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry(f"+{x}+{y}")

        self.wm_attributes("-alpha", 0.0)
        self.after(30, lambda: self._fade(0.0))
        self.after(40, self._drain_ui)
        self.after(100, self._scan_devices)
        self._update_shown = False
        self.after(1500, self._kick_update_checks)

    # ── update checks (model: auto; app: notify only) ───────────────────────────
    def _kick_update_checks(self):
        if not HAS_OV and not HAS_MLX:
            pass
        else:
            threading.Thread(target=lambda: update_model_if_newer(
                status_cb=lambda m: self._post(lambda: self._status(m))),
                daemon=True).start()
        def _app():
            res = check_app_update()
            if res:
                self._post(lambda: self._show_app_update(*res))
        threading.Thread(target=_app, daemon=True).start()

    def _show_app_update(self, tag, url):
        if self._update_shown:
            return
        self._update_shown = True
        try:
            lk = self._link(self._links_row,
                            tf("Nova versão {tag}", tag=tag),
                            lambda: self._open_url(url), fg=ACCENT, font=SEG_XS,
                            icon="nova_versao")
            lk.pack(side="left", padx=(12, 0))
        except Exception:
            pass

    def _open_url(self, url):
        try:
            _abrir_arquivo(url)
        except Exception:
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass

    # ── frameless window: drag + minimize ───────────────────────────────────────
    def _drag_start(self, e):
        self._dx = e.x_root - self.winfo_x()
        self._dy = e.y_root - self.winfo_y()

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _minimize(self):
        # overrideredirect breaks iconify on Windows — drop the frame momentarily,
        # iconify, and restore the frameless state when the window maps again.
        self.overrideredirect(False)
        self.update_idletasks()
        self.iconify()

    def _on_restore(self, e=None):
        if (e is None or e.widget is self) and self.state() == "normal":
            self.overrideredirect(True)

    def _winbtn(self, parent, glyph, cmd, hover):
        hf = _best_fg(hover)
        b = tk.Label(parent, text=glyph, font=("Segoe MDL2 Assets", 10),
                     bg=BG, fg=MUTED, cursor="hand2", padx=13)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e, b=b, h=hover, f=hf: b.config(bg=h, fg=f))
        b.bind("<Leave>", lambda e, b=b: b.config(bg=BG, fg=MUTED))
        return b

    # ── thread → UI marshalling (tkinter is not thread-safe) ───────────────────
    def _post(self, fn):
        self._ui_q.put(fn)

    def _drain_ui(self):
        if self._closing:
            return
        try:
            while True:
                fn = self._ui_q.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.after(40, self._drain_ui)

    def _fade(self, a=0.0):
        a = min(a + 0.07, 1.0)
        self.wm_attributes("-alpha", a)
        if a < 1.0:
            self.after(14, lambda: self._fade(a))

    # ── style ────────────────────────────────────────────────────────────────
    def _apply_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        for name in ("D.TCombobox", "Sm.TCombobox", "XS.TCombobox"):
            s.configure(name,
                fieldbackground=CARD, background=CARD_H, foreground=TEXT,
                arrowcolor=MUTED, selectbackground=CARD_A, selectforeground=TEXT,
                borderwidth=0, relief="flat")
            s.map(name,
                fieldbackground=[("readonly", CARD), ("disabled", BG)],
                foreground=[("disabled", SUBTLE)])
        s.configure("D.TCombobox",  font=SEG_SM)
        s.configure("Sm.TCombobox", font=SEG_SM)
        s.configure("XS.TCombobox", font=SEG_XS)
        # Reintroduzidos em 12/08 para a biblioteca de gravações — tinham sido
        # removidos como mortos na Fase 5 do roadmap de UX do mesmo dia; agora
        # têm consumidor real (Treeview da biblioteca + scrollbars dela e do
        # painel ao vivo).
        s.configure("D.Treeview",
            background=CARD, foreground=TEXT, fieldbackground=CARD,
            borderwidth=0, relief="flat", rowheight=26, font=SEG_SM)
        s.configure("D.Treeview.Heading",
            background=BG, foreground=MUTED, relief="flat",
            font=SEG_XS, borderwidth=0, padding=(6, 4))
        s.map("D.Treeview",
            background=[("selected", CARD_A)],
            foreground=[("selected", TEXT)])
        s.map("D.Treeview.Heading",
            background=[("active", CARD)], relief=[("active", "flat")])
        s.configure("D.Vertical.TScrollbar",
            background=CARD_H, troughcolor=CARD, arrowcolor=SUBTLE,
            borderwidth=0, gripcount=0)

    # ── widget factories ───────────────────────────────────────────────────────
    def _icon(self, nome, cor, tamanho):
        """Máscara PNG (assets/icons/<nome>.png) tingida com `cor` em runtime —
        troca de tema pede a cor nova, sem retingir a antiga (o cache velho
        fica órfão e não atrapalha, roadmap/2026-08-12-icones-lucide.md).
        None se a máscara/Pillow faltar — o CALLER decide o fallback (nunca
        escondido aqui), nunca derruba o app."""
        if not HAS_PIL or not nome:
            return None
        chave = (nome, cor, tamanho)
        img = self._icon_cache.get(chave)
        if img is not None:
            return img
        try:
            caminho = _asset("assets", "icons", f"{nome}.png")
            mascara = Image.open(caminho).convert("RGBA")
            colorida = Image.new("RGBA", mascara.size, cor)
            colorida.putalpha(mascara.getchannel("A"))
            img = ImageTk.PhotoImage(colorida)
        except Exception:
            return None
        self._icon_cache[chave] = img
        return img

    def _btn(self, parent, text, cmd, primary=False, danger=False,
              icon=None, icon_size=20, **kw):
        if danger:
            bg, abg, fg = RED_C, _mix(RED_C, "#000000", 0.18), "#FFFFFF"
        elif primary:
            bg, abg, fg = ACCENT, _mix(ACCENT, "#000000", 0.14), ACCENT_FG
        else:
            bg, abg, fg = CARD, CARD_H, TEXT
        img = self._icon(icon, fg, icon_size) if icon else None
        if img is not None:
            kw.setdefault("image", img)
            # "none" por padrão: ícone-só substitui o `text` (fallback se a
            # máscara faltar) — quem quer ícone+texto lado a lado passa
            # compound="left" explicitamente na chamada.
            kw.setdefault("compound", "none")
        b = tk.Button(
            parent, text=text, command=cmd,
            bg=bg, fg=fg, activebackground=abg, activeforeground=fg,
            relief="flat", bd=0, cursor="hand2",
            font=SEG_SB if (primary or danger) else SEG,
            padx=12, pady=7, **kw)
        if img is not None:
            b.image = img   # referência viva — sem isso o GC apaga o ícone
        b._bg, b._abg = bg, abg
        b.bind("<Enter>", lambda e, b=b: b.config(bg=b._abg)
               if str(b.cget("state")) != "disabled" else None)
        b.bind("<Leave>", lambda e, b=b: b.config(bg=b._bg)
               if str(b.cget("state")) != "disabled" else None)
        return b

    def _link(self, parent, text, cmd, fg=None, font=None, icon=None, icon_size=16):
        # fg/font resolvidos no CORPO, não no default-arg (F13): um default-arg
        # avalia SUBTLE uma vez no import, com o tema escuro inicial — depois
        # de trocar pro tema claro, todo link sem fg explícito ficava preso na
        # cor do tema escuro.
        fg = fg or SUBTLE
        font = font or SEG_XS
        img = self._icon(icon, fg, icon_size) if icon else None
        kw = {}
        if img is not None:
            kw["image"] = img
            kw["compound"] = "left" if text else "none"
        lb = tk.Label(parent, text=text, bg=BG, fg=fg, cursor="hand2", font=font, **kw)
        if img is not None:
            lb.image = img   # referência viva — sem isso o GC apaga o ícone
        lb.bind("<Button-1>", lambda e: cmd())
        return lb

    def _link_recolor(self, lb, text, fg, icon=None, icon_size=16):
        """Reconfigura texto+cor (e o ícone, retingido na cor nova) de um link
        já criado — usado onde o texto muda dinamicamente (Opções/atalho)."""
        img = self._icon(icon, fg, icon_size) if icon else None
        lb.config(text=text, fg=fg, image=img or "",
                  compound=("left" if (img is not None and text) else "none"))
        lb.image = img

    # ── build ────────────────────────────────────────────────────────────────
    def _build(self):
        # Header doubles as the (custom) title bar: drag to move, controls at right.
        hdr = tk.Frame(self, bg=BG, height=42)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Frame(hdr, bg=ACCENT, width=4).pack(side="left", fill="y")
        title = tk.Label(hdr, text=APP_TITLE, bg=BG, fg=TEXT, font=SEG_LG)
        title.pack(side="left", padx=12)

        self._winbtn(hdr, "", self._on_close, RED_C).pack(side="right", fill="y")
        self._winbtn(hdr, "", self._minimize, CARD_H).pack(side="right", fill="y")

        for w in (hdr, title):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)
        self._body = body

        # Recording and transcription are mutually exclusive views — you do one or
        # the other, so the transcribe view replaces the recording view in place.
        self._rec_section = tk.Frame(body, bg=BG)
        self._rec_section.pack(fill="x")
        self._build_meters(self._rec_section)
        self._build_recording(self._rec_section)
        self._build_links(body)
        self._build_advanced(body)
        self._build_transcribe_section(body)
        self._build_convert_section(body)
        self._build_library_section(body)

    def _build_meters(self, body):
        vu_row = tk.Frame(body, bg=BG)
        vu_row.pack(fill="x", pady=(0, 10))
        self._vu_mult = {}
        for lbl, attr, src, cfg_key in [
                ("MIC", "_vu_mic", "mic", "mic_gain"),
                ("SISTEMA", "_vu_sys", "sys", "sys_gain")]:
            col = tk.Frame(vu_row, bg=BG)
            col.pack(side="left", fill="x", expand=True,
                     padx=(0, 5) if attr == "_vu_mic" else (5, 0))
            tk.Label(col, text=t(lbl), bg=BG, fg=SUBTLE, font=SEG_XS).pack(anchor="w")
            vu = VuMeter(col, on_gain=lambda g, s=src: self._on_gain(s, g),
                        on_release=lambda g, s=src: self._on_gain_release(s, g))
            vu.pack(fill="x")
            # gain multiplier readout, centered under the bar
            mult = tk.Label(col, text="", bg=BG, fg=MUTED, font=SEG_XS)
            mult.pack(pady=(2, 0))
            self._vu_mult[src] = mult
            vu.set_gain(self._cfg.get(cfg_key, 1.0))
            self._set_mult_label(src, vu.gain)
            setattr(self, attr, vu)

    def _set_mult_label(self, src, g):
        """Show the channel's current record gain as a multiplier (e.g. '1,0x')."""
        self._vu_mult[src].config(text=fmt_gain(g))

    def _on_gain(self, src, g):
        self._set_mult_label(src, g)
        if self._recorder:
            if src == "mic":
                self._recorder.set_gain(mic=g)
            else:
                self._recorder.set_gain(sys=g)

    def _on_gain_release(self, src, g):
        self._cfg["mic_gain" if src == "mic" else "sys_gain"] = round(g, 4)
        save_config(self._cfg)

    def _build_recording(self, body):
        self._btn_row = tk.Frame(body, bg=BG)
        self._btn_row.pack(fill="x")
        self._btn_row2 = tk.Frame(body, bg=BG)
        self._btn_row2.pack(fill="x", pady=(4, 0))

        self._btn_gravar   = self._btn(self._btn_row, t("Gravar"),
                                        self._start_rec, primary=True, icon="gravar",
                                        compound="left")
        self._btn_parar    = self._btn(self._btn_row, t("Parar"),
                                        self._stop_rec, danger=True, icon="parar",
                                        compound="left")
        self._btn_pausar   = self._btn(self._btn_row, "❚❚", self._pause_rec,
                                        icon="pausar")
        self._btn_seguir   = self._btn(self._btn_row, t("Continuar"),
                                        self._resume_rec, primary=True,
                                        icon="reproduzir", compound="left")

        # STOPPED state: compact icons. The icon itself performs the action;
        # hovering reveals a floating menu/caption over the interface (the window
        # never grows).
        self._ic_save = self._btn(self._btn_row, "⚡", self._conclude_and_transcribe,
                                  primary=True, icon="transcrever")
        self._ic_del  = self._btn(self._btn_row, "✕", self._conclude_delete,
                                  danger=True, icon="excluir")
        self._ic_play = self._btn(self._btn_row, "▶", self._play_recording,
                                  icon="reproduzir")
        # ⚡ : clickable menu (debounced hide so the mouse can move into it)
        self._ic_save.bind("<Enter>", lambda e: self._show_menu(self._ic_save, [
            ("Salvar + Transcrever", self._conclude_and_transcribe, "transcrever"),
            ("Salvar", self._conclude_save, "salvar"),
            ("Transcrever + excluir", self._conclude_transcribe_and_delete, "transcrever")]), add="+")
        self._ic_save.bind("<Leave>", lambda e: self._schedule_hide_pop(), add="+")
        # ✕ / ▶ : the icon acts on click; hover shows a non-clickable caption
        self._ic_del.bind("<Enter>", lambda e: self._show_tip(self._ic_del,
                                                              "Excluir", icon="excluir"), add="+")
        self._ic_del.bind("<Leave>", lambda e: self._hide_pop(), add="+")
        self._ic_play.bind("<Enter>", lambda e: self._show_tip(self._ic_play,
                                                               "Reproduzir", icon="reproduzir"), add="+")
        self._ic_play.bind("<Leave>", lambda e: self._hide_pop(), add="+")

        self._timer_var = tk.StringVar(value="00:00:00")
        self._timer_lbl = tk.Label(self._btn_row, textvariable=self._timer_var,
                                    bg=BG, fg=TEXT, font=("Segoe UI Semibold", 20))
        self._dot = tk.Label(self._btn_row, text=" ●",
                             bg=BG, fg=RED_C, font=("Segoe UI", 12))

        self._status_var = tk.StringVar(value=t("Pronto para gravar."))
        tk.Label(body, textvariable=self._status_var, bg=BG, fg=SUBTLE,
                 font=SEG_XS, wraplength=300, justify="left").pack(
                     anchor="w", pady=(6, 0))

        # Botão de cancelar para a transcrição disparada pelo ⚡ da própria tela
        # de gravação (F7) — sem isso, uma transcrição longa iniciada por aqui
        # não tinha como ser interrompida pela UI (o ⬛ Parar só existe na view
        # "tr"). Escondido por padrão; _rec_show_stop_tr o mostra/esconde.
        self._rec_stop_tr = self._btn(body, t("Parar"),
                                       self._stop_transcription, danger=True,
                                       icon="parar", compound="left")

        # Link de acesso direto ao .txt pronto (F10) — o produto do app é a
        # transcrição; antes só dava pra abrir indo pra view "tr" > "Abrir pasta".
        self._rec_open_txt = self._link(body, t("Abrir transcrição"),
                                        lambda: None, fg=ACCENT)

        # Painel de texto ao vivo (Fase 3) — só aparece durante uma gravação com
        # "live" ligado. Rola conforme chega; nenhum peso de fonte acima de 600
        # (Segoe UI Semibold = 600, CLAUDE.md § Convenções).
        self._live_frame = tk.Frame(body, bg=BG)
        self._live_text = tk.Text(
            self._live_frame, height=8, width=38, bg=CARD, fg=TEXT,
            font=SEG_SM, wrap="word", relief="flat", bd=0,
            highlightthickness=1, highlightbackground=BORDER, padx=8, pady=6,
            state="disabled")
        self._live_text.tag_configure("spk", foreground=ACCENT, font=SEG_SB)
        sb = ttk.Scrollbar(self._live_frame, command=self._live_text.yview,
                           style="D.Vertical.TScrollbar")
        self._live_text.configure(yscrollcommand=sb.set)
        self._live_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # não faz .pack() do _live_frame aqui — só quando a gravação com live
        # ligado começa (_start_rec/_after_stop controlam a visibilidade)

        self._set_rec_state(IDLE)

    def _live_clear(self):
        self._live_text.config(state="normal")
        self._live_text.delete("1.0", "end")
        self._live_text.config(state="disabled")

    def _live_append(self, spk: str, texto: str):
        self._live_text.config(state="normal")
        self._live_text.insert("end", f"{spk}: ", ("spk",))
        self._live_text.insert("end", f"{texto}\n")
        self._live_text.see("end")
        self._live_text.config(state="disabled")

    def _live_show(self, show: bool):
        if show:
            self._live_frame.pack(fill="both", expand=True, pady=(8, 0))
        else:
            self._live_frame.pack_forget()
        self.update_idletasks()
        self.geometry("")

    def _build_links(self, body):
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(10, 0))
        self._links_row = row
        self._adv_link = self._link(row, t("Opções"), self._toggle_advanced,
                                     icon="opcoes")
        self._adv_link.pack(side="left")
        # Packed right-to-left: Transcrever first, so Converter lands to its left.
        self._tr_link = self._link(row, t("Transcrever…"),
                                   lambda: self._toggle_view("tr"),
                                   fg=ACCENT, font=SEG_SM)
        self._tr_link.pack(side="right")
        self._cv_link = self._link(row, t("Converter…"),
                                   lambda: self._toggle_view("cv"),
                                   fg=ACCENT, font=SEG_SM)
        self._cv_link.pack(side="right", padx=(0, 14))
        self._lib_link = self._link(row, t("Gravações…"),
                                    lambda: self._toggle_view("lib"),
                                    fg=ACCENT, font=SEG_SM)
        self._lib_link.pack(side="right", padx=(0, 14))

    def _build_advanced(self, body):
        self._adv = tk.Frame(body, bg=BG)

        tk.Frame(self._adv, bg=BORDER, height=1).pack(fill="x", pady=(10, 8))

        for label, var_attr, cb_attr, cfg_key in [
            ("Entrada:", "_mic_var", "_mic_cb", "mic_device"),
            ("Saída:", "_sys_var", "_sys_cb", "sys_device"),
        ]:
            r = tk.Frame(self._adv, bg=BG)
            r.pack(fill="x", pady=2)
            tk.Label(r, text=t(label), bg=BG, fg=MUTED, font=SEG_SM,
                     width=9, anchor="w").pack(side="left")
            var = tk.StringVar()
            setattr(self, var_attr, var)
            cb = ttk.Combobox(r, textvariable=var, state="readonly",
                              style="D.TCombobox", font=SEG_SM, width=24)
            cb.pack(side="left")
            setattr(self, cb_attr, cb)
            var.trace_add("write",
                lambda *_, v=var, k=cfg_key: self._on_device_change(v, k))

        self._link(self._adv, t("Atualizar dispositivos"),
                   self._scan_devices, icon="atualizar").pack(anchor="w", pady=(2, 6))

        # Auto-save folder (defaults to Documents\Reco; changeable)
        frow = tk.Frame(self._adv, bg=BG)
        frow.pack(fill="x", pady=(0, 2))
        tk.Label(frow, text=t("Pasta:"), bg=BG, fg=SUBTLE,
                 font=SEG_XS).pack(side="left", padx=(0, 4))
        self._link(frow, t("Alterar…"), self._pick_output_dir,
                   fg=ACCENT, font=SEG_XS).pack(side="right")
        self._dir_var = tk.StringVar(value=str(self._out_dir))
        tk.Label(frow, textvariable=self._dir_var, bg=BG, fg=MUTED, font=SEG_XS,
                 anchor="w").pack(side="left", fill="x", expand=True)

        # Processing device. Diarization, echo cancellation and the model stay
        # automatic, but the device came back as a control on 29/07/2026 because
        # there is a real trade-off only the user can settle: the iGPU is the
        # fastest (2 h of audio in ~12 min vs ~19 on the NPU) but it is also the
        # only one competing with drawing the screen — under concurrent load it
        # lost 52% of its speed where the NPU lost 3%. Transcribing during a
        # video call is exactly when you want NPU.
        drow = tk.Frame(self._adv, bg=BG)
        drow.pack(fill="x", pady=(8, 0))
        tk.Label(drow, text=t("Processar em:"), bg=BG, fg=SUBTLE,
                 font=SEG_XS).pack(side="left", padx=(0, 4))
        self._dev_var = tk.StringVar(value=self._cfg.get("device", "AUTO"))
        self._dev_cb = ttk.Combobox(
            drow, textvariable=self._dev_var, state="readonly",
            values=["AUTO"] + [d for d in ("GPU", "NPU", "CPU")
                               if d in ov_available_devices()],
            style="XS.TCombobox", font=SEG_XS, width=8)
        self._dev_cb.pack(side="left")
        self._dev_var.trace_add("write", lambda *_: self._on_device_pref_change())

        # language selector
        lrow = tk.Frame(self._adv, bg=BG)
        lrow.pack(fill="x", pady=(8, 0))
        tk.Label(lrow, text=t("Idioma:"), bg=BG, fg=SUBTLE,
                 font=SEG_XS).pack(side="left", padx=(0, 4))
        self._lang_var = tk.StringVar(value=LANG_LABELS.get(LANG, "English"))
        self._lang_cb = ttk.Combobox(lrow, textvariable=self._lang_var,
                                     values=list(LANG_LABELS.values()),
                                     state="readonly", style="XS.TCombobox",
                                     font=SEG_XS, width=12)
        self._lang_cb.pack(side="left")
        self._lang_var.trace_add("write", lambda *_: self._on_lang_change())

        # Theme: pick background + accent (text colors auto-adjust for contrast)
        trow = tk.Frame(self._adv, bg=BG)
        trow.pack(fill="x", pady=(8, 0))
        tk.Label(trow, text=t("Tema:"), bg=BG, fg=SUBTLE,
                 font=SEG_XS).pack(side="left", padx=(0, 6))
        self._link(trow, t("Fundo"), self._pick_bg, fg=ACCENT,
                   font=SEG_XS).pack(side="left", padx=(0, 10))
        self._link(trow, t("Destaque"), self._pick_accent, fg=ACCENT,
                   font=SEG_XS).pack(side="left", padx=(0, 10))
        self._link(trow, t("Padrão"), self._reset_theme, fg=SUBTLE,
                   font=SEG_XS).pack(side="left")

        # Transcrição ao vivo (Fase 3, rascunho — Dec2): desligado por padrão,
        # consome o acelerador durante a gravação inteira. O checkbox fica
        # desabilitado durante a gravação — o modo vale para a PRÓXIMA sessão,
        # não muda no meio (self._live_was_on trava a escolha no início).
        lvrow = tk.Frame(self._adv, bg=BG)
        lvrow.pack(fill="x", pady=(8, 0))
        self._live_var = tk.BooleanVar(value=bool(self._cfg.get("live")))
        self._live_chk = tk.Checkbutton(
            lvrow, text=t("Transcrição ao vivo (rascunho)"),
            variable=self._live_var, command=self._on_live_toggle,
            bg=BG, fg=SUBTLE, activebackground=BG, activeforeground=TEXT,
            selectcolor=BG, highlightthickness=0, bd=0, font=SEG_XS)
        self._live_chk.pack(anchor="w")

        # Keyboard shortcut — opt-in (NOT created automatically by setup).
        # Windows-only: é um .lnk do Menu Iniciar com hotkey; sem sentido fora do nt.
        if os.name == "nt":
            self._sc_link = self._link(self._adv, "", self._toggle_shortcut)
            self._sc_link.pack(anchor="w", pady=(8, 0))
            self._update_shortcut_link()

        if not (HAS_SC and HAS_NP and HAS_AV):
            self._status(t("Captura indisponível — instale soundcard, numpy e av."))

    def _toggle_advanced(self):
        self._adv_shown = not self._adv_shown
        if self._adv_shown:
            self._adv.pack(fill="x")
            self._link_recolor(self._adv_link, t("Ocultar opções"), ACCENT, icon="opcoes")
        else:
            self._adv.pack_forget()
            self._link_recolor(self._adv_link, t("Opções"), SUBTLE, icon="opcoes")
        self.update_idletasks()
        self.geometry("")

    # ── config callbacks ─────────────────────────────────────────────────────
    def _on_device_change(self, var: tk.StringVar, cfg_key: str):
        devs = self._mic_devs if cfg_key == "mic_device" else self._sys_devs
        dev_id = id_for_name(devs, var.get())
        if dev_id:
            self._cfg[cfg_key] = dev_id
            save_config(self._cfg)

    def _on_device_pref_change(self):
        """Which accelerator runs Whisper. Takes effect on the next transcription:
        the live pipeline is keyed by (model, device), so OVTranscriber rebuilds
        it by itself when the key changes."""
        pref = (self._dev_var.get() or "AUTO").strip()
        if pref == self._cfg.get("device"):
            return
        self._cfg["device"] = pref
        save_config(self._cfg)
        if self._transcriber:          # None when deps for transcription are missing
            self._transcriber.set_device(pref)

    def _on_live_toggle(self):
        self._cfg["live"] = bool(self._live_var.get())
        save_config(self._cfg)

    def _on_lang_change(self):
        label = self._lang_var.get()
        code = next((c for c, lbl in LANG_LABELS.items() if lbl == label), None)
        if code:
            self._set_language(code)

    def _set_language(self, code):
        global LANG
        if code == LANG or self._state in (RECORDING, PAUSED, BUSY) or self._transcribing:
            return
        LANG = code
        self._cfg["language"] = code
        save_config(self._cfg)
        self._rebuild_ui()

    def _pick_output_dir(self):
        if self._state in (RECORDING, PAUSED, BUSY):
            return
        init = self._out_dir if self._out_dir.exists() else Path.home()
        d = filedialog.askdirectory(parent=self, title=t("Pasta de gravações"),
                                    initialdir=str(init))
        if d:
            self._out_dir = Path(d)
            self._cfg["output_dir"] = d
            save_config(self._cfg)
            self._dir_var.set(d)

    def _pick_bg(self):
        _, hx = colorchooser.askcolor(color=BG, parent=self, title=t("Cor de fundo"))
        if hx:
            self._set_theme(hx, ACCENT)

    def _pick_accent(self):
        _, hx = colorchooser.askcolor(color=ACCENT, parent=self,
                                      title=t("Cor de destaque"))
        if hx:
            self._set_theme(BG, hx)

    def _reset_theme(self):
        self._set_theme(DEFAULT_BG, DEFAULT_ACCENT)

    def _set_theme(self, bg, accent):
        if self._state in (RECORDING, PAUSED, BUSY) or self._transcribing:
            return
        apply_theme(bg, accent)
        self._cfg["bg_color"] = BG
        self._cfg["accent_color"] = ACCENT
        save_config(self._cfg)
        self.configure(bg=BG)
        self._rebuild_ui()

    def _rebuild_ui(self):
        # Rebuild the whole UI (used on language/theme change).
        for c in self.winfo_children():
            c.destroy()
        self._adv_shown = False
        self._view = "rec"
        self._apply_style()
        self._build()
        if self._tray:                # menu labels are language-dependent
            self._tray.remove()
            self._tray = None
            self._init_tray()
        self._toggle_advanced()       # keep Options open (where the controls live)
        self._scan_devices()
        self.update_idletasks()
        self.geometry("")

    # ── recording state machine ───────────────────────────────────────────────
    def _set_rec_state(self, state):
        self._hide_pop()
        self.after_idle(self._sync_tray)      # red-dot icon follows the state
        for w in self._btn_row.winfo_children():
            w.pack_forget()
        for w in self._btn_row2.winfo_children():
            w.pack_forget()

        if state == IDLE:
            self._btn_gravar.pack(side="left", padx=(0, 16))
            self._timer_lbl.pack(side="left")
        elif state == RECORDING:
            self._btn_parar.pack(side="left", padx=(0, 8))
            self._btn_pausar.pack(side="left", padx=(0, 16))
            self._timer_lbl.pack(side="left")
            self._dot.pack(side="left")
        elif state == PAUSED:
            self._btn_seguir.pack(side="left", padx=(0, 8))
            self._btn_parar.pack(side="left", padx=(0, 16))
            self._timer_lbl.pack(side="left")
            self._dot.pack(side="left")
        elif state == STOPPED:
            self._ic_save.pack(side="left", padx=(0, 8))
            self._ic_del.pack(side="left", padx=(0, 8))
            self._ic_play.pack(side="left")
            self._timer_var.set(self._final_dur)
            self._timer_lbl.pack(side="left", padx=(16, 0))
        elif state == BUSY:
            self._timer_lbl.pack(side="left")

        # Refit so the window shrinks back when a row empties out. Tk quirk: an
        # emptied frame keeps its old requested size, so force it small first.
        try:
            for fr in (self._btn_row, self._btn_row2):
                if not any(w.winfo_manager() for w in fr.winfo_children()):
                    fr.configure(width=1, height=1)
            self.update_idletasks()
            self.geometry("")
        except Exception:
            pass

    # ── hover popups for the compact STOPPED icons ──────────────────────────────
    def _make_pop(self, anchor):
        self._hide_pop()
        self._pop_anchor = anchor
        pop = tk.Toplevel(self, bg=BORDER)
        self._pop = pop
        pop.overrideredirect(True)
        try:
            pop.attributes("-topmost", True)
        except Exception:
            pass
        return pop

    def _place_pop(self, pop, anchor):
        pop.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 2
        pw = pop.winfo_reqwidth()
        sw = self.winfo_screenwidth()
        if x + pw > sw:
            x = max(0, sw - pw - 4)
        pop.geometry(f"+{x}+{y}")
        pop.lift()

    def _show_menu(self, anchor, items):
        # clickable menu (for the ⚡ icon); debounced hide so the mouse can move in
        self._cancel_hide_pop()
        if self._pop is not None and self._pop_anchor is anchor:
            return
        pop = self._make_pop(anchor)
        inner = tk.Frame(pop, bg=CARD)
        inner.pack(padx=1, pady=1)
        for label_key, cmd, *icon in items:
            img = self._icon(icon[0], TEXT, 16) if icon else None
            row = tk.Label(inner, text=t(label_key), bg=CARD, fg=TEXT, font=SEG_SM,
                           anchor="w", cursor="hand2", padx=12, pady=7,
                           image=img, compound="left" if img is not None else "none")
            row.image = img
            row.pack(fill="x")
            row.bind("<Enter>", lambda e, r=row: r.config(bg=CARD_H))
            row.bind("<Leave>", lambda e, r=row: r.config(bg=CARD))
            row.bind("<Button-1>", lambda e, c=cmd: self._pop_action(c))
        pop.bind("<Enter>", lambda e: self._cancel_hide_pop())
        pop.bind("<Leave>", lambda e: self._schedule_hide_pop())
        self._place_pop(pop, anchor)

    def _show_tip(self, anchor, text_key, icon=None):
        # non-clickable caption (for the ✕ / ▶ icons, which act on click)
        self._cancel_hide_pop()
        if self._pop is not None and self._pop_anchor is anchor:
            return
        pop = self._make_pop(anchor)
        img = self._icon(icon, TEXT, 16) if icon else None
        lb = tk.Label(pop, text=t(text_key), bg=CARD, fg=TEXT, font=SEG_SM,
                      padx=10, pady=5, image=img,
                      compound="left" if img is not None else "none")
        lb.image = img
        lb.pack(padx=1, pady=1)
        self._place_pop(pop, anchor)

    def _pop_action(self, cmd):
        self._hide_pop()
        self.after(1, cmd)               # defer so the popup is gone first

    def _hide_pop(self):
        self._pop_after = None
        self._pop_anchor = None
        if self._pop is not None:
            try:
                self._pop.destroy()
            except Exception:
                pass
            self._pop = None

    def _schedule_hide_pop(self):
        self._cancel_hide_pop()
        self._pop_after = self.after(220, self._hide_pop)

    def _cancel_hide_pop(self):
        if self._pop_after:
            try:
                self.after_cancel(self._pop_after)
            except Exception:
                pass
            self._pop_after = None

    # ── device scan ───────────────────────────────────────────────────────────
    def _scan_devices(self):
        if not (HAS_SC and HAS_NP):
            return
        if self._state in (RECORDING, PAUSED, BUSY):
            self._status(t("Não é possível atualizar dispositivos durante a gravação."))
            return
        self._status(t("Buscando dispositivos…"))
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        try:
            mics, spks = list_capture_devices()
            def_mic = default_mic_id()
            def_spk = default_speaker_id()
        except Exception as e:
            self._post(lambda msg=str(e):
                       self._status(tf("Erro ao listar dispositivos: {m}", m=msg)))
            return
        self._post(lambda: self._populate_devices(mics, spks, def_mic, def_spk))

    def _populate_devices(self, mics, spks, def_mic, def_spk):
        self._mic_devs = mics
        self._sys_devs = spks
        self._mic_cb["values"] = [n for _, n in mics]
        self._sys_cb["values"] = [n for _, n in spks]

        if mics:
            chosen = pick_device(mics, self._cfg.get("mic_device"), def_mic)
            self._mic_var.set(name_for_id(mics, chosen) or mics[0][1])
        else:
            self._mic_var.set("")

        if spks:
            chosen = pick_device(spks, self._cfg.get("sys_device"), def_spk)
            self._sys_var.set(name_for_id(spks, chosen) or spks[0][1])
        else:
            self._sys_var.set("")

        if not mics and not spks:
            self._status(t("Nenhum dispositivo de áudio encontrado."))
        elif not spks:
            self._status(t("Atenção: nenhuma saída de áudio para loopback."))
        else:
            self._status(t("Pronto para gravar."))

    # ── recording actions ──────────────────────────────────────────────────────
    def _out_settings(self) -> tuple:
        # Fixed format: 16 kHz stereo (L=mic, R=system) 96 kbps ABR — what
        # transcription, channel diarization and echo cancellation all need.
        return OUT_SR, OUT_CH, MP3_BR

    def _whisper_lang(self) -> str:
        return LANG

    def _start_rec(self):
        if not self._recorder:
            self._status(t("Captura indisponível — instale soundcard, numpy e av."))
            return
        mic_id = id_for_name(self._mic_devs, self._mic_var.get())
        sys_id = id_for_name(self._sys_devs, self._sys_var.get())
        if mic_id is None and sys_id is None:
            self._status(t("Nenhuma fonte de áudio — abra Opções."))
            return

        self._state    = RECORDING
        self._start_ts = time.monotonic()
        self._accum    = 0.0
        self._set_rec_state(RECORDING)
        self._vu_mic.reset()
        self._vu_sys.reset()
        self._set_combos_enabled(False)
        self._show_open_txt(self._rec_open_txt, None)  # limpa link do .txt anterior

        # Modo ao vivo (Fase 3, Dec2): rascunho durante a gravação, substituído
        # pela passada final ao parar. A escolha trava no início — mudar o
        # checkbox no meio da gravação não afeta a sessão em andamento.
        # `not self._transcribing` (F3): se uma transcrição de lote já estiver
        # rodando no MESMO WhisperPipeline, ligar o rascunho ao vivo geraria
        # duas chamadas concorrentes a pipe.generate — exatamente o que o
        # drain-then-start do stop existe para impedir.
        live_suprimido = bool(self._cfg.get("live")) and self._transcribing
        self._live_was_on = (bool(self._cfg.get("live")) and self._transcriber is not None
                              and not self._transcribing)
        on_pair = None
        if self._live_was_on:
            self._live = LiveTranscriber(self._transcriber, lang=self._whisper_lang())
            self._live_clear()
            self._live_show(True)
            self._live.start(
                on_text=lambda spk, txt: self._post(
                    lambda s=spk, x=txt: self._live_append(s, x)),
                on_warn=lambda msg: self._post(lambda m=msg: self._status(m)))
            on_pair = self._live.feed_pair
        else:
            self._live = None
            self._live_show(False)

        # Output settings go in at start(): the MP3 is written as we record, so
        # the file (and its name's timestamp) is created now, not at stop().
        sr, ch, br = self._out_settings()
        self._recorder.start(
            mic_id, sys_id,
            on_level=lambda src, rms: self._post(
                lambda s=src, r=rms: self._on_level(s, r)),
            on_error=lambda src, msg: self._post(
                lambda s=src, m=msg: self._on_stream_error(s, m)),
            on_pair=on_pair,
            out_sr=sr, out_channels=ch, bitrate=br, out_dir=self._out_dir)
        if not self._recorder.recording:        # couldn't open the file
            if self._live:
                self._live.stop(wait=False)
                self._live = None
            return

        self._tick_timer()
        self._blink_dot()
        if live_suprimido:
            self._status(t("Rascunho ao vivo desativado — transcrição em andamento."))
        else:
            self._status(t("Gravando…  (mic + sistema)"))

    def _set_combos_enabled(self, enabled):
        st = "readonly" if enabled else "disabled"
        for cb in (self._mic_cb, self._sys_cb, self._lang_cb):
            cb.config(state=st)

    def _on_stream_error(self, src, msg):
        if self._state != RECORDING:
            return
        if src == "save":                       # couldn't open the MP3 for writing
            erro = tf("Erro ao salvar: {m}", m=msg[:80])
            self._abort_to_idle(erro)
            self._balloon_if_hidden(erro)
            return
        which = t("microfone") if src == "mic" else t("áudio do sistema")
        if self._recorder and self._recorder.all_failed():
            erro = tf("Nenhuma fonte pôde ser capturada ({which}): {m}",
                      which=which, m=msg[:60])
            self._abort_to_idle(erro)
            self._balloon_if_hidden(erro)
        else:
            self._status(tf("Falha ao capturar {which} (a outra fonte continua).",
                            which=which))

    def _abort_to_idle(self, msg):
        if self._recorder and self._recorder.recording:
            self._recorder.abort()
        if self._live:
            self._live.stop(wait=False)      # gravação descartada — não precisa drenar
            self._live = None
        self._live_show(False)
        self._vu_mic.reset()
        self._vu_sys.reset()
        self._set_combos_enabled(True)
        self._state = IDLE
        self._set_rec_state(IDLE)
        self._timer_var.set("00:00:00")
        self._status(msg)

    def _stop_rec(self):
        if self._state == RECORDING:            # bank the running segment first
            self._accum += time.monotonic() - self._start_ts
        self._state = BUSY
        self._set_rec_state(BUSY)
        self._status(t("Salvando…"))

        def do_stop():
            try:
                path = self._recorder.stop(
                    progress=lambda m: self._post(lambda: self._status(m)))
                # Drain-then-start (Fase 3, decisão do roadmap): esvazia a fila
                # do rascunho ANTES de qualquer passada final poder rodar — nunca
                # duas chamadas a pipe.generate no mesmo WhisperPipeline ao
                # mesmo tempo (self._live é o único produtor daqui em diante,
                # já que o recorder parou de alimentar on_pair).
                if self._live:
                    self._post(lambda: self._status(t("Fechando rascunho ao vivo…")))
                    self._live.stop(wait=True, timeout=15.0, discard=True)
                self._post(lambda: self._after_stop(path))
            except NoAudioCaptured:
                if self._live:
                    self._live.stop(wait=False)
                self._post(lambda: self._after_stop_error(
                    t("Nenhum áudio capturado — verifique as fontes selecionadas.")))
            except Exception as e:
                if self._live:
                    self._live.stop(wait=False)
                self._post(lambda msg=str(e): self._after_stop_error(
                    tf("Erro ao salvar: {m}", m=msg)))
            finally:
                self._live = None

        threading.Thread(target=do_stop, daemon=True).start()

    def _after_stop_error(self, msg):
        self._live_show(False)
        self._set_combos_enabled(True)
        self._vu_mic.reset()
        self._vu_sys.reset()
        self._state = IDLE
        self._set_rec_state(IDLE)
        self._timer_var.set("00:00:00")
        self._status(msg[:110])

    def _after_stop(self, path: Path):
        self._last_rec = path
        self._final_dur = self._fmt_dur(self._elapsed())   # excludes paused time
        self._vu_mic.reset()
        self._vu_sys.reset()
        self._set_combos_enabled(True)
        self._state = STOPPED
        self._set_rec_state(STOPPED)
        if self._live_was_on:
            # Dec2: o texto ao vivo é RASCUNHO — a passada final roda agora,
            # com a máquina livre da disputa da gravação, e substitui o painel.
            self._status(tf("Salvo: {n}  —  refinando a transcrição…", n=path.name))
            self._run_live_final_pass(path)
        else:
            self._live_show(False)
            self._status(tf("Salvo: {n}  —  Escolha o que fazer:", n=path.name))
        self._balloon_if_hidden(tf("Salvo: {n}", n=path.name))

    def _balloon_if_hidden(self, msg: str):
        """Erro/conclusão com a janela na bandeja (F9): status numa janela
        `withdraw()` é invisível — o balão é o único jeito de o usuário saber."""
        if self._hidden and self._tray:
            self._tray.balloon("Reco", msg[:255])

    def _run_live_final_pass(self, path: Path):
        def done(text, err):
            if err:
                self._status(tf("Rascunho mantido — passada final falhou: {e}", e=err))
                return
            self._live_clear()
            txt = self._autosave_txt(path, text)
            for linha in (text or "").split("\n"):
                if not linha.strip():
                    continue
                if ": " in linha:
                    spk, _, resto = linha.partition(": ")
                    self._live_append(spk, resto)
                else:
                    self._live_append("", linha)
            if txt:
                self._status(tf("Transcrição final pronta: {n}", n=txt.name))
                self._show_open_txt(self._rec_open_txt, txt)
            else:
                self._status(t("Transcrição final pronta (falha ao salvar o .txt)."))
        self._show_open_txt(self._rec_open_txt, None)
        self._run_transcriber(path, self._status, done)

    def _conclude_save(self):
        self._state = IDLE
        self._set_rec_state(IDLE)
        self._timer_var.set("00:00:00")
        if self._last_rec:
            self._status(tf("Gravação salva: {n}", n=self._last_rec.name))
        else:
            self._status(t("Pronto para gravar."))

    def _conclude_and_transcribe(self):
        if self._transcribe_recording(delete_after=False):
            self._state = IDLE
            self._set_rec_state(IDLE)
            self._timer_var.set("00:00:00")

    def _conclude_delete(self):
        if self._last_rec and self._last_rec.exists():
            try:
                if not _excluir_gravacao(self._last_rec):
                    self._status(t("Não foi possível excluir.")); return
            except Exception as e:
                self._status(tf("Não foi possível excluir: {e}", e=e)); return
        self._last_rec = None
        self._state = IDLE
        self._set_rec_state(IDLE)
        self._timer_var.set("00:00:00")
        self._status(t("Gravação descartada."))

    def _conclude_transcribe_and_delete(self):
        if self._transcribe_recording(delete_after=True):
            self._state = IDLE
            self._set_rec_state(IDLE)
            self._timer_var.set("00:00:00")

    # ── timer / VU ──────────────────────────────────────────────────────────────
    def _elapsed(self) -> int:
        """Seconds of audio actually captured — paused time doesn't count."""
        run = (time.monotonic() - self._start_ts) if self._state == RECORDING else 0.0
        return int(self._accum + run)

    def _fmt_dur(self, secs: int) -> str:
        h, r = divmod(int(secs), 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _tick_timer(self):
        # Cancela o `after` pendente antes de reagendar (F16) — sem isso,
        # pausar/retomar antes do tick disparar duplicava o loop (o cosmético
        # "pisca-pisca acelera").
        if self._timer_after_id is not None:
            self.after_cancel(self._timer_after_id)
            self._timer_after_id = None
        if self._state != RECORDING:
            return
        self._timer_var.set(self._fmt_dur(self._elapsed()))
        self._sync_tray()             # tray tooltip counts up while hidden
        self._timer_after_id = self.after(1000, self._tick_timer)

    def _blink_dot(self):
        if self._dot_after_id is not None:
            self.after_cancel(self._dot_after_id)
            self._dot_after_id = None
        if self._state == PAUSED:
            self._dot.config(fg=AMBER)        # steady amber = paused
            return
        if self._state != RECORDING:
            return
        fg = self._dot.cget("fg")
        self._dot.config(fg=BG if fg == RED_C else RED_C)
        self._dot_after_id = self.after(600, self._blink_dot)

    # ── pause / resume ──────────────────────────────────────────────────────────
    def _pause_rec(self):
        if self._state != RECORDING or not self._recorder:
            return
        self._accum += time.monotonic() - self._start_ts   # freeze the clock
        self._recorder.pause()
        self._state = PAUSED
        self._set_rec_state(PAUSED)
        self._timer_var.set(self._fmt_dur(self._elapsed()))
        self._vu_mic.reset()
        self._vu_sys.reset()
        self._blink_dot()
        self._status(tf("Pausado — {d} gravado.", d=self._timer_var.get()))

    def _resume_rec(self):
        if self._state != PAUSED or not self._recorder:
            return
        self._start_ts = time.monotonic()
        self._recorder.resume()
        self._state = RECORDING
        self._set_rec_state(RECORDING)
        self._tick_timer()
        self._blink_dot()
        self._status(t("Gravando…  (mic + sistema)"))

    def _on_level(self, src, rms):
        if src == "mic":
            self._vu_mic.update_level(rms)
        else:
            self._vu_sys.update_level(rms)

    # ── transcription core (auto-saves .txt next to the audio file) ─────────────
    def _autosave_txt(self, audio_path: Path, text: str):
        try:
            txt = audio_path.with_suffix(".txt")
            txt.write_text(text or t("(nenhum conteúdo reconhecido)"), encoding="utf-8")
            return txt
        except Exception as e:
            print(f"[txt] {e}")
            return None

    def _stop_transcription(self):
        if not self._transcribing:
            return
        if self._transcriber:
            self._transcriber.cancel()

    def _run_transcriber(self, path: Path, status_cb, done_cb) -> bool:
        if not path or not path.exists():
            status_cb(t("Arquivo não encontrado."))
            return False
        if self._transcribing:
            status_cb(t("Já há uma transcrição em andamento."))
            return False
        if not self._transcriber:
            status_cb(t("Transcrição indisponível — instale openvino-genai."))
            return False

        self._transcribing = True
        status_cb(tf("Transcrevendo {n}…", n=path.name))
        self._transcriber.set_model(self._cfg.get("model", _CFG_DEFAULTS["model"]))
        self._transcriber.set_device(self._cfg.get("device", "AUTO"))

        # Channel diarization + echo cancellation only apply to Reco's own
        # mic+system recordings; any other file is transcribed plainly.
        reco_rec = is_reco_recording(path)
        diarize = bool(self._cfg.get("diarize")) and reco_rec
        aec     = bool(self._cfg.get("aec")) and reco_rec

        def _done(text, err):
            self._transcribing = False
            done_cb(text, err)

        self._transcriber.transcribe(
            path, lang=self._whisper_lang(),
            diarize=diarize, aec=aec,
            progress_cb=lambda m: self._post(lambda: status_cb(m)),
            done_cb=lambda t_, e: self._post(lambda: _done(t_, e)))
        return True

    def _transcribe_recording(self, delete_after=False) -> bool:
        path = self._last_rec
        if not path or not path.exists():
            self._status(t("Nada para transcrever."))
            return False

        def done(text, err):
            self._rec_show_stop_tr(False)
            if err == CANCELLED:
                self._status(t("Transcrição cancelada."))
                return
            if err:
                self._status(tf("Erro na transcrição: {e}", e=err))
                return
            txt = self._autosave_txt(path, text)
            if not txt:
                self._status(t("Transcrito, mas falha ao salvar o .txt."))
                return
            if delete_after:
                try:
                    _excluir_gravacao(path)
                except Exception:
                    pass
                self._status(tf("Transcrição salva: {n}. Áudio excluído.", n=txt.name))
            else:
                self._status(tf("Transcrição salva: {n}", n=txt.name))
            self._show_open_txt(self._rec_open_txt, txt)

        self._show_open_txt(self._rec_open_txt, None)
        if self._run_transcriber(path, self._status, done):
            self._rec_show_stop_tr(True)
            return True
        return False

    # ── inline transcribe section (expands the window; no separate window) ───────
    def _build_transcribe_section(self, body):
        sec = tk.Frame(body, bg=BG)
        self._tr_section = sec
        tk.Label(sec, text=t("TRANSCRIÇÃO"), bg=BG, fg=SUBTLE,
                 font=SEG_XS).pack(anchor="w", pady=(0, 6))

        nav = tk.Frame(sec, bg=BG)
        nav.pack(fill="x")
        self._link(nav, t("Escolher arquivo…"), self._tr_browse,
                   fg=ACCENT, font=SEG_SM, icon="escolher_arquivo").pack(side="left")
        self._link(nav, t("Abrir pasta"), self._open_tr_folder,
                   font=SEG_SM).pack(side="right")

        self._tr_path_var = tk.StringVar(value=str(self._tr_sel) if self._tr_sel else "")
        tk.Label(sec, textvariable=self._tr_path_var, bg=BG, fg=SUBTLE,
                 font=SEG_XS, wraplength=300, justify="left").pack(
                     anchor="w", pady=(4, 0))

        arow = tk.Frame(sec, bg=BG)
        arow.pack(fill="x", pady=(8, 0))
        self._tr_btn = self._btn(arow, t("Transcrever"),
                                 self._tr_transcribe, primary=True, icon="transcrever",
                                 compound="left")
        self._tr_btn.pack(side="left", padx=(0, 8))
        self._tr_stop = self._btn(arow, t("Parar"),
                                  self._stop_transcription, danger=True, icon="parar",
                                  compound="left")
        # _tr_stop is packed only while transcribing

        self._tr_status_var = tk.StringVar(
            value=t("Selecione um arquivo e clique em Transcrever."))
        tk.Label(sec, textvariable=self._tr_status_var, bg=BG, fg=SUBTLE,
                 font=SEG_XS, wraplength=300, justify="left").pack(
                     anchor="w", pady=(8, 0))

        # Link de acesso direto ao .txt pronto (F10) — mesmo padrão da view rec.
        self._tr_open_txt = self._link(sec, t("Abrir transcrição"),
                                       lambda: None, fg=ACCENT)

    def _toggle_view(self, view: str):
        # Record / transcribe / convert are exclusive views — one replaces the
        # other in place. Clicking the link of the current view goes back to Record.
        self._show_view("rec" if self._view == view else view)

    def _show_view(self, view: str):
        if self._state in (RECORDING, PAUSED, BUSY):
            self._status(t("Termine a gravação para trocar de tela."))
            return
        if self._transcribing:
            self._status(t("Termine a transcrição para trocar de tela."))
            return
        if getattr(self, "_extracting", False):
            self._status(t("Termine a conversão para trocar de tela."))
            return
        if view == "tr" and not self._tr_sel and self._last_rec and self._last_rec.exists():
            self._tr_sel = self._last_rec
            self._tr_path_var.set(str(self._tr_sel))

        for sec in (self._rec_section, self._tr_section, self._cv_section,
                    self._lib_section):
            sec.pack_forget()
        self._view = view
        {"rec": self._rec_section,
         "tr":  self._tr_section,
         "cv":  self._cv_section,
         "lib": self._lib_section}[view].pack(fill="x", before=self._links_row)

        back = t("← Gravar")
        self._tr_link.config(text=back if view == "tr" else t("Transcrever…"))
        self._cv_link.config(text=back if view == "cv" else t("Converter…"))
        self._lib_link.config(text=back if view == "lib" else t("Gravações…"))
        if view == "lib":
            self._lib_scan()          # a pasta pode ter mudado desde a última visita
        self.update_idletasks()
        self.geometry("")

    def _tr_set_status(self, msg):
        self._tr_status_var.set(msg)

    def _tr_show_stop(self, on):
        if on:
            self._tr_btn.config(state="disabled")
            self._tr_stop.pack(side="left")
        else:
            self._tr_stop.pack_forget()
            self._tr_btn.config(state="normal")

    def _rec_show_stop_tr(self, on):
        if on:
            self._rec_stop_tr.pack(anchor="w", pady=(6, 0))
        else:
            self._rec_stop_tr.pack_forget()

    def _show_open_txt(self, link, txt_path):
        """Mostra o link "Abrir transcrição" (F10) apontando pro .txt pronto;
        `txt_path=None` esconde (nova transcrição iniciada, erro etc.)."""
        link.pack_forget()
        if txt_path is None:
            return
        link.bind("<Button-1>", lambda e, p=txt_path: _abrir_arquivo(p))
        link.pack(anchor="w", pady=(4, 0))

    def _tr_browse(self):
        media = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS | VIDEO_EXTS))
        p = filedialog.askopenfilename(
            title=t("Selecionar áudio"),
            filetypes=[(f'{t("Áudio")} / {t("Vídeo")}', media),
                       (t("Áudio"), " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))),
                       (t("Vídeo"), " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))),
                       (t("Todos"), "*.*")])
        if not p:
            return
        self._tr_sel = Path(p)
        self._tr_path_var.set(str(self._tr_sel))
        self._tr_set_status(t("Selecione um arquivo e clique em Transcrever."))

    # ── recordings library (view "Gravações…") ──────────────────────────────────
    # Mesa mínima que Meetily/anarlog provaram: biblioteca das gravações passadas
    # com busca por nome E por conteúdo do transcript, e as ações de sempre por
    # ícone. O filesystem é o banco (mp3 + .txt + .resumo.md lado a lado) —
    # greppável e portátil, sem SQLite de propósito.
    def _build_library_section(self, body):
        sec = tk.Frame(body, bg=BG)
        self._lib_section = sec
        tk.Label(sec, text=t("GRAVAÇÕES"), bg=BG, fg=SUBTLE,
                 font=SEG_XS).pack(anchor="w", pady=(0, 6))

        nav = tk.Frame(sec, bg=BG)
        nav.pack(fill="x")
        tk.Label(nav, text=t("Buscar:"), bg=BG, fg=SUBTLE,
                 font=SEG_XS).pack(side="left", padx=(0, 4))
        self._lib_query = tk.StringVar()
        ent = tk.Entry(nav, textvariable=self._lib_query, bg=CARD, fg=TEXT,
                       insertbackground=TEXT, relief="flat",
                       highlightthickness=1, highlightbackground=BORDER,
                       highlightcolor=ACCENT, font=SEG_SM)
        ent.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._lib_query.trace_add("write", lambda *_: self._lib_refilter_soon())
        self._link(nav, t("Atualizar"), self._lib_scan,
                   icon="atualizar").pack(side="right")

        wrap = tk.Frame(sec, bg=BG)
        wrap.pack(fill="both", expand=True, pady=(6, 0))
        tree = ttk.Treeview(wrap, columns=("nome", "data", "dur", "txt"),
                            show="headings", style="D.Treeview", height=8,
                            selectmode="browse")
        for cid, lbl, w, anchor in (
                ("nome", t("Arquivo"), 150, "w"),
                ("data", t("Data"), 76, "w"),
                ("dur", t("Duração"), 56, "e"),
                ("txt", "📄", 26, "center")):
            tree.heading(cid, text=lbl, anchor=anchor)
            tree.column(cid, width=w, anchor=anchor, stretch=(cid == "nome"))
        sb = ttk.Scrollbar(wrap, command=tree.yview,
                           style="D.Vertical.TScrollbar")
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tree.bind("<<TreeviewSelect>>", lambda e: self._lib_sync_actions())
        tree.bind("<Double-1>", lambda e: self._lib_play())
        self._lib_tree = tree

        arow = tk.Frame(sec, bg=BG)
        arow.pack(fill="x", pady=(8, 0))
        self._lib_play_btn = self._lib_action(arow, "▶", self._lib_play,
                                              "Reproduzir", icon="reproduzir")
        self._lib_tr_btn = self._lib_action(arow, "⚡", self._lib_transcribe,
                                            "Transcrever", icon="transcrever",
                                            primary=True)
        self._lib_txt_btn = self._lib_action(arow, "📄", self._lib_open_txt,
                                             "Abrir transcrição",
                                             icon="abrir_transcricao")
        self._lib_sum_btn = self._lib_action(arow, "✦", self._lib_resumo,
                                             "Resumo IA", icon="resumo_ia")
        self._lib_del_btn = self._lib_action(arow, "✕", self._lib_delete,
                                             "Excluir", icon="excluir", danger=True)
        self._lib_stop = self._btn(arow, t("Parar"),
                                   self._stop_transcription, danger=True, icon="parar",
                                   compound="left")
        # _lib_stop só é empacotado enquanto uma transcrição desta view roda
        self._link(arow, t("Abrir pasta"), self._lib_open_folder,
                   font=SEG_SM).pack(side="right")

        self._lib_status_var = tk.StringVar(value=t("Selecione uma gravação."))
        tk.Label(sec, textvariable=self._lib_status_var, bg=BG, fg=SUBTLE,
                 font=SEG_XS, wraplength=300, justify="left").pack(
                     anchor="w", pady=(8, 0))
        self._lib_sync_actions()

    def _lib_action(self, parent, glyph, cmd, tip_key, icon=None, **kw):
        """Botão-ícone da biblioteca: age no clique, caption no hover (mesmo
        padrão dos ícones do estado STOPPED — a janela nunca cresce). `icon`
        (nome Lucide) tem prioridade sobre `glyph` — o emoji vira só o
        fallback se a máscara/Pillow faltar (mesmo mecanismo de `_btn`)."""
        b = self._btn(parent, glyph, cmd, icon=icon, **kw)
        b.pack(side="left", padx=(0, 8))
        b.bind("<Enter>", lambda e: self._show_tip(b, tip_key, icon=icon), add="+")
        b.bind("<Leave>", lambda e: self._hide_pop(), add="+")
        return b

    def _lib_set_status(self, msg):
        self._lib_status_var.set(msg)

    def _lib_sel(self) -> Path | None:
        sel = self._lib_tree.selection()
        info = self._lib_rows.get(sel[0]) if sel else None
        return info["path"] if info else None

    def _lib_sync_actions(self):
        p = self._lib_sel()
        txt = p.with_suffix(".txt") if p else None
        has_txt = bool(txt and txt.exists())
        for b, on in ((self._lib_play_btn, p is not None),
                      (self._lib_tr_btn, p is not None and not self._transcribing),
                      (self._lib_txt_btn, has_txt),
                      (self._lib_sum_btn, has_txt and not self._resumindo),
                      (self._lib_del_btn, p is not None)):
            b.config(state="normal" if on else "disabled")

    # ── scan (thread) ──────────────────────────────────────────────────────────
    def _lib_scan(self):
        if self._lib_scanning:
            return
        self._lib_scanning = True
        self._lib_set_status(t("Lendo gravações…"))
        threading.Thread(target=self._lib_scan_thread, args=(self._out_dir,),
                         daemon=True).start()

    def _lib_scan_thread(self, folder: Path):
        rows = []
        try:
            files = sorted((p for p in folder.iterdir()
                            if p.suffix.lower() in AUDIO_EXTS),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            files = []
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue                       # sumiu entre o listdir e o stat
            rows.append({
                "path": p, "mtime": st.st_mtime,
                "data": datetime.datetime.fromtimestamp(st.st_mtime)
                        .strftime("%d/%m %H:%M"),
                "dur": self._lib_duration(p, st.st_mtime),
                "txt": p.with_suffix(".txt").exists(),
            })
        self._post(lambda: self._lib_populate(rows))

    def _lib_duration(self, p: Path, mtime: float) -> str:
        """Duração declarada pelo container (instantânea com header Xing).
        Cache por (path, mtime): reabrir dezenas de MP3 a cada visita da view
        seria o único custo perceptível da biblioteca."""
        key = (str(p), mtime)
        dur = self._dur_cache.get(key)
        if dur is None:
            try:
                import av
                with av.open(str(p)) as cont:
                    dur = (float(cont.duration) / av.time_base
                           if cont.duration else 0.0)
            except Exception:
                dur = -1.0
            self._dur_cache[key] = dur
        return "—" if dur < 0 else self._fmt_dur(int(dur))

    def _lib_populate(self, rows):
        self._lib_scanning = False
        self._lib_all = rows
        self._lib_refilter()

    # ── busca (debounced; nome + conteúdo do .txt) ─────────────────────────────
    def _lib_refilter_soon(self):
        if self._lib_search_after:
            try:
                self.after_cancel(self._lib_search_after)
            except Exception:
                pass
        self._lib_search_after = self.after(250, self._lib_refilter)

    def _lib_txt_content(self, p: Path, mtime: float) -> str:
        key = (str(p), mtime)
        s = self._txt_cache.get(key)
        if s is None:
            try:
                s = p.with_suffix(".txt").read_text(
                    encoding="utf-8", errors="ignore").casefold()
            except OSError:
                s = ""
            self._txt_cache[key] = s
        return s

    def _lib_refilter(self):
        self._lib_search_after = None
        q = (self._lib_query.get() or "").strip().casefold()
        tree = self._lib_tree
        tree.delete(*tree.get_children())
        self._lib_rows = {}
        for r in self._lib_all:
            if q:
                hit = q in r["path"].name.casefold()
                # Conteúdo também — é o que acha "aquela reunião em que falamos
                # de X" sem lembrar a data (a busca do Meetily faz o mesmo).
                if not hit and r["txt"]:
                    hit = q in self._lib_txt_content(r["path"], r["mtime"])
                if not hit:
                    continue
            iid = tree.insert("", "end", values=(
                r["path"].name, r["data"], r["dur"], "✓" if r["txt"] else ""))
            self._lib_rows[iid] = r
        self._lib_set_status(t("Selecione uma gravação.") if self._lib_rows
                             else t("Nenhuma gravação encontrada."))
        self._lib_sync_actions()

    # ── ações ──────────────────────────────────────────────────────────────────
    def _lib_play(self):
        p = self._lib_sel()
        if p and p.exists():
            try:
                _abrir_arquivo(p)
            except Exception as e:
                self._lib_set_status(tf("Erro: {e}", e=e))

    def _lib_open_txt(self):
        p = self._lib_sel()
        txt = p.with_suffix(".txt") if p else None
        if txt and txt.exists():
            try:
                _abrir_arquivo(txt)
            except Exception as e:
                self._lib_set_status(tf("Erro: {e}", e=e))

    def _lib_open_folder(self):
        try:
            self._out_dir.mkdir(parents=True, exist_ok=True)
            _abrir_arquivo(self._out_dir)
        except Exception:
            pass

    def _lib_transcribe(self):
        p = self._lib_sel()
        if not p:
            return

        def done(text, err):
            self._lib_stop.pack_forget()
            if err == CANCELLED:
                self._lib_set_status(t("Transcrição cancelada."))
            elif err:
                self._lib_set_status(tf("Erro: {e}", e=err))
            else:
                txt = self._autosave_txt(p, text)
                if txt:
                    self._lib_set_status(tf("Transcrição salva: {n}", n=txt.name))
                    self._lib_mark_txt(p)
                else:
                    self._lib_set_status(t("Transcrito, mas falha ao salvar o .txt."))
            self._lib_sync_actions()

        if self._run_transcriber(p, self._lib_set_status, done):
            self._lib_stop.pack(side="left")
            self._lib_sync_actions()

    def _lib_mark_txt(self, p: Path):
        """Atualiza a coluna 📄 da linha de `p` sem re-escanear a pasta."""
        for iid, r in self._lib_rows.items():
            if r["path"] == p:
                r["txt"] = True
                vals = list(self._lib_tree.item(iid, "values"))
                vals[3] = "✓"
                self._lib_tree.item(iid, values=vals)
                break

    def _lib_delete(self):
        # Só o áudio vai pra Lixeira — o .txt/.resumo.md ficam: o transcript
        # sobrevive ao MP3 (é ele o produto; Meetily/anarlog fazem igual).
        p = self._lib_sel()
        if not p:
            return
        try:
            if not _excluir_gravacao(p):
                self._lib_set_status(tf("Não foi possível excluir: {e}", e=p.name))
                return
        except Exception as e:
            self._lib_set_status(tf("Não foi possível excluir: {e}", e=e))
            return
        if self._last_rec == p:
            self._last_rec = None
        sel = self._lib_tree.selection()
        if sel:
            self._lib_rows.pop(sel[0], None)
            self._lib_tree.delete(sel[0])
        self._lib_all = [r for r in self._lib_all if r["path"] != p]
        self._lib_set_status(tf("Enviada para a Lixeira: {n}", n=p.name))
        self._lib_sync_actions()

    # ── resumo IA (✦, opt-in por clique — nunca automático: consome o Claude
    # do usuário). Se o .resumo.md já existe, abre; para refazer, exclua-o. ────
    def _lib_resumo(self):
        p = self._lib_sel()
        txt = p.with_suffix(".txt") if p else None
        if not txt or not txt.exists():
            return
        resumo = p.with_suffix(".resumo.md")
        if resumo.exists():
            try:
                _abrir_arquivo(resumo)
            except Exception as e:
                self._lib_set_status(tf("Erro: {e}", e=e))
            return
        if self._resumindo:
            self._lib_set_status(t("Já há um resumo em andamento."))
            return
        cli = shutil.which("claude")
        if not cli:
            self._lib_set_status(
                t("Resumo indisponível — instale o Claude Code (comando 'claude')."))
            return
        self._resumindo = True
        self._lib_sync_actions()
        self._lib_set_status(t("Gerando resumo com o Claude…"))

        def run():
            try:
                texto = txt.read_text(encoding="utf-8", errors="ignore")
                # --model sonnet, cravado: sem a flag, o `claude -p` herda o
                # default da máquina — aqui era Fable 5 (contexto 1M) em effort
                # xhigh, o tier mais caro que existe, para um resumo de reunião.
                # Sonnet é o meio-termo certo pra sumarização; quem preferir
                # haiku (mais barato) ou o default da máquina, troca AQUI.
                r = subprocess.run(
                    [cli, "-p", PROMPT_RESUMO.get(LANG, PROMPT_RESUMO["en"]),
                     "--model", "sonnet"],
                    input=texto, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=900,
                    **_no_window_kwargs())
                out = (r.stdout or "").strip()
                if r.returncode != 0 or not out:
                    raise RuntimeError((r.stderr or "").strip()[:80]
                                       or f"código {r.returncode}")
                resumo.write_text(out + "\n", encoding="utf-8")
            except Exception as e:
                self._post(lambda m=str(e)[:110]: self._lib_resumo_done(None, m))
                return
            self._post(lambda: self._lib_resumo_done(resumo, None))

        threading.Thread(target=run, daemon=True).start()

    def _lib_resumo_done(self, resumo, err):
        self._resumindo = False
        self._lib_sync_actions()
        if err:
            self._lib_set_status(tf("Falha ao gerar o resumo: {e}", e=err))
            return
        self._lib_set_status(tf("Resumo salvo: {n}", n=resumo.name))
        try:
            _abrir_arquivo(resumo)
        except Exception:
            pass

    # ── convert section (MP4/… → MP3; also shrinks a heavy audio file) ──────────
    def _build_convert_section(self, body):
        sec = tk.Frame(body, bg=BG)
        self._cv_section = sec
        tk.Label(sec, text=t("CONVERSÃO"), bg=BG, fg=SUBTLE,
                 font=SEG_XS).pack(anchor="w", pady=(0, 6))

        nav = tk.Frame(sec, bg=BG)
        nav.pack(fill="x")
        self._link(nav, t("Escolher vídeo ou áudio…"), self._cv_browse,
                   fg=ACCENT, font=SEG_SM, icon="escolher_arquivo").pack(side="left")
        self._link(nav, t("Abrir pasta"), self._open_cv_folder,
                   font=SEG_SM).pack(side="right")

        self._cv_path_var = tk.StringVar(value="")
        tk.Label(sec, textvariable=self._cv_path_var, bg=BG, fg=SUBTLE,
                 font=SEG_XS, wraplength=300, justify="left").pack(
                     anchor="w", pady=(4, 0))

        arow = tk.Frame(sec, bg=BG)
        arow.pack(fill="x", pady=(8, 0))
        self._cv_btn = self._btn(arow, t("Converter para MP3"),
                                 self._cv_convert, primary=True, icon="converter_mp3",
                                 compound="left")
        self._cv_btn.pack(side="left")

        self._cv_status_var = tk.StringVar(
            value=tf("MP3 leve: {sr} kHz mono, {br} kbps.",
                     sr=OUT_SR // 1000, br=EXTRACT_BR))
        tk.Label(sec, textvariable=self._cv_status_var, bg=BG, fg=SUBTLE,
                 font=SEG_XS, wraplength=300, justify="left").pack(
                     anchor="w", pady=(8, 0))

    def _cv_set_status(self, msg):
        self._cv_status_var.set(msg)

    def _cv_browse(self):
        media = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS | VIDEO_EXTS))
        p = filedialog.askopenfilename(
            title=t("Selecionar vídeo ou áudio"),
            filetypes=[(f'{t("Vídeo")} / {t("Áudio")}', media),
                       (t("Vídeo"), " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))),
                       (t("Áudio"), " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))),
                       (t("Todos"), "*.*")])
        if not p:
            return
        self._cv_sel = Path(p)
        self._cv_path_var.set(str(self._cv_sel))
        self._cv_set_status(tf("Origem: {a}", a=_fmt_size(self._cv_sel.stat().st_size)))

    def _open_cv_folder(self):
        folder = self._cv_sel.parent if self._cv_sel else self._out_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
            _abrir_arquivo(folder)
        except Exception:
            pass

    def _cv_convert(self):
        path = self._cv_sel
        if not path or not path.exists():
            self._cv_set_status(t("Selecione um arquivo válido."))
            return
        if not HAS_AV:
            self._cv_set_status(t("Conversão indisponível — instale av."))
            return
        if self._extracting:
            self._cv_set_status(t("Já há uma conversão em andamento."))
            return

        self._extracting = True
        self._cv_btn.config(state="disabled")
        self._cv_set_status(tf("Convertendo… {p}%", p=0))
        src_size = path.stat().st_size

        def done(msg, out: Path | None):
            self._extracting = False
            self._cv_btn.config(state="normal")
            self._cv_set_status(msg)
            if out:            # chain: the MP3 is what you'd transcribe next
                self._tr_sel = out
                self._tr_path_var.set(str(out))

        def run():
            try:
                out = extract_mp3(
                    path,
                    progress=lambda p: self._post(
                        lambda p=p: self._cv_set_status(
                            tf("Convertendo… {p}%", p=p))))
                msg = tf("MP3 salvo: {n}  ({a} → {b})", n=out.name,
                         a=_fmt_size(src_size), b=_fmt_size(out.stat().st_size))
                self._post(lambda: done(msg, out))
            except NoAudioStream:
                self._post(lambda: done(t("O arquivo não tem faixa de áudio."), None))
            except Exception as e:
                self._post(lambda m=str(e)[:80]: done(
                    tf("Falha ao converter: {e}", e=m), None))

        threading.Thread(target=run, daemon=True).start()

    def _tr_transcribe(self):
        path = self._tr_sel
        if not path or not path.exists():
            self._tr_set_status(t("Selecione um arquivo válido."))
            return

        def done(text, err):
            self._tr_show_stop(False)
            if err == CANCELLED:
                self._tr_set_status(t("Transcrição cancelada."))
                return
            if err:
                self._tr_set_status(tf("Erro: {e}", e=err))
                return
            txt = self._autosave_txt(path, text)
            self._tr_set_status(tf("Salvo: {n}", n=txt.name) if txt
                                else t("Transcrito, mas falha ao salvar o .txt."))
            self._show_open_txt(self._tr_open_txt, txt)

        self._show_open_txt(self._tr_open_txt, None)
        if self._run_transcriber(path, self._tr_set_status, done):
            self._tr_show_stop(True)

    def _open_tr_folder(self):
        folder = self._tr_sel.parent if self._tr_sel else self._out_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
            _abrir_arquivo(folder)
        except Exception:
            pass

    def _play_recording(self):
        if self._last_rec and self._last_rec.exists():
            try:
                _abrir_arquivo(self._last_rec)   # default audio player
            except Exception as e:
                self._status(tf("Erro: {e}", e=e))
        else:
            self._status(t("Nada para reproduzir."))

    # ── keyboard shortcut (opt-in, created from here — never automatically) ──────
    def _shortcut_path(self) -> Path:
        base = os.environ.get("APPDATA") or str(Path.home())
        return (Path(base) / "Microsoft" / "Windows" / "Start Menu" /
                "Programs" / f"{APP_NAME}.lnk")

    def _update_shortcut_link(self):
        exists = self._shortcut_path().exists()
        self._link_recolor(
            self._sc_link,
            t("Remover atalho") if exists else t("Criar atalho (Ctrl+Shift+R)"),
            ACCENT if exists else SUBTLE, icon="atalho")

    def _toggle_shortcut(self):
        lnk = self._shortcut_path()
        try:
            if lnk.exists():
                lnk.unlink()
                self._status(t("Atalho removido."))
            else:
                self._create_shortcut(lnk)
                self._status(t("Atalho criado — abra pelo Menu Iniciar ou com Ctrl+Shift+R."))
        except Exception as e:
            self._status(tf("Não foi possível criar o atalho: {e}", e=e))
        self._update_shortcut_link()

    def _create_shortcut(self, lnk: Path):
        if IS_FROZEN:
            target, args = sys.executable, ""
            wd = str(Path(sys.executable).parent)
        else:
            exe = Path(sys.executable)
            pyw = exe.with_name("pythonw.exe")
            target = str(pyw if pyw.exists() else exe)
            script = str(Path(__file__).resolve())
            args = f'"{script}"'
            wd = str(Path(script).parent)
        lnk.parent.mkdir(parents=True, exist_ok=True)
        ps = (
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(lnk))});"
            f"$s.TargetPath={_ps_quote(target)};$s.Arguments={_ps_quote(args)};"
            f"$s.HotKey='CTRL+SHIFT+R';$s.WorkingDirectory={_ps_quote(wd)};"
            f"$s.IconLocation='shell32.dll,168';$s.Description='Reco';$s.Save()"
        )
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       check=True, capture_output=True, **_no_window_kwargs())

    # ── tray icon (hover shows this window over the tray; X hides into it) ──────
    def _init_tray(self):
        if not HAS_TRAY:
            return
        icons = {"idle": _asset("logo", "tray_idle.ico"),
                 "rec":  _asset("logo", "tray_rec.ico")}
        icons = {k: v for k, v in icons.items() if v.exists()}
        if not icons:
            return
        try:
            self._tray = _tray.Tray(
                icons, t("Reco — pronto"),
                {"open": t("Abrir"), "quit": t("Sair")},
                on_hover=lambda: self._post(self._tray_hover),
                on_click=lambda: self._post(lambda: self._show_from_tray(True)),
                on_open=lambda: self._post(lambda: self._show_from_tray(True)),
                on_quit=lambda: self._post(self._quit),
                on_record=lambda: self._post(self._tray_toggle_rec),
                rec_label=self._tray_rec_label,
                on_pause=lambda: self._post(self._tray_toggle_pause),
                pause_label=self._tray_pause_label)
        except Exception as e:
            print(f"[tray] {e}")
            self._tray = None

    def _tray_rec_label(self):
        # Called by the tray while the menu is being built (same thread as Tk, since
        # Tk's loop dispatches our WndProc), so reading the state here is safe.
        if self._state in (RECORDING, PAUSED):
            return t("Parar")
        if self._state == IDLE:
            return t("Gravar")
        return None                   # BUSY / STOPPED: no sensible one-click action

    def _tray_pause_label(self):
        if self._state == RECORDING:
            return t("Pausar")
        if self._state == PAUSED:
            return t("Continuar")
        return None

    def _tray_toggle_rec(self):
        if self._state in (RECORDING, PAUSED):
            self._stop_rec()
            # Stopping leaves the save/transcribe/delete choice — show it, otherwise
            # the recording would sit in limbo behind a hidden window.
            self._show_from_tray(True)
        elif self._state == IDLE:
            self._start_rec()
            if self._hidden:          # started from the tray → stay out of the way
                self._sync_tray()

    def _tray_toggle_pause(self):
        if self._state == RECORDING:
            self._pause_rec()
        elif self._state == PAUSED:
            self._resume_rec()

    def _tray_hover(self):
        # Hover shows the window without stealing focus; it stays until the cursor
        # leaves both the window and the icon (see _hover_watch).
        if self._tray is None or self._closing:
            return
        if not self._hidden:
            return
        self._show_from_tray(False)

    def _anchor_to_tray(self):
        """Park the window just above/beside the tray icon, clamped to the desktop."""
        try:
            self.update_idletasks()
            w, h = self.winfo_reqwidth(), self.winfo_reqheight()
            wl, wt, wr, wb = _tray.work_area()
            rect = self._tray.icon_rect() if self._tray else None
            if rect:
                il, it, ir, ib = rect
                x = ir - w
                y = it - h - 8 if it > (wb - wt) / 2 else ib + 8   # taskbar top/bottom
            else:
                x, y = wr - w - 12, wb - h - 12
            x = max(wl + 4, min(x, wr - w - 4))
            y = max(wt + 4, min(y, wb - h - 4))
            self.geometry(f"+{int(x)}+{int(y)}")
        except Exception:
            pass

    def _show_from_tray(self, activate: bool):
        self._cancel_hover_watch()
        self._hidden = False
        self._pinned = bool(activate)
        self.deiconify()
        self._anchor_to_tray()
        if activate:
            self.overrideredirect(True)
            self.lift()
            self.focus_force()
        else:
            _tray.show_no_activate(self.winfo_id())
            self.lift()
            # Grace period: the window pops up under/near the cursor, but the user
            # needs a moment to travel from the icon into it. Checking the hot zone
            # immediately would hide it again on the very first tick.
            self._hover_after = self.after(500, self._hover_watch)

    def _hide_to_tray(self):
        self._cancel_hover_watch()
        self._hide_pop()
        self._hidden = True
        self._pinned = False
        self.withdraw()

    def _hover_watch(self):
        # The hot zone is the window ∪ the icon: a cursor resting *on the icon*
        # must not dismiss the window it just opened (WM_MOUSEMOVE stops firing
        # when the mouse stops, so we poll instead of relying on a Leave event).
        if self._closing or self._hidden or self._pinned:
            return
        try:
            cx, cy = _tray.cursor_pos()
            x, y = self.winfo_rootx(), self.winfo_rooty()
            w, h = self.winfo_width(), self.winfo_height()
            inside = (x - 6 <= cx <= x + w + 6) and (y - 6 <= cy <= y + h + 6)
            if not inside and self._tray:
                r = self._tray.icon_rect()
                if r and (r[0] - 4 <= cx <= r[2] + 4) and (r[1] - 4 <= cy <= r[3] + 4):
                    inside = True
            if not inside:
                self._hide_to_tray()
                return
        except Exception:
            pass
        self._hover_after = self.after(120, self._hover_watch)

    def _cancel_hover_watch(self):
        if self._hover_after:
            try:
                self.after_cancel(self._hover_after)
            except Exception:
                pass
            self._hover_after = None

    def _pin(self, _e=None):
        # Any click inside the window pins it — otherwise moving the mouse to a
        # file dialog or a combobox would make the window vanish mid-action.
        self._pinned = True
        self._cancel_hover_watch()

    def _sync_tray(self):
        if not self._tray:
            return
        live = self._state in (RECORDING, PAUSED)
        # The red dot stays on while paused: a paused session is still "armed", and
        # an idle-looking icon would read as "nothing is being recorded".
        self._tray.set_state("rec" if live else "idle")
        if self._state == RECORDING:
            tip = tf("Reco — gravando {d}", d=self._timer_var.get())
        elif self._state == PAUSED:
            tip = tf("Reco — pausado {d}", d=self._timer_var.get())
        else:
            tip = t("Reco — pronto")
        self._tray.set_tooltip(tip)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _status(self, msg):
        self._status_var.set(msg)

    def _on_close(self):
        # X hides into the tray (a recording keeps running); Quit really exits.
        if self._quitting:               # already saving-then-exiting: let it finish
            return
        if self._tray and self._tray.alive:
            self._hide_to_tray()
            return
        self._quit()

    def _quit(self):
        # Quitting mid-recording must not throw the audio away: close the MP3
        # first, then exit. It is already encoded, so this is quick — the window
        # still comes up so "Salvando…" is visible instead of a silent hang.
        if self._quitting:
            return
        self._quitting = True             # NOT _closing: that would stop _drain_ui,
                                          # and the save-then-exit callback rides it
        if self._recorder and self._recorder.recording:
            self._show_from_tray(True)
            self._state = BUSY
            self._set_rec_state(BUSY)
            self._status(t("Salvando antes de sair…"))
            if self._tray:
                self._tray.set_tooltip(t("Reco — salvando gravação…"))

            def save_then_exit():
                try:
                    self._recorder.stop()
                except Exception as e:
                    print(f"[quit] {e}")   # nothing captured / encode failed → just go
                self._post(self._hard_exit)

            threading.Thread(target=save_then_exit, daemon=True).start()
            return
        self._hard_exit()

    def _hard_exit(self):
        self._closing = True
        self._transcribing = False
        if self._tray:
            self._tray.remove()
        try:
            self.destroy()
        except Exception:
            pass
        # OpenVINO/WASAPI keep native threads alive that can outlive the Python
        # interpreter's shutdown — without this the process lingers with no window
        # (and, notably, keeps holding its own files locked).
        sys.stdout.flush()
        os._exit(0)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import tempfile
        devs = ov_available_devices() if HAS_OV else []
        backend = ("mlx-whisper" if HAS_MLX else "openvino" if HAS_OV else "none")
        lines = [
            f"frozen={IS_FROZEN} lang={LANG} HAS_SC={HAS_SC} HAS_NP={HAS_NP} "
            f"HAS_OV={HAS_OV} HAS_AV={HAS_AV} HAS_MLX={HAS_MLX}",
            f"backend={backend}",
            f"ov_devices={devs}",
            f"resolved(AUTO)={resolve_device('AUTO') if HAS_OV else 'n/a'}",
            f"transcription_available={(HAS_OV or HAS_MLX) and HAS_AV}",
        ]
        Path(tempfile.gettempdir(), "reco_selftest.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        sys.exit(0)

    if "--transcribe" in sys.argv:
        # Headless transcription:  Reco.exe --transcribe <audio> [--diarize]
        # Saves <audio>.txt next to the file. Status/errors go to the log file
        # below (the windowed .exe has no console).
        import tempfile
        i = sys.argv.index("--transcribe")
        audio = Path(sys.argv[i + 1])
        diar = "--diarize" in sys.argv
        log = Path(tempfile.gettempdir()) / "reco_transcribe_log.txt"
        log.write_text("starting\n", encoding="utf-8")
        tr = make_transcriber()
        if tr is None or not HAS_AV:
            log.write_text("ERROR: transcription backend/PyAV unavailable\n",
                           encoding="utf-8")
            sys.exit(2)
        cfg = load_config()
        tr.set_model(cfg.get("model", _CFG_DEFAULTS["model"]))
        tr.set_device(cfg.get("device", "AUTO"))
        ev = threading.Event(); out = {}
        tr.transcribe(
            audio, lang=LANG, diarize=diar,
            aec=bool(cfg.get("aec")) and diar,
            progress_cb=lambda m: log.write_text(m + "\n", encoding="utf-8"),
            done_cb=lambda t_, e: (out.update(t=t_, e=e), ev.set()))
        ev.wait(36000)
        if out.get("e"):
            log.write_text(f"ERROR: {out['e']}\n", encoding="utf-8")
            sys.exit(2)
        txt = audio.with_suffix(".txt")
        txt.write_text(out.get("t") or "", encoding="utf-8")
        log.write_text(f"OK -> {txt}\n", encoding="utf-8")
        sys.exit(0)

    if not (HAS_SC and HAS_NP and HAS_AV):
        _root = tk.Tk()
        _root.withdraw()
        missing = []
        if not HAS_NP:   missing.append("numpy")
        if not HAS_SC:   missing.append("soundcard")
        if not HAS_AV:   missing.append("av")
        messagebox.showerror(
            t("Dependências ausentes"),
            tf("Para gravar áudio, instale as dependências:\n\n  pip install {pkgs}\n\n"
               "Abra um terminal e rode o comando acima. Depois, reinicie o {app}.",
               pkgs=" ".join(missing), app=APP_TITLE))
        _root.destroy()
        sys.exit(1)

    # Instância única (F6): o atalho Ctrl+Shift+R é um .lnk que LANÇA o exe —
    # apertar com o Reco já aberto abria uma 2ª instância (bandeja duplicada,
    # disputando os mesmos dispositivos de áudio). --selftest/--transcribe já
    # saíram (sys.exit) antes de chegar aqui, então nunca criam o mutex.
    if os.name == "nt":
        ERROR_ALREADY_EXISTS = 183
        HWND_BROADCAST = 0xFFFF
        _mutex = ctypes.windll.kernel32.CreateMutexW(
            None, False, "Local\\Reco.SingleInstance")
        # `ctypes.windll.*` não usa `use_last_error=True` — GetLastError() direto
        # é o jeito certo de ler o erro de verdade logo após a chamada anterior.
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            wm_show = ctypes.windll.user32.RegisterWindowMessageW("Reco.Show")
            ctypes.windll.user32.PostMessageW(HWND_BROADCAST, wm_show, 0, 0)
            sys.exit(0)

    App().mainloop()
    os._exit(0)     # see App._quit: native OpenVINO/WASAPI threads can hang exit
