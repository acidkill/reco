"""Confere a cobertura de tradução PT->EN e PT->PL de reco.py/tray.py.

Extrai por AST todo literal usado em t("...")/tf("...") e compara com as
chaves da tabela _TR_EN. Reporta:
  FALTANTES EN — usada no código, sem tradução em _TR_EN (usuário EN vê PT)
  FALTANTES PL — usada no código, sem tradução em _TR_PL (usuário PL vê PT)
  MORTAS       — está em _TR_EN mas nenhum t()/tf() do código a usa

Exit code 1 se houver FALTANTES (MORTAS sozinha não falha o build).
"""
import ast
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVOS = [RAIZ / "reco.py", RAIZ / "tray.py"]


def _lit(no) -> str | None:
    if isinstance(no, ast.Constant) and isinstance(no.value, str):
        return no.value
    return None


def _nome_chamada(no: ast.Call) -> str | None:
    if isinstance(no.func, ast.Name):
        return no.func.id
    if isinstance(no.func, ast.Attribute):
        return no.func.attr
    return None


def _strings_de_for(no: ast.For) -> set:
    """Padrão recorrente no app: `for lbl, ... in [(...), (...)]: t(lbl)` — a
    chave real só existe na tupla do laço, não no site de chamada de t()/tf().
    Casa o nome da variável usada dentro de t()/tf() com a posição dela no
    alvo do for e recolhe o literal correspondente de CADA tupla da lista."""
    achadas = set()
    alvo = no.target
    if isinstance(alvo, ast.Tuple):
        nomes = [e.id if isinstance(e, ast.Name) else None for e in alvo.elts]
    elif isinstance(alvo, ast.Name):
        nomes = [alvo.id]
    else:
        return achadas
    if not isinstance(no.iter, ast.List):
        return achadas

    usados_pos = set()
    for filho in ast.walk(no):
        if isinstance(filho, ast.Call) and _nome_chamada(filho) in ("t", "tf") and filho.args:
            arg = filho.args[0]
            if isinstance(arg, ast.Name) and arg.id in nomes:
                usados_pos.add(nomes.index(arg.id))
    if not usados_pos:
        return achadas

    for el in no.iter.elts:
        if isinstance(el, ast.Tuple):
            itens = el.elts
        else:
            itens = [el]
        for pos in usados_pos:
            if pos < len(itens):
                s = _lit(itens[pos])
                if s is not None:
                    achadas.add(s)
    return achadas


def strings_usadas(caminho: Path) -> set:
    """Literais que acabam passando por t()/tf() — direto (1º arg), indireto
    via _show_menu/_show_tip (chamam t(label_key) sobre valor recebido em
    variável), ou indireto via `for lbl, ... in [tuplas]: t(lbl)`."""
    achadas = set()
    arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    for no in ast.walk(arvore):
        if isinstance(no, ast.For):
            achadas |= _strings_de_for(no)
            continue
        if not isinstance(no, ast.Call):
            continue
        nome = _nome_chamada(no)

        if nome in ("t", "tf") and no.args:
            s = _lit(no.args[0])
            if s is not None:
                achadas.add(s)
        elif nome == "_show_menu" and len(no.args) >= 2:
            itens = no.args[1]
            if isinstance(itens, ast.List):
                for el in itens.elts:
                    if isinstance(el, ast.Tuple) and el.elts:
                        s = _lit(el.elts[0])
                        if s is not None:
                            achadas.add(s)
        elif nome == "_show_tip" and len(no.args) >= 2:
            s = _lit(no.args[1])
            if s is not None:
                achadas.add(s)
        elif nome == "_lib_action" and len(no.args) >= 4:
            # botão-ícone da biblioteca: o caption de hover é o 4º argumento e
            # vira _show_tip(b, tip_key) lá dentro — mesmo caso do _show_tip.
            s = _lit(no.args[3])
            if s is not None:
                achadas.add(s)
    return achadas


def _tabele() -> dict:
    import importlib.util
    spec = importlib.util.spec_from_file_location("reco", RAIZ / "reco.py")
    mod = importlib.util.module_from_spec(spec)
    # reco.py importa libs pesadas de captura/transcrição no topo; carregar só
    # a tabela via exec parcial seria frágil — em vez disso, roda o módulo
    # inteiro (mesmo custo do `python -c "import reco"` que já é rotina aqui).
    spec.loader.exec_module(mod)
    return {"en": mod._TR_EN, "pl": mod._TR_PL}


def main():
    usadas = set()
    for arq in ARQUIVOS:
        usadas |= strings_usadas(arq)

    tabelas = _tabele()
    falha = False

    en = set(tabelas["en"])
    faltantes_en = sorted(usadas - en)
    mortas = sorted(en - usadas)
    if faltantes_en:
        print(f"FALTANTES EN ({len(faltantes_en)}):")
        for s in faltantes_en:
            print(f"  {s!r}")
    if mortas:
        print(f"MORTAS ({len(mortas)}):")
        for s in mortas:
            print(f"  {s!r}")
    falha |= bool(faltantes_en)

    pl = set(tabelas["pl"])
    faltantes_pl = sorted(usadas - pl)
    if faltantes_pl:
        print(f"FALTANTES PL ({len(faltantes_pl)}):")
        for s in faltantes_pl:
            print(f"  {s!r}")
    falha |= bool(faltantes_pl)

    if not falha and not mortas:
        print("OK — cobertura completa (EN e PL), nada morto.")

    return 1 if falha else 0


if __name__ == "__main__":
    sys.exit(main())
