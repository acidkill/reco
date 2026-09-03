# Plano — ícones Lucide substituindo emoji nos botões (2026-08-12)

## Entendimento do pedido

O Gabriel notou, ao conferir o app pós-roadmap de UX/UI/lógica, que os
"ícones" dos botões (⬤ ⬛ ❚❚ ▶ ⚡ ✕ ✓ ⚙ ↺ ⌨ ⬆ ＋ 🎵) são inconsistentes entre
si: alguns caem na fonte `Segoe UI Symbol` (mono, vetorial), outros em
`Segoe UI Emoji` (bitmap colorido, sem anti-aliasing real) — a mistura é o
que ele quer resolver. Pedido: trocar por um icon set de verdade, baixado e
empacotado no app. Decisão já tomada na conversa: **Lucide** (MIT/ISC,
stroke-based, o mesmo family usado em boa parte de app moderno).

Fora do escopo deste plano: redesenho de layout, paleta de cores nova, ou
qualquer coisa em `docs/ARMADILHAS.md`/pipeline de transcrição — só a troca
de glifo por ícone vetorial nos pontos que hoje usam emoji como
texto de botão/link.

## O que NÃO muda (guardrails)

- **Tema custom continua funcionando.** O Gabriel escolhe cor de fundo/
  destaque livremente (`colorchooser`, `apply_theme`) — os ícones têm que
  acompanhar isso, não vir com cor fixa gravada no PNG. Mesma armadilha do
  F13 desta sessão (`_link` congelando `SUBTLE` no default-arg): resolver a
  cor **no momento do uso**, nunca uma vez só.
- **Bilíngue PT/EN e `tools/check_i18n.py` continuam intactos.** Ícone não é
  string traduzível; texto ao lado do ícone (quando existir) continua
  passando por `t()`/`tf()` normalmente.
- **`build.ps1`/PyInstaller continuam de um comando só**, sem dependência
  nova pesada no app **empacotado** — só o que já roda hoje (`Reco.exe`
  onedir, ~835 MB).
- **Nenhum ícone novo desliga o app se faltar.** Se um PNG não existir por
  algum motivo, cai num fallback (o texto/emoji antigo), nunca crash.

## Abordagem / arquitetura

**Vendorizar SVG, rasterizar em dev-time, tingir em runtime.** Três camadas:

1. **Fonte (dev-time, não vai pro `.exe`):** baixar os `.svg` do Lucide
   (`raw.githubusercontent.com/lucide-icons/lucide/main/icons/<nome>.svg`,
   licença ISC — permite embutir) pros 11 conceitos com ícone Lucide, salvos
   em `assets/icons_src/*.svg` (fonte de verdade, versionada; pasta separada
   da `assets/icons/` de saída DE PROPÓSITO — só a de saída entra no bundle).
   ⚠️ O Lucide renomeou a família `*-circle` para `circle-*`: o nome vigente
   é `circle-arrow-up` (`arrow-up-circle` dá **404**). Os 11 nomes da tabela
   abaixo foram testados contra o raw em 12/08/2026 — todos HTTP 200.
   ⬤/⬛ não vêm do Lucide (ver exceção abaixo): são 2 SVGs de uma linha
   embutidos como string no próprio `gerar_icones.py`.
2. **Máscara (dev-time, script novo `tools/gerar_icones.py`):** cada SVG vira
   um **PNG branco-sobre-transparente** (a máscara alfa) — 20px pros 8
   conceitos de ação (⬤ ⬛ ❚❚ ▶ ⚡ ✕ ✓ 🎵) e 16px pros 5 inline (⚙ ↺ ⌨ ⬆ ＋)
   — via `cairosvg` (confirmado instalável e funcional nesta máquina). As
   máscaras PNG vão em `assets/icons/*.png` e **essas sim** entram no bundle
   (`reco.spec` `datas`, mesmo padrão do `logo/logo_symbol_1x1.ico`).
   `cairosvg` fica só como dependência de **dev** (script em `tools/`, não
   importado por `reco.py`) — não precisa lidar com a fama de PyInstaller +
   Cairo DLL sofrendo pra empacotar, porque nunca vai dentro do `.exe`.
