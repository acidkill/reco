"""bench_latencia_stt.py — latência de transcrição de FRASE CURTA pelo pipeline
real do Reco (decode → VAD → anti-loop → OpenVINO Whisper), para decidir se o
jarvis da central usa o Reco local ou STT em nuvem (roadmap
central/roadmap/2026-08-28-jarvis-voz-e-notebook.md, Fase 0).

O RTF de lote (2 h em 12 min, medido 29/07/2026) NÃO responde a pergunta
"quanto demora uma frase de 5 s?" — carga do modelo, compilação da iGPU e
overhead fixo por chamada dominam em áudio curto. Este script mede isso.

Uso:
    python tools/bench_latencia_stt.py [--modelo large-v3-turbo] [--modelo small]
        [--device AUTO] [--clipes temp/bench-voz] [--rep 3] [--saida <json>]

Para cada modelo: 1 chamada de aquecimento (inclui carga + compilação — é o
custo de subir o servidor, não da frase) e depois `--rep` chamadas por clipe;
reporta mín/mediana em segundos, o RTF (tempo/duração) e o texto devolvido.
"""

import argparse
import json
import statistics
import sys
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reco  # noqa: E402

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8")
    except Exception:
        pass


def duracao_wav(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def transcrever(tr, src: Path, lang: str):
    fim = threading.Event()
    res = {}

    def done_cb(texto, erro):
        res["texto"], res["erro"] = texto, erro
        fim.set()

    t0 = time.perf_counter()
    tr.transcribe(src, lang=lang, progress_cb=lambda m: None, done_cb=done_cb)
    fim.wait()
    dt = time.perf_counter() - t0
    if res.get("erro"):
        raise RuntimeError(f"{src.name}: {res['erro']}")
    return dt, (res.get("texto") or "").strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--modelo", action="append",
                    help="repetível; default: large-v3-turbo e small")
    ap.add_argument("--device", default="AUTO")
    ap.add_argument("--clipes", default=str(Path(__file__).resolve().parents[1]
                                          / "temp" / "bench-voz"))
    ap.add_argument("--rep", type=int, default=3)
    ap.add_argument("--lang", default="pt")
    ap.add_argument("--saida", default=None)
    args = ap.parse_args()

    modelos = args.modelo or ["large-v3-turbo", "small"]
    pasta = Path(args.clipes)
    clipes = sorted(pasta.glob("clip*.wav"))
    if not clipes:
        sys.exit(f"nenhum clip*.wav em {pasta}")

    resultado = {"device": args.device, "rep": args.rep, "modelos": {}}
    for modelo in modelos:
        tr = reco.make_transcriber()
        if tr is None:
            sys.exit("transcrição indisponível: instale openvino-genai")
        tr.set_model(modelo)
        tr.set_device(args.device)
        try:
            resolvido = reco.resolve_device(args.device)
        except Exception:
            resolvido = "?"
        print(f"\n== modelo {modelo} @ {args.device} (resolvido: {resolvido})", flush=True)
        t_warm, _ = transcrever(tr, clipes[0], args.lang)
        print(f"  aquecimento (carga+compilação+1ª frase): {t_warm:.2f} s", flush=True)
        por_clipe = {}
        for c in clipes:
            dur = duracao_wav(c)
            tempos, texto = [], ""
            for _ in range(args.rep):
                dt, texto = transcrever(tr, c, args.lang)
                tempos.append(dt)
            med = statistics.median(tempos)
            por_clipe[c.name] = {"duracao_s": round(dur, 2), "min_s": round(min(tempos), 3),
                                 "mediana_s": round(med, 3), "rtf": round(med / dur, 3),
                                 "texto": texto}
            print(f"  {c.name}: dur {dur:4.1f} s | min {min(tempos):.2f} s | "
                  f"mediana {med:.2f} s | rtf {med / dur:.2f}\n    -> {texto[:120]}",
                  flush=True)
        resultado["modelos"][modelo] = {"aquecimento_s": round(t_warm, 2), "clipes": por_clipe}
        try:
            tr.close()
        except Exception:
            pass

    saida = Path(args.saida) if args.saida else pasta / "bench-local.json"
    saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[json] {saida}")


if __name__ == "__main__":
    main()
