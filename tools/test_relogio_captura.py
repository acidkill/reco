"""Mede a deriva de cada canal contra o RELOGIO (Fase 0.2 do roadmap
2026-08-21-salto-de-alinhamento-sob-carga.md).

    python tools/test_relogio_captura.py [segundos]     # default 120

NAO altera reco.py, NAO escreve MP3, NAO transcreve — abre os dois recorders
exatamente como o `DualRecorder` faz (mesmo `blocksize=CHUNK`, mesma barreira,
mesmo `record(numframes=CHUNK)`), conta frames entregues por canal e compara com
`perf_counter`.

A PERGUNTA que ele responde (§ 1.6): o `soundcard` nao bloqueia quando o WASAPI
nao tem pacote — passados ~40 ms de ociosidade ele FABRICA silencio medido pelo
relogio, e o `int()` desse calculo descarta o resto fracionario a cada disparo.
Isso e assimetrico entre os canais: o mic sempre entrega pacotes, o loopback so
entrega quando alguem esta tocando audio. Como o `_pump` pareia por CONTAGEM de
amostras, uma deriva so no loopback desalinha a gravacao — e por um caminho que
NAO levanta `DATA_DISCONTINUITY`, logo invisivel para a Fase B1.

Rodar DUAS vezes, e a diferenca entre elas e a medida:

  1. com a caixa MUDA (nada tocando)   -> o loopback passa pelo caminho fabricado
  2. com audio TOCANDO nos alto-falantes -> o loopback entrega pacotes reais

LEITURA DO RESULTADO (o numero que decide a ordem do roadmap, § 6.1):
  |deriva relativa| < 1 ms/min  -> § 1.6 e irrelevante; sai do roadmap
  dezenas de ms/min             -> § 1.6 e co-causa e a Fase B2 SOBE para logo
                                   depois de A1 (era o "o que reverteria")

O que importa e a deriva RELATIVA entre os canais, nunca a absoluta: latencia de
dispositivo e tempo de arranque atingem os dois juntos e nao desalinham nada.
"""
import sys
import threading
import time
from pathlib import Path

# O console desta maquina e cp1252 e o print levanta em vez de degradar — ja
# derrubou um teste de hardware no minuto 2 (docs/ARMADILHAS.md, 19/08/2026).
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reco                                                        # noqa: E402

try:
    import soundcard as sc
except Exception as e:                                             # sem WASAPI
    print(f"soundcard indisponivel: {e}")
    sys.exit(2)

SR = reco.CAPTURE_SR
CHUNK = reco.CHUNK


def capta(dev_id, loopback, dur_s, barreira, saida, rotulo):
    """Replica _rec_mic/_rec_sys: mesma criacao, mesma leitura, so contando."""
    canais = 2 if loopback else 1
    try:
        rec = sc.get_microphone(dev_id, include_loopback=loopback).recorder(
            samplerate=SR, channels=canais, blocksize=CHUNK)
    except Exception as e:
        saida[rotulo] = {"erro": str(e)}
        return
    try:
        barreira.wait(timeout=5.0)
    except Exception:
        pass
    frames, chamadas, t0 = 0, 0, None
    soma_q, n_q = 0.0, 0        # energia: diz se o canal estava MUDO de verdade
    try:
        with rec as r:
            buffersize = getattr(r, "buffersize", None)
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < dur_s:
                d = r.record(numframes=CHUNK)
                frames += len(d)
                chamadas += 1
                if len(d):
                    import numpy as _np
                    soma_q += float(_np.sum(_np.asarray(d, dtype=_np.float64) ** 2))
                    n_q += _np.asarray(d).size
            t1 = time.perf_counter()
    except Exception as e:
        saida[rotulo] = {"erro": str(e)}
        return
    parede = t1 - t0
    esperado = parede * SR
    rms = (soma_q / n_q) ** 0.5 if n_q else 0.0
    saida[rotulo] = {
        "frames": frames, "chamadas": chamadas, "parede_s": parede,
        "buffersize": buffersize, "rms": rms,
        "esperado": esperado, "deficit_frames": esperado - frames,
        "deficit_ms": (esperado - frames) / SR * 1000,
        "deriva_ms_min": (esperado - frames) / SR * 1000 / (parede / 60),
    }


