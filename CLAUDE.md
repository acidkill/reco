# Reco — instruções do projeto

App de desktop (Windows, Tkinter) que grava **microfone + áudio do sistema**
(WASAPI loopback) num MP3 estéreo (L=mic, R=sistema) e transcreve localmente via
OpenVINO GenAI (Whisper), com diarização por canal e cancelamento de eco.
Todo o código vive em `reco.py` (um arquivo só) + `tray.py` (bandeja).

**Memória/decisões deste projeto:** `C:\Dev\cerebro\projetos\reco.md`
(local desta máquina — ler antes de decisão não-trivial: pendências abertas,
decisões recentes).

## REGRA: sempre compilar após alterar o código

**Toda vez que mexer em `reco.py`/`tray.py` (ou qualquer coisa que entre no
executável), recompilar ao final** rodando
`powershell -ExecutionPolicy Bypass -File "C:\Dev\Reco\build.ps1"`. Sem
recompilar, a mudança fica só no fonte e não chega ao app que o Gabriel usa.
Detalhe (`-Clean`, modelo bundlado, warnings inofensivos, o que se distribui):
[docs/ARQUITETURA.md § Build](docs/ARQUITETURA.md#build).

## REGRA: transcrição se lê INTEIRA, e "li" é afirmação de fato

⚠️ **`wc -l` no `.txt` ANTES de ler, e ler até a última linha.** `sed -n '1,200p'`
/ `head` / `Read` com `limit` truncam **sem avisar**, e transcrição não tem
sumário: o assunto mais importante pode estar no último terço. Nunca dizer "li a
transcrição inteira" sem ter conferido a contagem — é afirmação de verificação, e
o Gabriel decide em cima dela.

**Áudio/vídeo que chega ao chat em qualquer projeto de `C:\Dev` se transcreve
aqui:** `python tools/transcrever.py <arquivo...>` (gera `<arquivo>.txt`, sem UI;
`--forcar` refaz). O caso que gerou a regra:
[docs/ARMADILHAS.md](docs/ARMADILHAS.md) § "Ler 200 de 287 linhas"; régua
transversal: [metodo.md § evidência](../cerebro/temas/harness/metodo.md).

## REGRA: MP3 sempre por container (nunca encoder cru)

⚠️ **Todo MP3 gerado aqui passa por `_open_mp3()` / `MP3Writer` — nunca por bytes
concatenados de um encoder**, e **fechar o container não é opcional**: é o
`close()` que escreve o header `Xing`/`Info` com a duração. Não voltar para
`lameenc`; ABR (`bit_rate` + `{"abr": "1"}`), nunca `global_quality`/qscale.
Arquivo antigo com duração errada: `tools/reparar_duracao.py <pasta> --aplicar`.
Bug de origem, medições e descartes:
[docs/ARQUITETURA.md § MP3 por container](docs/ARQUITETURA.md#mp3-por-container-o-bug-e-o-que-não-reintroduzir).

## Antes de mexer no código: o que não é acidente

1. **Formato fixo, não configurável** — 16 kHz estéreo (L=mic, R=sistema), 96 kbps
   ABR é exatamente o que transcrição + diarização por canal + AEC precisam.
2. **O `_pump` alinha os canais em runtime** (`estimar_offset`): por isso o MP3 só
   começa a crescer ~10 s depois do start, e sem eco medível ele não alinha.
3. **Encode em streaming** — o arquivo nasce no `start()`, o ganho por canal não é
   retroativo e `stop()` só drena e fecha. Nada disso deve ser "consertado".
4. **AEC roda só na transcrição**, o MP3 é gravado cru — e **ERLE sozinho é
   métrica proibida**: sempre o par (ERLE, dano na voz), por `tools/medir_aec.py`.
5. **A defesa anti-loop é nossa** (`_degenerado`/`_generate_sem_loop`): o
   `WhisperPipeline` ignora `no_repeat_ngram_size` em silêncio.

Cada uma com número medido, consequência e prova:
[docs/ARQUITETURA.md](docs/ARQUITETURA.md).

## Ícones e biblioteca de gravações

Botão de ícone usa `App._icon()` sobre as máscaras de `assets/icons/`
(`tools/gerar_icones.py`): **nunca passar `text=""` num botão ícone-só** — o
texto é o fallback se o Pillow ou a máscara faltar. Na biblioteca, **o filesystem
é o banco** (mp3 + .txt + .resumo.md lado a lado), sem SQLite, de propósito.
Camadas, `compound` e o resumo IA (`claude -p --model sonnet`, flag cravada):
[docs/ARQUITETURA.md](docs/ARQUITETURA.md); decisão dos ícones:
[roadmap/2026-08-12-icones-lucide.md](roadmap/2026-08-12-icones-lucide.md).

## Config: opção nova entra em `_CFG_DEFAULTS`

⚠️ **Mudar um default NÃO alcança quem já usou o app** — o `~/.reco_config.json`
salvo vence. Para a mudança chegar aos atuais, subir `CFG_MIGRACAO` e tratar o
caso em `_migra_config()`:
[docs/ARQUITETURA.md § Config](docs/ARQUITETURA.md#config-e-persistência).

## Onde está o resto

| quero… | leio |
| --- | --- |
| o que **parece** funcionar e não funciona | [docs/ARMADILHAS.md](docs/ARMADILHAS.md) — ler antes de mexer em transcrição, AEC ou benchmark |
| como cada peça funciona por dentro | [docs/ARQUITETURA.md](docs/ARQUITETURA.md) |
| para que serve cada script de `tools/` | [docs/TOOLS.md](docs/TOOLS.md) |
| por que decidimos X, e o que foi descartado | [roadmap/README.md](roadmap/README.md) |

## Ritual

Segue o ritual da raiz `C:\Dev` (plano em `roadmap/`, docs atualizadas,
consolidado datado em `docs/CONSOLIDADO-<data>.md` ao fim). A regra de compilar
acima é específica deste projeto e **não** é opcional.