3. **Tinta (runtime, `reco.py`):** função `_icon(nome, cor, tamanho) ->
   ImageTk.PhotoImage`, cacheada em dict por `(nome, cor, tamanho)`. Abre a
   máscara PNG com `Pillow` (já presente no venv, `12.2.0` — só falta entrar
   em `requirements.txt` explicitamente) e recolore via
   `Image.new("RGBA", ..., cor); .putalpha(mascara.getchannel("A"))`. Troca
   de tema (`apply_theme`/`_rebuild_ui`, que já destrói e recria tudo)
   simplesmente pede a cor nova — o cache velho fica órfão e não atrapalha
   (chaves diferentes).

**Exceção deliberada — ⬤ Gravar e ⬛ Parar não usam ícone Lucide.** Motivo:
Lucide é 100% *stroke* (contorno), sem variante preenchida — usar o contorno
pros dois símbolos mais universais de gravação/parada (círculo e quadrado
**sólidos**) quebraria a convenção que todo app de gravação usa.
**Correção de premissa (auditoria de 12/08):** hoje eles NÃO são canvas — são
glifos de texto em `_btn` (`reco.py:2825-2827`; o único `tk.Canvas` do
arquivo é o `VuMeter`, `reco.py:2423`). Mantê-los como texto preservaria a
inconsistência que motivou este plano justamente nos dois botões mais
visíveis do app. Solução: o próprio `gerar_icones.py` rasteriza dois SVGs de
uma linha escritos à mão (`<circle cx="12" cy="12" r="8" fill="white"/>` e
`<rect x="5" y="5" width="14" height="14" rx="2" fill="white"/>`, viewBox
24×24), embutidos como string no script — mesmíssimo pipeline de máscara +
tintura dos demais, zero caso especial no runtime.

## Mapa de substituição (13 conceitos)

| hoje (emoji) | conceito | ícone Lucide | tamanho |
| --- | --- | --- | --- |
| ⬤ | gravar | `record` (SVG próprio, círculo `fill`) | 20 |
| ⬛ | parar | `stop` (SVG próprio, quadrado `fill`) | 20 |
| ❚❚ | pausar | `pause` | 20 |
| ▶ | reproduzir/continuar | `play` | 20 |
| ⚡ | transcrever | `zap` | 20 |
| ✕ | excluir | `trash-2` (ver Decisões) | 20 |
| ✓ | salvar | `check` | 20 |
| ⚙ | opções | `settings` | 16 |
| ↺ | atualizar (dispositivos/biblioteca) | `refresh-cw` | 16 |
| ⌨ | atalho de teclado | `keyboard` | 16 |
| ⬆ | nova versão disponível | `circle-arrow-up` | 16 |
| ＋ | escolher arquivo | `plus` | 16 |
| 🎵 | converter para MP3 | `music` | 20 |

⚠️ **Fora do escopo: o "✓" de célula da biblioteca** (`reco.py:4005` e
`reco.py:4066`, coluna "tem .txt" do Treeview). `ttk.Treeview` só aceita
imagem na coluna `#0` — célula de coluna comum é texto e ponto. Esse ✓ fica
como está e **não conta** como pendência no grep do passo 4.

Pontos de uso confirmados por grep em `reco.py` — **~30 ocorrências vivas**
cobrindo os 13 conceitos (inclui a view "Biblioteca" da outra sessão,
`_lib_action`, que também usa ▶/⚡/✕/↺): view gravar (`_build_recording`),
view transcrever, view converter, view biblioteca, links de opções/atalho/
atualização, menu do ⚡, tooltip de hover, e os rótulos dinâmicos de
`_btn_label` (`reco.py:4373-4382`).

## Decisões e trade-offs

- **`trash-2` em vez de `x` pro Excluir.** O glifo atual (✕) é um X genérico;
  `trash-2` (lata de lixo) comunica "isto é destrutivo/vai pra Lixeira" com
  mais clareza — e a Fase 1.2 desta sessão já trocou o `unlink()` por
  `SHFileOperationW` (Lixeira de verdade), então o ícone passa a bater com o
  comportamento real. Custo: é a única troca que muda o *significado* visual,
  não só o traço — vale confirmar com o Gabriel antes de executar (pergunta
  aberta no fechamento deste plano, não decidida sozinho).
