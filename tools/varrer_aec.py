"""Distribuicao de (acoplamento, ERLE, dano na voz) no acervo — Fase 0.4 do
roadmap 2026-08-21-salto-de-alinhamento-sob-carga.md.

    python tools/varrer_aec.py <mp3...|--acervo N> [--saida temp/x]

SO LE. Nunca escreve MP3, nunca altera reco.py.

A PERGUNTA que ele responde: o passo E4 (guard que devolve o mic cru quando o
`cancel_echo` piora o audio) foi DECIDIDO em § 6.2, e o que falta e o LIMIAR.
Em 21/08 mediu-se um caso — acoplamento -31,5 dB com ERLE -7,8 dB, o filtro
somando energia — mas um ponto nao e um limiar. Este script mede o par nos dois
eixos em varias gravacoes para achar o acoplamento abaixo do qual o ERLE vira
<= 0 dB.

⚠️ Reusa a rotulagem ALINHADA de `medir_aec.py` (`rotula`), nunca a de
`medir_eco.py`: rotular por energia simultanea com os canais desalinhados mede o
oposto do que se quer (docs/ARMADILHAS.md, 19/08).

⚠️ Le o par (ERLE, dano) SEMPRE junto — ERLE sozinho e metrica proibida neste
projeto (CLAUDE.md): e indistinguivel de "abaixei o volume".
"""
import json
import sys
from pathlib import Path

import numpy as np

# Console cp1252: print de '§'/emoji levanta em vez de degradar (ARMADILHAS 19/08).
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from medir_aec import (B, SR, db, escolhe_janelas, ler_janela,  # noqa: E402
                       mede, perfil, rms, rotula)
from reco import _alinhar_canais                                # noqa: E402


def acoplamento(mic, sysc, so_sys):
    """Ganho caixa->mic em dB, medido so nos blocos em que SO o sistema toca.

    Negativo e o normal: o mic capta o eco mais fraco do que a fonte. Muito
    negativo (< -30 dB) = quase nao ha eco para cancelar, e e ai que o filtro
    de minimos quadrados tende a ajustar ruido e SOMAR energia (o caso de 15:00).
    """
    _, ref_al = _alinhar_canais(mic, sysc, SR)
    idx = np.flatnonzero(so_sys)
    if len(idx) < 5:
        return None
    em = np.mean([rms(mic[j * B:(j + 1) * B]) for j in idx])
    es = np.mean([rms(ref_al[j * B:(j + 1) * B]) for j in idx
                  if (j + 1) * B <= len(ref_al)])
    return db(em) - db(es)


