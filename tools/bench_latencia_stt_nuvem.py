"""bench_latencia_stt_nuvem.py — mesmo contrato do bench_latencia_stt.py
(mediana, RTF, texto), mas para STT em nuvem, para comparar contra o Reco
local (roadmap central/roadmap/2026-08-28-jarvis-voz-e-notebook.md, Fase 0).

Providers via urllib puro (sem SDK): groq, openai, deepgram. Chave só do
ambiente (GROQ_API_KEY / OPENAI_API_KEY / DEEPGRAM_API_KEY) — sem chave, o
provider é pulado com aviso, não é erro fatal.

Uso:
    doppler run --project <projeto-com-as-chaves> --config dev -- \
        python tools/bench_latencia_stt_nuvem.py [--rep 3] [--clipes temp/bench-voz/reais]
"""

import argparse
import json
import mimetypes
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8")
    except Exception:
        pass


def duracao_wav(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _multipart(campos: dict, arquivo: tuple):
    boundary = uuid.uuid4().hex
    nome_campo, caminho, content_type = arquivo
    partes = []
    for chave, valor in campos.items():
        partes.append(f"--{boundary}\r\n"
                       f'Content-Disposition: form-data; name="{chave}"\r\n\r\n'
                       f"{valor}\r\n".encode())
    partes.append((f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{nome_campo}"; '
                    f'filename="{Path(caminho).name}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n").encode())
    partes.append(Path(caminho).read_bytes())
    partes.append(f"\r\n--{boundary}--\r\n".encode())
    corpo = b"".join(partes)
    return corpo, f"multipart/form-data; boundary={boundary}"


def transcrever_groq(clip: Path, chave: str):
    corpo, ctype = _multipart(
        {"model": "whisper-large-v3-turbo", "language": "pt"},
        ("file", clip, "audio/wav"),
    )
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        data=corpo, method="POST",
        headers={"Authorization": f"Bearer {chave}", "Content-Type": ctype},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")).get("text", "").strip()


def transcrever_openai(clip: Path, chave: str):
    corpo, ctype = _multipart(
        {"model": "whisper-1", "language": "pt"},
        ("file", clip, "audio/wav"),
    )
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=corpo, method="POST",
        headers={"Authorization": f"Bearer {chave}", "Content-Type": ctype},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")).get("text", "").strip()


def transcrever_deepgram(clip: Path, chave: str):
    corpo = clip.read_bytes()
    ctype = mimetypes.guess_type(str(clip))[0] or "audio/wav"
    req = urllib.request.Request(
        "https://api.deepgram.com/v1/listen?language=pt&model=nova-2&smart_format=true",
        data=corpo, method="POST",
        headers={"Authorization": f"Token {chave}", "Content-Type": ctype},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        dados = json.loads(resp.read().decode("utf-8"))
        return dados["results"]["channels"][0]["alternatives"][0]["transcript"].strip()


PROVEDORES = {
    "groq": ("GROQ_API_KEY", transcrever_groq),
    "openai": ("OPENAI_API_KEY", transcrever_openai),
    "deepgram": ("DEEPGRAM_API_KEY", transcrever_deepgram),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--provider", action="append",
                     help="repetível; default: groq, openai, deepgram")
    ap.add_argument("--clipes", default=str(Path(__file__).resolve().parents[1]
                                          / "temp" / "bench-voz" / "reais"))
    ap.add_argument("--rep", type=int, default=3)
    ap.add_argument("--saida", default=None)
    args = ap.parse_args()

    provedores = args.provider or list(PROVEDORES)
    pasta = Path(args.clipes)
    clipes = sorted(pasta.glob("*.wav"))
    if not clipes:
        sys.exit(f"nenhum .wav em {pasta}")

    resultado = {"rep": args.rep, "clipes_dir": str(pasta), "provedores": {}}
    for nome in provedores:
        if nome not in PROVEDORES:
            print(f"[aviso] provider desconhecido: {nome}", flush=True)
            continue
        var_env, fn = PROVEDORES[nome]
        chave = os.environ.get(var_env)
        if not chave:
            print(f"[aviso] {nome} pulado: {var_env} ausente no ambiente", flush=True)
            resultado["provedores"][nome] = {"pulado": True, "motivo": f"{var_env} ausente"}
            continue

        print(f"\n== provider {nome}", flush=True)
        por_clipe = {}
        try:
            for c in clipes:
                dur = duracao_wav(c)
                tempos, texto = [], ""
                for _ in range(args.rep):
                    t0 = time.perf_counter()
                    texto = fn(c, chave)
                    tempos.append(time.perf_counter() - t0)
                med = statistics.median(tempos)
                por_clipe[c.name] = {"duracao_s": round(dur, 2), "min_s": round(min(tempos), 3),
                                      "mediana_s": round(med, 3), "rtf": round(med / dur, 3),
                                      "texto": texto}
                print(f"  {c.name}: dur {dur:4.1f} s | min {min(tempos):.2f} s | "
                      f"mediana {med:.2f} s | rtf {med / dur:.2f}\n    -> {texto[:120]}",
                      flush=True)
            resultado["provedores"][nome] = {"clipes": por_clipe}
        except urllib.error.HTTPError as e:
            corpo_erro = e.read().decode("utf-8", errors="replace")
            print(f"[erro] {nome}: HTTP {e.code} — {corpo_erro[:300]}", flush=True)
            resultado["provedores"][nome] = {"erro": f"HTTP {e.code}: {corpo_erro[:300]}"}
        except Exception as e:
            print(f"[erro] {nome}: {e}", flush=True)
            resultado["provedores"][nome] = {"erro": str(e)}

    saida = Path(args.saida) if args.saida else pasta.parent / "bench-nuvem.json"
    saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[json] {saida}")


if __name__ == "__main__":
    main()