- **Dois tamanhos fixos (16/20px), não escala por DPI.** Renderizar só nesses
  dois tamanhos evita borrão de upscale (Tk não escala `PhotoImage`
  suavemente) sem precisar decidir uma matriz DPI× tamanho agora. Se ficar
  pequeno/grande num monitor específico, é ajuste de valor, não de
  arquitetura — YAGNI até incomodar.
- **Pillow vira dependência real do app** (estava só transitivo). Baixo
  risco: é uma das libs mais testadas com PyInstaller que existe. ⚠️ **MAS o
  `reco.spec` hoje EXCLUI o PIL de propósito** (`excludes=[..., 'PIL', ...]`,
  linha ~57 — herança de quando era lib transitiva indesejada). Sem tirar
  `'PIL'` dos excludes, o exe empacota **sem Pillow** e, por causa do
  fallback silencioso deste plano, o sintoma é o pior possível: ícones
  perfeitos no dev, emoji antigo no `Reco.exe`, zero erro. Além disso,
  `PIL.ImageTk` precisa de `PIL._tkinter_finder` como hiddenimport —
  armadilha clássica de PyInstaller + ImageTk.
- **`cairosvg` nunca entra no `.exe`** — só gera os PNGs uma vez, no
  desenvolvimento. Se o Gabriel trocar de máquina de dev, `pip install
  cairosvg` de novo resolve; não é dependência de quem só usa o app.

## Passos

1. [x] **Baixar os SVGs Lucide** (`play`, `pause`, `zap`, `trash-2`, `check`,
   `settings`, `refresh-cw`, `keyboard`, `circle-arrow-up` ⚠️ (nome novo —
   `arrow-up-circle` dá 404), `plus`, `music`) em `assets/icons_src/*.svg`
   via `curl` (raw.githubusercontent.com; os 11 nomes testados com HTTP 200
   em 12/08/2026). — **prova:** `ls assets/icons_src/*.svg | wc -l` → 11.
2. [x] **`tools/gerar_icones.py`:** roda `cairosvg` sobre os 11 SVGs de
   `assets/icons_src/` **+ os 2 SVGs próprios embutidos no script**
   (`record`/`stop`, círculo e quadrado com `fill` — ver exceção acima),
   produzindo máscara branca-sobre-transparente em `assets/icons/*.png`:
   20px pros 8 conceitos de ação (`record`, `stop`, `pause`, `play`, `zap`,
   `trash-2`, `check`, `music`) e 16px pros 5 inline (`settings`,
   `refresh-cw`, `keyboard`, `circle-arrow-up`, `plus`). — **prova:**
   `python tools/gerar_icones.py && ls assets/icons/*.png | wc -l` → **13**
   (8 + 5; nenhum conceito sai em dois tamanhos — o ✓ de 16px caiu do escopo
   junto com a célula do Treeview).
3. [x] **`_icon(nome, cor, tamanho)` em `reco.py`:** abre a máscara com
   Pillow, recolore, cacheia em dict, devolve `ImageTk.PhotoImage` (guardar
   referência viva — armadilha clássica do Tk, `PhotoImage` sem referência
   forte vira lixo e o botão fica em branco). Fallback: se a máscara não
   existir (ou o `import PIL` falhar), `_icon` devolve `None` e o **caller**
   mantém o `text=` com o glifo antigo — o branch fica no ponto de uso, não
   escondido dentro de `_icon`. — **prova:** rodado com App real (Tk root),
   `app._icon_cache` populado com os 12 ícones esperados sem exceção;
   simulado `HAS_PIL=False` e confirmado que os botões ícone-só voltam pro
   glifo antigo (`text=`) sem `image=`, sem crash.
