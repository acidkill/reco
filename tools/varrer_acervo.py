"""Varre o acervo e mede o desalinhamento por janela (Fase 0.1 do roadmap
2026-08-21-salto-de-alinhamento-sob-carga.md).

    python tools/varrer_acervo.py <pasta|mp3> [--janela 15] [--saida temp/x]

SO LE. Nunca escreve MP3, nunca toca no original — o unico efeito colateral sao
os dois relatorios (`.txt` legivel e `.json` com a serie completa).

Por que existe, em vez de so rodar `alinhar_gravacao.py` sem `--aplicar`: aquele
relatorio resume por MEDIANA, e a auditoria de 30/08 (§ 4.7) mostrou que a
mediana engana justamente no defeito que se quer contar — a gravacao de 10:41 tem
mediana -199 ms com serie indo de +83 a -495 ms. As metricas que correspondem ao
dano sao **pior janela** e **% do tempo acima de 50 ms**; a assinatura de SALTO
(o defeito desta investigacao, distinto de offset constante) e a **amplitude da
faixa** — quanto a defasagem MUDOU dentro do proprio arquivo.

Registra tambem o `q` de cada janela: e o insumo para calibrar o par (limiar de
q, fracao minima de trechos) que a Fase E1 precisa, e que hoje herda um
ALIGN_Q_MIN calibrado para outra pergunta (§ 4.3).
"""
import json
import sys
from pathlib import Path

import numpy as np

# Console cp1252: print de '§'/emoji levanta em vez de degradar (ARMADILHAS 19/08).
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reco                                                        # noqa: E402
from alinhar_gravacao import blocos_do_mp3                         # noqa: E402

SR = reco.OUT_SR
LIMIAR_DANO_MS = 50      # acima disso o ouvido separa o eco (roadmap 19/08)
LIMIAR_SALTO_MS = 100    # amplitude da faixa que caracteriza salto, nao offset fixo


def mede(path, janela_s):
    """Um passe pelo arquivo; devolve a serie de (t, d_ms, q) por janela."""
    J = int(janela_s * SR)
    buf_m = np.zeros(0, np.float32)
    buf_s = np.zeros(0, np.float32)
    serie, n_trechos, total = [], 0, 0
    for m, s in blocos_do_mp3(path):
        buf_m = np.concatenate([buf_m, m])
        buf_s = np.concatenate([buf_s, s])
        total += len(m)
        while len(buf_m) >= J:
            d, q = reco.estimar_offset(buf_m[:J], buf_s[:J], sr=SR)
            n_trechos += 1
            t = (total - len(buf_m)) / SR
            if q >= reco.ALIGN_Q_MIN:
                serie.append((round(t, 1), round(d / SR * 1000, 1), round(q, 3)))
            buf_m, buf_s = buf_m[J:], buf_s[J:]
    return serie, n_trechos, total / SR


def resume(nome, serie, n_trechos, dur_s):
    """Reduz a serie as metricas que a auditoria § 4.7 elegeu."""
    r = {"arquivo": nome, "duracao_min": round(dur_s / 60, 1),
         "n_medidos": len(serie), "n_trechos": n_trechos, "serie": serie}
    if not serie:
        r["veredito"] = "sem correlacao"   # fone, caixa muda: nada a dizer
        return r
    ds = np.array([d for _, d, _ in serie])
    qs = np.array([q for _, _, q in serie])
    r.update(
        mediana_ms=round(float(np.median(ds)), 1),
        min_ms=round(float(ds.min()), 1), max_ms=round(float(ds.max()), 1),
        amplitude_ms=round(float(ds.max() - ds.min()), 1),
        pior_janela_ms=round(float(max(ds, key=abs)), 1),
        pct_acima_50=round(100.0 * float((np.abs(ds) > LIMIAR_DANO_MS).mean()), 1),
        q_mediano=round(float(np.median(qs)), 3),
        frac_confiavel=round(len(serie) / n_trechos, 3) if n_trechos else 0.0,
    )
    # SALTO = a defasagem mudou muito DENTRO do arquivo; offset constante grande
    # e outro defeito (o de antes de 19/08) e nao e o que se investiga aqui.
    r["veredito"] = ("salto" if r["amplitude_ms"] > LIMIAR_SALTO_MS else
                     "desalinhado" if abs(r["pior_janela_ms"]) > LIMIAR_DANO_MS else
                     "ok")
    return r


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    alvo = Path(argv[1])
    janela_s = float(argv[argv.index("--janela") + 1]) if "--janela" in argv else 15.0
    saida = Path(argv[argv.index("--saida") + 1]) if "--saida" in argv else None
    arquivos = ([alvo] if alvo.is_file()
                else sorted(p for p in alvo.glob("*.mp3") if "_alinhado" not in p.stem))
    if not arquivos:
        print(f"nada para processar em {alvo}")
        return 2

    res = []
    for i, p in enumerate(arquivos, 1):
        try:
            serie, n, dur = mede(p, janela_s)
            r = resume(p.name, serie, n, dur)
        except Exception as e:                     # arquivo corrompido, codec…
            r = {"arquivo": p.name, "veredito": "FALHOU", "erro": str(e)}
        res.append(r)
        print(f"[{i}/{len(arquivos)}] {r['arquivo']} -> {r['veredito']}"
              + (f" | pior {r['pior_janela_ms']:+.0f} ms"
                 f" | amplitude {r['amplitude_ms']:.0f} ms"
                 f" | {r['pct_acima_50']:.0f}% acima de 50 ms"
                 f" | {r['n_medidos']}/{r['n_trechos']} trechos"
                 if "pior_janela_ms" in r else ""), flush=True)

    linhas = ["| arquivo | min | med/trechos | mediana | faixa | pior | ampl | >50ms | veredito |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in res:
        if "pior_janela_ms" in r:
            linhas.append(
                f"| {r['arquivo']} | {r['duracao_min']} | {r['n_medidos']}/{r['n_trechos']} "
                f"| {r['mediana_ms']:+.0f} | {r['min_ms']:+.0f}..{r['max_ms']:+.0f} "
                f"| {r['pior_janela_ms']:+.0f} | {r['amplitude_ms']:.0f} "
                f"| {r['pct_acima_50']:.0f}% | {r['veredito']} |")
        else:
            linhas.append(f"| {r['arquivo']} | | | | | | | | {r['veredito']} |")
    cont = {}
    for r in res:
        cont[r["veredito"]] = cont.get(r["veredito"], 0) + 1
    linhas += ["", f"**{len(res)} gravacoes originais.** Vereditos: "
               + ", ".join(f"{k}={v}" for k, v in sorted(cont.items()))]
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