def analisa(path, n_janelas=6, dur=15.0):
    bm, bs = perfil(path)
    janelas = escolhe_janelas(bm, bs, n_janelas, dur)
    if not janelas:
        return {"arquivo": path.name, "veredito": "sem janela util"}
    erles, danos, acops = [], [], []
    for t, _, _ in janelas:
        mic, sysc = ler_janela(path, t, dur)
        n = min(len(mic), len(sysc))
        if n < SR:
            continue
        mic, sysc = mic[:n], sysc[:n]
        try:
            so_sys, so_mic = rotula(mic, sysc)
            erle, dano = mede(mic, sysc, so_sys, so_mic)
            ac = acoplamento(mic, sysc, so_sys)
        except MemoryError:
            continue
        if erle is not None:
            erles.append(erle)
        if dano is not None:
            danos.append(dano)
        if ac is not None:
            acops.append(ac)
    if not erles or not acops:
        return {"arquivo": path.name, "veredito": "inconclusivo"}
    r = {"arquivo": path.name, "n_janelas": len(erles),
         "erle_mediano": round(float(np.median(erles)), 1),
         "erle_min": round(float(np.min(erles)), 1),
         "dano_pior": round(float(np.max(danos)), 1) if danos else None,
         "acoplamento_db": round(float(np.median(acops)), 1),
         "erles": [round(float(x), 1) for x in erles],
         "acops": [round(float(x), 1) for x in acops]}
    r["veredito"] = ("AEC PIORA" if r["erle_mediano"] <= 0 else
                     "AEC fraco" if r["erle_mediano"] < 5 else "AEC ajuda")
    return r


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    saida = Path(argv[argv.index("--saida") + 1]) if "--saida" in argv else None
    if "--acervo" in argv:
        n = int(argv[argv.index("--acervo") + 1])
        pasta = Path.home() / "Documents" / "Reco"
        todos = sorted((p for p in pasta.glob("*.mp3") if "_alinhado" not in p.stem),
                       key=lambda p: p.stat().st_size, reverse=True)
        alvos = todos[:n]
    else:
        # .strip(): lista vinda de arquivo com CRLF chega com \r colado no fim e
        # o endswith rejeitaria TODOS os caminhos em silencio (custou uma rodada).
        alvos = [Path(a.strip()) for a in argv[1:] if a.strip().endswith(".mp3")]
    faltando = [p for p in alvos if not p.exists()]
    if faltando:
        print("nao existem: " + ", ".join(p.name for p in faltando))
        return 2
    if not alvos:
        print("nenhum .mp3 recebido — nada a medir")
        return 2

    res = []
    for i, p in enumerate(alvos, 1):
        try:
            r = analisa(p)
        except Exception as e:
            r = {"arquivo": p.name, "veredito": "FALHOU", "erro": str(e)}
        res.append(r)
        print(f"[{i}/{len(alvos)}] {r['arquivo']} -> {r['veredito']}"
              + (f" | acoplamento {r['acoplamento_db']:+.1f} dB"
                 f" | ERLE {r['erle_mediano']:+.1f} dB (min {r['erle_min']:+.1f})"
                 f" | dano {r['dano_pior']:+.1f} dB"
                 if "erle_mediano" in r else ""), flush=True)

    bons = [r for r in res if "acoplamento_db" in r]
    linhas = ["| arquivo | janelas | acoplamento | ERLE mediano | ERLE min | dano pior | veredito |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in bons:
        linhas.append(f"| {r['arquivo']} | {r['n_janelas']} | {r['acoplamento_db']:+.1f} dB "
                      f"| {r['erle_mediano']:+.1f} | {r['erle_min']:+.1f} "
                      f"| {r['dano_pior']:+.1f} | {r['veredito']} |")
    # O limiar do guard E4: o acoplamento mais ALTO ainda associado a ERLE <= 0.
    ruins = [r["acoplamento_db"] for r in bons if r["erle_mediano"] <= 0]
    okz = [r["acoplamento_db"] for r in bons if r["erle_mediano"] > 0]
    linhas.append("")
    # ⚠️ Amostra vazia NAO e resposta. Sem esta guarda o relatorio dizia
    # "nenhum arquivo com ERLE <= 0" tendo medido ZERO arquivos — o mesmo defeito
    # de instrumento de § 4.5/4.6 (o relatorio descreve outra coisa que o que fez).
    if not bons:
        linhas.append(f"⚠️ **AMOSTRA VAZIA: 0 de {len(res)} arquivos renderam medida.** "
                      "Nada se conclui daqui — conferir os vereditos por arquivo acima.")
    elif ruins:
        linhas.append(f"**AEC piora (ERLE <= 0) em {len(ruins)} arquivo(s)**, "
                      f"acoplamento {min(ruins):+.1f}..{max(ruins):+.1f} dB.")
        if okz:
            linhas.append(f"AEC ajuda com acoplamento {min(okz):+.1f}..{max(okz):+.1f} dB.")
            sep = (max(ruins) + min(okz)) / 2 if max(ruins) < min(okz) else None
            linhas.append(f"**Limiar sugerido para E4: acoplamento < {sep:+.1f} dB "
                          f"-> devolver o mic cru.**" if sep is not None else
                          "⚠️ as duas populacoes SE SOBREPOEM no acoplamento — "
                          "o acoplamento sozinho nao separa, E4 precisa medir o "
                          "ganho no proprio sinal (era o desenho de § 6.2).")
    else:
        linhas.append("**Nenhum arquivo com ERLE <= 0 nesta amostra** — o caso de "
                      "15:00 pode ser raro; E4 continua valendo como guard barato, "
                      "mas nao ha limiar de acoplamento a calibrar.")
    txt = "\n".join(linhas)
    print("\n" + txt)
    if saida:
        Path(saida).with_suffix(".txt").write_text(txt, encoding="utf-8")
        Path(saida).with_suffix(".json").write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nrelatorios em {saida}.txt e {saida}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