def main(argv):
    try:
        dur = float(argv[1]) if len(argv) > 1 else 120.0
    except ValueError:                            # -h, --help, dedo errado
        print(__doc__)
        return 2
    mic_id, spk_id = reco.default_mic_id(), reco.default_speaker_id()
    if not mic_id or not spk_id:
        print("sem dispositivo padrao (mic ou alto-falante)")
        return 2
    print(f"mic      : {mic_id}\nloopback : {spk_id}\n"
          f"blocksize={CHUNK} ({CHUNK/SR*1000:.1f} ms pedidos por leitura), "
          f"{dur:.0f} s\n")
    print("⚠️  a leitura so vale se voce souber se havia audio tocando ou nao.\n")

    saida, barreira = {}, threading.Barrier(2)
    ts = [threading.Thread(target=capta, args=(mic_id, False, dur, barreira, saida, "mic"),
                           daemon=True),
          threading.Thread(target=capta, args=(spk_id, True, dur, barreira, saida, "sys"),
                           daemon=True)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=dur + 30)

    for k in ("mic", "sys"):
        r = saida.get(k)
        if not r:
            print(f"{k}: nao retornou"); return 1
        if "erro" in r:
            print(f"{k}: FALHOU — {r['erro']}"); return 1
        print(f"{k:4s}: buffer={r['buffersize']} frames "
              f"({(r['buffersize'] or 0)/SR*1000:.1f} ms) | {r['chamadas']} leituras | "
              f"{r['frames']} frames em {r['parede_s']:.2f} s | rms {r['rms']:.6f}")
        print(f"      deficit {r['deficit_ms']:+.1f} ms  ->  "
              f"{r['deriva_ms_min']:+.1f} ms/min")

    # A condicao experimental tem de ser MEDIDA, nao lembrada: a leitura de
    # § 1.6 so vale com o loopback mudo, e quem sabe disso e a energia do canal.
    mudo = saida["sys"]["rms"] < 1e-5
    print(f"\ncondicao medida: loopback {'MUDO' if mudo else 'COM AUDIO'} "
          f"(rms {saida['sys']['rms']:.6f}) -> "
          + ("e esta a condicao que § 1.6 descreve"
             if mudo else "⚠️ NAO e a condicao de § 1.6; repetir com a caixa muda"))

    rel = saida["sys"]["deriva_ms_min"] - saida["mic"]["deriva_ms_min"]
    print(f"\nDERIVA RELATIVA (sys - mic): {rel:+.1f} ms/min  "
          f"<- e este o numero que desalinha a gravacao")
    print(f"   (a deriva ABSOLUTA de cada canal — {saida['mic']['deriva_ms_min']:+.1f} e "
          f"{saida['sys']['deriva_ms_min']:+.1f} ms/min — atinge os dois juntos "
          f"e nao desalinha nada)")

    # O teto nao e 1 ms/min: e a capacidade de correcao que JA existe. O
    # alinhador corrige ate ALIGN_MAX_AJUSTE por ALIGN_RECHECK_S.
    capacidade = reco.ALIGN_MAX_AJUSTE / SR * 1000 / (reco.ALIGN_RECHECK_S / 60)
    print(f"\ncapacidade de correcao de deriva hoje: {capacidade:.0f} ms/min "
          f"(ALIGN_MAX_AJUSTE={reco.ALIGN_MAX_AJUSTE} por {reco.ALIGN_RECHECK_S:.0f} s)")
    if abs(rel) < 1.0:
        print("veredito: § 1.6 IRRELEVANTE (< 1 ms/min) — sai do roadmap")
    elif abs(rel) < capacidade:
        print(f"veredito: § 1.6 EXISTE mas esta DENTRO da capacidade de correcao "
              f"({abs(rel):.1f} < {capacidade:.0f} ms/min) — o alinhador atual da "
              f"conta; NAO justifica subir a Fase B2, e NAO explica salto de "
              f"centenas de ms (que e abrupto, nao acumulado)")
    else:
        print("veredito: § 1.6 e CO-CAUSA (excede a correcao) — a Fase B2 sobe "
              "para depois de A1")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