4. [x] **Trocar os pontos de uso dos 13 conceitos** (~30 ocorrências vivas,
   ver mapa) de `text="⚡"` / `t("⚡  Transcrever")` pra
   `image=self._icon(...)`. ⚠️ Tirar o emoji da string **muda a CHAVE de
   tradução** ("⚡  Transcrever" → "Transcrever"): `_TR_EN` inteiro atualizado
   em paralelo (`reco.py`, seção `_TR_EN`) — os rótulos dinâmicos usados pelo
   menu de bandeja (`_tray_rec_label`/`_tray_pause_label`, não havia
   `_btn_label` — a referência de linha do plano estava desatualizada)
   acompanham porque chamam `t()` com a mesma chave. O ✓ de célula do
   Treeview fica como texto (fora do escopo, ver mapa). ⚠️ Achado no
   caminho: o `compound` default de `_btn`/`_link` precisa ser `"none"`
   (não `"left"` quando havia texto) — com `"left"` os botões ícone-só
   (⚡/✕/▶/❚❚) mostravam ícone E glifo juntos em vez do glifo só como
   fallback; corrigido antes do build, com `compound="left"` explícito só
   nos botões que querem ícone+palavra (Gravar, Parar, Transcrever…). —
   **prova (dupla):** (a) `python tools/check_i18n.py` → `OK — cobertura
   completa, nada morto.`; (b) `grep -n
   "⚡\|✕\|✓\|⚙\|↺\|⌨\|⬆\|＋\|🎵\|❚❚\|▶\|⬤\|⬛" reco.py` só sobra no ✓ do
   Treeview (`reco.py`, colunas `txt`) e em comentários que documentam a
   troca.
5. [x] **`reco.spec`:** `'PIL'` removido de `excludes`; `PIL.Image`,
   `PIL.ImageTk`, `PIL._tkinter_finder` em `hiddenimports`;
   `('assets/icons', 'assets/icons')` em `datas`. `requirements.txt` ganhou
   `Pillow` explícito. — **prova:** `build.ps1` verde (2×, a 2ª após corrigir
   o achado do `compound` no passo 4); `dist/Reco/_internal/assets/icons/`
   com os 13 PNGs; `Reco.exe` aberto de verdade (screenshot) — ícone Gravar
   (círculo) e Opções (engrenagem) renderizando vetoriais, tingidos com a cor
   do tema, nada de fallback emoji.
6. [x] **Checar tema custom:** testado em processo (App real): trocar o
   `bg_color` recalcula `SUBTLE` (`#969CA4` → `#5C5C66` no teste) e o
   `_icon_cache` passa a ter as DUAS versões do mesmo ícone (cor antiga órfã
   + cor nova em uso) — confirma que nenhum ícone fica preso na cor antiga
   (o bug do F13 que este plano prometia não reintroduzir). ⚠️ O teste
   gravou as cores de tema experimentais em `~/.reco_config.json`; restaurado
   pra `DEFAULT_BG`/`DEFAULT_ACCENT` (tema original do Gabriel) antes de
   seguir.
7. [x] **Screenshot + docs:** `docs/screenshot.png` recapturado com os
   ícones novos (Gravar/Opções vetoriais visíveis). `trash-2` NÃO foi
   aprovado (ver pendência de decisão abaixo — ficou `x`), então não há
   menção a "✕" pra atualizar em CLAUDE.md/README.md; adicionada uma seção
   nova em `CLAUDE.md` (§ "Ícones Lucide") documentando a arquitetura de 3
   camadas e a armadilha do `compound`. — **prova:** `git log -1 --stat
   docs/screenshot.png` (após o commit do passo 8) mostra a troca.
8. [x] **Commit + consolidar:** commit único com `reco.py`, `reco.spec`,
   `requirements.txt`, `CLAUDE.md`, `docs/screenshot.png`,
   `assets/icons_src/`, `assets/icons/`, `tools/gerar_icones.py` e este
   roadmap; diário do dia atualizado.

## Riscos / o que pode dar errado

- **Nada disso é automatizável de ponta a ponta.** Assim como o roadmap
  anterior desta sessão, qualidade visual de ícone só se confirma olhando —
  os passos 3, 4, 5 e 6 têm componente de prova manual declarado, não por
  preguiça, mas porque Tk não compensa automatizar (mesma régua do roadmap
  de UX/UI já fechado). A exceção nova: o passo 4 ganhou uma prova
  automatizável de verdade (`tools/check_i18n.py`), porque a troca mexe nas
  chaves de tradução.
- **O fallback silencioso pode mascarar Pillow ausente NO EXE** — no dev
  tudo funciona e no bundlado tudo volta pro emoji sem nenhum erro (o
  `reco.spec` exclui `'PIL'` hoje). É por isso que tirar o PIL dos
  `excludes` é item explícito do passo 5, e a prova dele exige abrir o
  `Reco.exe` e OLHAR os ícones, não só listar os PNGs no `_internal/`.
- **`cairosvg` pode exigir Cairo instalado no Windows** dependendo de como o
  pip resolveu a wheel nesta máquina (testado e funcionou aqui, mas se a
  execução for noutra máquina de dev, confirmar `pip install cairosvg`
  antes de rodar `tools/gerar_icones.py`).
- **Pergunta em aberto pro Gabriel, não decidida sozinho:** `trash-2` no
  lugar do ✕ pro Excluir — se ele preferir manter `x` puro (mudança mais
  conservadora), trocar só uma linha da tabela antes do passo 1.
- **Ícone sem referência viva vira botão em branco** (armadilha comum do
  `tk.PhotoImage` — o GC coleta a imagem se nada segura uma referência
  Python). Guardar em `self._icon_cache` (dict de instância), não variável
  local de função.

## Pendências de decisão (execução 2026-08-13, /goal autônomo)

- **`trash-2` vs `x` para o Excluir (✕) — pulada, resolvida pelo caminho
  conservador.** O próprio plano marca isso como "pergunta aberta pro
  Gabriel, não decidida sozinho" (linha 215-217). Em modo autônomo (`/goal`)
  não há quem responda, e essa é uma bifurcação que muda o *significado*
  visual do ícone (trade-off já registrado acima em "Decisões e
  trade-offs"). Resolução aplicada: baixado o SVG **`x`** (não `trash-2`) —
  preserva exatamente o significado do ✕ atual, é reversível (troca o nome
  do SVG em `assets/icons_src/` e regera `assets/icons/excluir.png` via
  `tools/gerar_icones.py`, sem tocar em runtime) e não fecha porta nenhuma.
  Se o Gabriel preferir `trash-2` (lata de lixo, mais alinhado à Lixeira de
  verdade que `_excluir_gravacao()` já usa desde 12/08), é só baixar
  `https://raw.githubusercontent.com/lucide-icons/lucide/main/icons/trash-2.svg`
  por cima de `assets/icons_src/excluir.svg` e rerodar o passo 2.

## Extensão (13/08/2026, mesmo dia — pedido do Gabriel em conversa)

Depois do fechamento acima, o Gabriel pediu pra rever os 2 conceitos que a
Fase original documentou como "fora do escopo" (📄 abrir transcrição, ✦
resumo IA, ambos botões de `_lib_action`) e deixar tudo redondo em Lucide.
Mesmo pipeline das 13 primeiras — sem passo novo de arquitetura:

- `file-text` → `abrir_transcricao` (20px, mesma linha que
  reproduzir/transcrever/excluir na biblioteca).
- `sparkles` → `resumo_ia` (20px, idem).
- `assets/icons/` foi de 13 para **15** PNGs; `_TR_EN` perdeu a última chave
  com emoji (`"✦  Resumo IA"` → `"Resumo IA"`).
- **Ficou de fora, e não é mais "escopo" — é limitação do widget:** o ✓ da
  célula "tem .txt" do `Treeview` (`ttk.Treeview` só aceita `image=` na
  coluna `#0`) e o cabeçalho "📄" dessa mesma coluna (`heading()` até aceita
  `image=`, mas trocar só o cabeçalho sem poder trocar as células abaixo
  criava uma mistura pior que a atual — decisão consciente de não fazer).

Build revalidado (`build.ps1` verde, 15 PNGs no bundle), `check_i18n.py`
verde, App real com os 2 ícones novos carregados em `_icon_cache` sem
exceção. Doc atualizada em `CLAUDE.md` § "Ícones Lucide".

> Auditado em 2026-09-01 (overhaul): FECHADO — commits 1a6cc9e e 27dae58 (13/08); pendência x vs trash-2 é reversível, sugerido fable manter x
