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
executável), recompilar ao final** rodando:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Dev\Reco\build.ps1"
```

O executável distribuído é `dist\Reco\Reco.exe` (PyInstaller, via `reco.spec`).
Sem recompilar, a mudança fica só no fonte e não chega ao app que o Gabriel usa.

- Build normal reaproveita `dist/`/`build/`; use `-Clean` para do zero.
- O modelo Whisper fica em `models\whisper-small-int8-ov`
  ([README do modelo](models/whisper-small-int8-ov/README.md), bundlado, offline);
  se já existir, o build não rebaixa nem rebaixa.
- Warnings esperados e **inofensivos**: `openvino.torch`/`No module named 'torch'`
  (não usamos o frontend PyTorch). Não são erro de build.
- Ship: a pasta `dist\Reco\` inteira; rodar o `Reco.exe` de dentro dela.

## REGRA: transcrição se lê INTEIRA, e "li" é afirmação de fato

⚠️ **`wc -l` no `.txt` ANTES de ler, e ler até a última linha.** `sed -n '1,200p'`
/ `head` / `Read` com `limit` truncam **sem avisar**, e transcrição não tem
sumário: o assunto mais importante pode estar no último terço, depois de meia
hora de outro tema. Nunca dizer "li a transcrição inteira" sem ter conferido a
contagem — é afirmação de verificação, e o Gabriel decide em cima dela.

Caso que gerou a regra (02/09/2026): li 200 de **287** linhas de
`gravacao_reco_2026-09-02_18-09-04.txt`, escrevi "li a transcrição inteira" e
entreguei o inventário. O terço final tinha a resposta da carteira da Renata, o
cancelamento do pedido ao Ruy, a ordem de prioridades de nove dias da Andressa e
o quadro financeiro — nada disso estava no que eu li. Só apareceu por acaso, ao
procurar "Renata" num texto que eu já tinha declarado lido.
Régua transversal: [metodo.md § evidência](../cerebro/temas/harness/metodo.md).

## REGRA: MP3 sempre por container (nunca encoder cru)

⚠️ **Todo MP3 gerado aqui passa por `_open_mp3()` / `MP3Writer` — nunca por bytes
concatenados de um encoder.** Um MP3 sem o header `Xing`/`Info` não declara a
duração, e todo player passa a *estimá-la* pelo bitrate dos primeiros frames.
Como a gravação começa em silêncio (8 kbps) e depois sobe para ~90, a estimativa
saía **até 9× maior** que o real e o VLC mostrava o tempo restante pulando em vez
de descer 1s por segundo. Foi o bug corrigido em 28/07/2026 — o header é escrito
pelo muxer no `close()` do container, então **fechar o container não é opcional**.

Consequências práticas, para não reintroduzir o problema:

- **Não voltar para `lameenc`** (removido em 28/07): a API do python-lameenc não
  expõe `lame_get_lametag_frame`, então não há como escrever o header por ela.
- **ABR, não qscale.** O caminho `global_quality` do PyAV aplica um lowpass que
  corta tudo acima de ~4 kHz (medido: −46 dB e −72 dB nas bandas de 4-6k e 6-8k).
  Para voz é inaceitável e o Whisper piora junto. Use `bit_rate` + `{"abr": "1"}`.
- **Arquivo antigo com duração errada** se conserta por remux, sem re-encodar:
  `tools/reparar_duracao.py <pasta> --aplicar` (valida antes de substituir; sem
  `--aplicar` só relata). Rodado em 28/07/2026 nos 16 arquivos de `Documents\Reco`.

Medições e alternativas descartadas:
`roadmap/2026-07-28-duracao-mp3-e-salvamento-instantaneo.md`.

## Arquitetura essencial (antes de mexer)

- **Formato fixo, não configurável:** 16 kHz estéreo (L=mic, R=sistema), 96 kbps
  ABR. É exatamente o que transcrição + diarização por canal + AEC precisam
  (`OUT_SR`/`OUT_CH`/`MP3_BR`). Não "melhorar" para mono/44.1k sem entender isso.
  (O `MP3_BR` era 128 e **não tinha efeito nenhum** — o LAME em `vbr_mtrh` ignora
  o mean bitrate; medido, os arquivos sempre saíram a ~92 kbps. 96 em ABR
  reproduz esse mesmo resultado, agora de propósito.)
- **Captura:** `soundcard` (WASAPI). `DualRecorder` roda um thread por canal,
  sincronizados por um `threading.Barrier` antes do `__enter__` dos streams.
  Pausar **continua lendo e descarta** os frames (não para o stream) para os dois
  canais caírem em lockstep e L/R não dessincronizarem.
  ⚠️ **A barreira sincroniza o INÍCIO dos streams, não o conteúdo** — o loopback
  entrega o primeiro bloco com um offset próprio, e como o `_pump` pareia por
  contagem de amostras, esse offset ficava gravado no arquivo (medido: +203 ms num
  arquivo, **−399 ms** noutro; o sinal varia). Desde 19/08/2026 o `_pump`
  **alinha** os canais: retém o pareamento nos primeiros ~10 s (até
  `ALIGN_DESISTE_S`=20 s), estima o offset por correlação cruzada
  (`estimar_offset`), descarta as amostras do canal adiantado, e reestima a cada
  `ALIGN_RECHECK_S`=60 s para acompanhar deriva de clock e jitter. Consequências
  que **não** são acidentes: o MP3 só começa a crescer ~10 s depois do start; sem
  eco medível (gravação de fone) o estado vai para `"tentando"` e a gravação segue
  sem alinhar, tentando de novo a cada minuto; `alinhamento()` expõe o estado para
  log/testes. Provas: `tools/test_alinhamento.py` (unitário, sem hardware) e
  `tools/test_gravacao_alinhada.py` (grava tocando áudio de verdade; gate: pior
  janela < 10 ms — medido −0,3 ms). Alinhar **não** é processar o áudio: não há
  filtro nem ganho, só descarte de amostras.
- **Encode em streaming (desde 28/07/2026):** um terceiro thread (`_encode_loop`)
  drena os buffers dos dois canais a cada 200 ms, pareia **só o trecho que ambos
  já entregaram** (`_pump`) e alimenta o `MP3Writer`; a sobra de quem está à
  frente fica em `_buf_mic`/`_buf_sys` até o par chegar — é isso que mantém L/R
  em sincronia. Consequências que **não** são acidentes e não devem ser
  "consertadas":
  - o **arquivo nasce no `start()`**, então o timestamp do nome é o do *início*
    da gravação (era o do fim) e um crash no meio deixa um MP3 parcial válido;
  - o **ganho por canal deixou de ser retroativo** — vale do momento em que o
    slider é movido em diante (era aplicado ao arquivo inteiro no save, o que é
    impossível quando o áudio já foi encodado);
  - `stop()` **não encoda nada**, só drena e fecha (~450 ms, contra ~11 s para
    20 min de gravação antes — e crescendo linearmente). Encodar no thread de
    captura seria pior que o problema original: a leitura do WASAPI atrasaria e o
    buffer estouraria (áudio perdido).
  - canal que falhou (`_fail`) sai do pareamento (`_mic_live`/`_sys_live`) e
    passa a ser preenchido com silêncio — senão um dispositivo morto trava o
    outro canal para sempre.
- **`RECO_TAG`** ("reco" no nome do arquivo) marca gravações dual-channel; só
  essas recebem diarização/AEC na tela de transcrever. Renomear o arquivo perde a marca.
- **Ganho por canal (mic/sys):** multiplicador linear por canal, ajustável ao vivo
  pelo slider arrastável sobre cada VU meter. Escala **bi-linear** com unity (1,0×)
  no centro: metade esquerda 0×..1× (atenua/muta), metade direita 1×..10× (amplifica);
  arrasto snapa em `GAIN_STEP` (0,5). Aplicado em `MP3Writer.feed`, bloco a bloco
  (ver acima: vale dali em diante, não retroativamente);
  o VU meter reflete o nível já ganhado e o multiplicador aparece
  embaixo da barra (`fmt_gain`, ex. "1,0x"). Persistido em `~/.reco_config.json`
  (`mic_gain`/`sys_gain`). Helpers `gain_to_frac`/`frac_to_gain`/`fmt_gain` e
  constantes `GAIN_MIN/UNITY/MAX/STEP`. Decisão e medições:
  [`roadmap/2026-07-15-ganho-por-canal.md`](roadmap/2026-07-15-ganho-por-canal.md).
- **Transcrição:** `OVTranscriber`, in-process. Modelo padrão **`large-v3-turbo`**
  e device `AUTO` → **iGPU** (`resolve_device`, ordem `GPU → NPU → CPU`). As duas
  coisas foram **medidas em 29/07/2026**, não escolhidas por intuição — antes eram
  `small` e NPU-first. Segmenta por VAD (`segmentar_por_vad`/`agrupar_segmentos`,
  não janela cega de 30 s — fallback só se o VAD não achar fala), agrupa até
  `ALVO_ACUMULO_S=3.0` de fala e passa contexto via `initial_prompt` (últimas
  ~30 palavras por canal, desligado na NPU). Diarização usa `dominancia_sistema`
  para descartar trechos do mic dominados pelo sistema (eco/interlocutor), com
  `k_db=15` calibrado em `tools/calibrar_dominancia.py` — **⚠️ calibrar só contra
  "só o mic fala" já apagou fala real uma vez** (29/07), sempre confira também a
  população de double-talk (`ambos`) e valide por diff de transcrição real, não
  só por métrica de bloco. Ver `roadmap/2026-07-29-transcricao-precisa-rapida-e-aec.md`
  e `roadmap/2026-07-29-melhoria-transcricao-ao-vivo-vad-diarizacao.md`.
- **Transcrição ao vivo (`LiveTranscriber`, config `"live"`, default `False`):**
  rascunho durante a gravação, thread própria + `queue.Queue`, alimentada pelo
  `on_pair` do `DualRecorder._pump` (callback só enfileira — nenhum trabalho real
  ali). Segmento fechado (VAD), não janela deslizante — o texto nunca se
  reescreve. Reusa o pipeline já carregado do `OVTranscriber` (nunca uma 2ª
  instância, 828 MB). Ao parar: `LiveTranscriber.stop(wait=True)` **drena a fila
  antes de** disparar a passada final (`_run_live_final_pass` → `_run_transcriber`,
  o mesmo caminho da transcrição manual) — nunca duas chamadas a `pipe.generate`
  no mesmo `WhisperPipeline` ao mesmo tempo. ⚠️ **O teste de estresse de 20 min
  real continua pendente do Gabriel** (memória acumulando, cliques na UI e
  pausa/retomada em prazo longo) — rodado até aqui só um teste automatizado de
  170s (2m50) com WASAPI real (fala tocada pelos alto-falantes, não MP3
  simulado): duração bateu (erro −0,06s), drain em 0,8s, passada final sem
  conflito. Reduz o risco mas **não substitui** os 20 min; não tratar como
  "pronto pra uso diário" até isso acontecer.
- **Defesa anti-loop (`_degenerado` / `_generate_sem_loop`):** o Whisper trava em
  repetição (caso real: `"o que é"` 147× seguidas). ⚠️ **`no_repeat_ngram_size`
  NÃO resolve — o `WhisperPipeline` o ignora em silêncio**, e o GenAI não expõe
  `compression_factor_threshold`/`logprob_threshold`. A defesa é nossa: detecta
  por compressão zlib (> 2,4) e n-grama repetido (> 3×), refaz com temperatura
  0,2/0,4/0,6 e **descarta a janela** se tudo degenerar. Não substituir por
  parâmetro de config achando que existe um.
- **Modelo pedido sem match não cai em silêncio no bundlado (12/08/2026):**
  `_find_model_dir(size)` devolve `None` (não mais o primeiro modelo válido)
  quando o `size` pedido não existe no disco — `ensure_ov_model` baixa de
  verdade nesse caso; só usa o modelo bundlado (`small`) como fallback se o
  download falhar (offline), com status explícito. Antes, máquina nova
  transcrevia para sempre com `small` achando que usava `large-v3-turbo`.
- **Exclusão de gravação vai pra Lixeira (12/08/2026):** `_excluir_gravacao()`
  usa `SHFileOperationW` com `FOF_ALLOWUNDO` (ctypes, sem dependência nova);
  fallback `unlink()` fora do Windows ou se o shell recusar.
- **Instância única (12/08/2026):** mutex `Local\Reco.SingleInstance`
  (`CreateMutexW`) no `__main__`; uma 2ª instância detecta
  `ERROR_ALREADY_EXISTS`, manda a mensagem registrada `Reco.Show` pro
  `HWND_BROADCAST` e sai — `tray._wnd_proc` trata isso como um clique no
  ícone (mostra/ativa a janela da 1ª). `--selftest`/`--transcribe` saem antes
  desse ponto e nunca criam o mutex.
- **Cancelamento de eco (`cancel_echo`):** mínimos quadrados em blocos de 2 s com
  6 taps + pós-supressão residual. Roda **só na transcrição** (canal do mic, com
  o R como referência) — o MP3 é gravado cru, de propósito (limpeza é exportação
  sob demanda, não gravação).
  Números honestos, medidos em 19/08/2026 com `tools/medir_aec.py` em três
  gravações: **ERLE mediano +15,5 / +6,4 / +9,0 dB, com dano na voz do usuário
  ≤ +1,2 dB** (decomposição: +8,7 dB de cancelamento linear, +6,8 dB de
  pós-supressão). O "~7 dB" que estava escrito aqui vinha de uma medição com
  métrica inválida.
  ⚠️ **Duas regras que passam a valer, e a segunda custou uma auditoria inteira:**
  (1) **ERLE sozinho é métrica proibida** — reportar sempre o par (ERLE, dano na
  voz); (2) **rotular far-end/near-end exige o canal do sistema ALINHADO.** Os
  canais saem 200 a 400 ms desalinhados (latência de buffer, sinal variável), e
  rotular por energia simultânea troca eco por voz: nessa métrica o AEC "aparecia"
  destruindo 28 dB da voz do Gabriel, e o diagnóstico que saiu disso está
  registrado como erro em `docs/ARMADILHAS.md`. Use `tools/medir_aec.py` (rotula
  alinhado, é o gate de regressão); `tools/medir_eco.py` **não** serve para julgar
  AEC. Contexto completo:
  [roadmap/2026-08-19-melhoria-antieco-de-verdade.md](roadmap/2026-08-19-melhoria-antieco-de-verdade.md)
  — inclui o acoplamento real (77–93% da energia do mic, medido por regressão com
  controle negativo) e o teto out-of-sample (~2,5 dB: o mic do Intel Smart Sound
  aplica AGC própria e o caminho de eco muda com o conteúdo). A deriva de clock
  (−65,8 ppm em 23/07, +21 ppm em 19/08) é um dos tetos, não o único.
  ⚠️ **Duas ressalvas medidas em 30/08/2026** (detalhe em `docs/ARMADILHAS.md` e
  [roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md](roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md)
  § 4.10–4.13): (1) os números acima são de **janela contígua**, e o pipeline roda
  o AEC sobre partes de grupo VAD **concatenadas** — regime diferente, ainda não
  medido; (2) a rede de segurança do `cancel_echo` compara **RMS global** e por
  isso não pega o caso em que o filtro soma energia (ERLE ≤ 0 em 2 de 13
  gravações, ambas com salto de alinhamento).

## Ferramentas de apoio (`tools/`)

Rodam pelo fonte, com o venv do projeto — não entram no executável.

| script | para quê |
| --- | --- |
| `test_encoder.py` | testa o encoder sem hardware: duração declarada == real, header Xing, L/R separados, pareamento dos canais e `close()` instantâneo. Rodar **sempre** que mexer em `MP3Writer`/`_pump` |
| `test_gravacao_real.py [seg]` | grava de verdade pelos dispositivos padrão, mede o tempo do `stop()` e apaga o MP3 no fim |
| `reparar_duracao.py <pasta> [--aplicar]` | conserta a duração de MP3 antigos por remux (ver a regra acima) |
| `test_antiloop.py <mp3> [modelo] [device]` | roda as janelas mais fracas com e sem a defesa anti-loop. Rodar **sempre** que mexer em `_degenerado`/`_generate_sem_loop`. Critério: nenhum n-grama > 3× |
| `test_e2e.py <mp3>` | transcrição ponta a ponta pelo caminho real do app (decode + diarização + AEC + anti-loop), com tempo e extrapolação para 2 h |
| `test_alinhamento.py` | alinhamento dos canais **sem hardware**: offset positivo/negativo, retenção inicial, correção de deriva, fone (sem eco → não alinha), canal único. Rodar **sempre** que mexer em `estimar_offset`/`_al_*`/`_pump` |
| `test_gravacao_alinhada.py [seg]` | prova ponta a ponta do alinhamento: grava de verdade **tocando áudio pelos alto-falantes** (sem isso não há eco para correlacionar) e mede o atraso residual por janela. Gate: pior janela < 10 ms. Medido em 19/08: **−0,3 ms** (era +203 ms) |
| `alinhar_gravacao.py <mp3\|pasta> [--aplicar]` | conserta gravações **antigas**, escrevendo `<nome>_alinhado.mp3` ao lado (nunca sobrescreve — o áudio é re-encodado). Reestima o deslocamento a cada 30 s, então corrige a deriva dentro do arquivo. Streaming, um passe, sem `seek` |
| `varrer_acervo.py <pasta\|mp3> [--janela 15]` | **diagnóstico** do desalinhamento no acervo, somente-leitura: por janela, dá **pior janela**, **amplitude da faixa** (= salto) e **% do tempo acima de 50 ms** — não mediana, que engana neste defeito. Use este para "quantas gravações têm salto"; o `alinhar_gravacao.py` é para consertar uma. Medido em 30/08: **37% das gravações medíveis têm salto** |
| `varrer_aec.py <mp3...> [--acervo N]` | distribuição de **(acoplamento, ERLE, dano na voz)** em várias gravações, com a rotulagem alinhada do `medir_aec.py`. Foi ele que mostrou que **o acoplamento não prediz se o AEC ajuda** (ver `docs/ARMADILHAS.md`) |
| `test_relogio_captura.py [seg]` | deriva de cada canal contra o relógio de parede, sem escrever MP3 — abre os dois recorders como o `DualRecorder` e conta frames. **Mede** se o loopback estava mudo (RMS) em vez de confiar na lembrança, e compara a deriva com o que o alinhador já corrige (50 ms/min). Medido em 30/08 com a caixa muda: **+4,1 ms/min relativo** |
| `medir_aec.py <mp3> [janelas] [dur]` | par **(ERLE, dano na voz)** com rotulagem alinhada — é o gate de regressão do AEC. Use este, não o `medir_eco.py`, para julgar cancelamento de eco |
| `medir_eco.py <mp3>` | acoplamento caixa→mic e ERLE do `cancel_echo` **em áudio real**. Rodar **sempre** que mexer no AEC — validar em eco sintético já mascarou uma implementação que entregava 3 dB. ⚠️ **A métrica é enviesada** (19/08/2026): mede acoplamento só em blocos com o mic quase mudo, então subestima o eco por construção, e não mede dano na voz. Ver `docs/ARMADILHAS.md` e a Fase 3 do roadmap de 19/08; até lá, o número que ela imprime é piso, não valor |
| `bench_final.py <mp3> [n]` | device × modelo: velocidade, extrapolação p/ 2 h, e qualidade por divergência (WER) contra o melhor modelo disponível. `BENCH_MODELOS`/`BENCH_DEVICES`/`BENCH_MODO=fracas` filtram |
| `bench_convivencia.py <mp3> [n]` + `vizinho.py` | quanto a transcrição atrasa **outro app** (latência de um vizinho single-thread em processo separado). É o que decide iGPU × NPU |
| `bench_convivencia_pipeline.py <mp3>` | igual acima, mas com o pipeline REAL (`OVTranscriber.transcribe`, VAD+contexto+dominância) em vez de `pipe.generate()` cru — o que decide o orçamento de device do modo ao vivo |
| `calibrar_dominancia.py <mp3...>` | calibra `k_db` de `dominancia_sistema` contra `so_sys`/`so_mic`/`ambos` (double-talk) — rodar de novo com mais gravações se mexer no limiar; **sempre conferir por diff de transcrição real depois**, métrica de bloco sozinha já mascarou perda de fala real |
| `test_live.py <mp3> [seg]` | alimenta `LiveTranscriber` com um MP3 real em tempo real simulado (resample 16k→48k→16k), mede latência mediana do rascunho |
| `test_live_integration.py [seg]` | grava de verdade com `DualRecorder`+`LiveTranscriber` ligados, confere duração do MP3, tempo de drain e a passada final rodando sem conflito depois |
| `bench_latencia_stt.py [--modelo X] [--device AUTO\|CPU]` | latência de **frase curta** (5/10/20 s, `temp/bench-voz/clip*.wav`) pelo pipeline real, com aquecimento separado — o RTF de lote não responde "quanto demora uma frase". Criado em 28/08/2026 para decidir STT local × nuvem no jarvis da central (`central/roadmap/2026-08-28-jarvis-voz-e-notebook.md`, Fase 0). Saída em `temp/bench-voz/bench-local-<device>.json` |
| `transcrever.py <arquivo...>` | transcreve **qualquer áudio/vídeo** para `<arquivo>.txt` pelo pipeline real (decode PyAV → VAD → anti-loop), sem UI — feito para **agentes** (Claude Code) lerem áudio que o Gabriel manda no chat. Pula `.txt` existente (`--forcar` refaz); `--diarizar`/`--aec` só para gravações estéreo do próprio Reco |

## Biblioteca de gravações e resumo IA (12/08/2026)

- A view **"Gravações…"** lista a pasta de saída (duração via PyAV, cache por
  `(path, mtime)`), com busca por nome E por conteúdo dos `.txt` e ações
  reproduzir/transcrever/abrir transcrição/resumo IA/excluir (excluir =
  Lixeira; o `.txt`/`.resumo.md` ficam — o transcript sobrevive ao áudio).
  Desde 13/08/2026 as 5 são ícones **Lucide** vetoriais (`_icon()`,
  [roadmap/2026-08-12-icones-lucide.md](roadmap/2026-08-12-icones-lucide.md)).
  O ✓ da coluna "tem .txt" do `Treeview` continua texto — limitação do
  widget (`ttk.Treeview` só aceita imagem na coluna `#0`; célula de coluna
  comum é texto), não escolha de escopo. **O filesystem é o banco** (mp3 +
  .txt + .resumo.md lado a lado) — sem SQLite, de propósito.
- ⚠️ **A biblioteca lê só `x.txt`; o `tools/transcrever.py` escreve `x.mp3.txt`**
  — 14 dos 47 `.txt` da pasta são invisíveis para a view (sem ✓, fora da busca por
  conteúdo). Ler as duas convenções, escrever uma: `docs/ARMADILHAS.md` §
  "A biblioteca não enxerga as transcrições do `tools/transcrever.py`".
- ⚠️ **Áudio corrigido não apaga texto velho:** existindo `x_alinhado.mp3`, ele é a
  fonte canônica da transcrição do par; o `.txt` do original sobrevive, só deixa de
  ser o mostrado. Regra completa em
  [roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md](roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md) § 6.6.
- **✦ Resumo IA**: roda `claude -p --model sonnet` (CLI do Claude Code do
  usuário, via `shutil.which`) com a transcrição no stdin e salva
  `<gravação>.resumo.md`; se já existe, abre (refazer = excluir o .md). Sem
  CLI → status claro; o app segue 100% funcional sem ele. Prompt em
  `PROMPT_RESUMO`; opt-in por clique, nunca automático (consome a assinatura
  do usuário). ⚠️ **O `--model sonnet` é cravado de propósito**: sem a flag, o
  `-p` herda o default da máquina — que aqui era Fable 5 (1M) em effort
  xhigh, o tier mais caro possível, para um resumo de reunião. Trocar o
  modelo = editar a flag em `_lib_resumo`, nunca removê-la.
- Origem, análise de mercado (Meetily/Char/anarlog/OGAD) e os NÃO-fazer
  (tela no Windows, LLM embarcado, SQLite, notepad):
  [roadmap/2026-08-12-melhoria-biblioteca-e-resumo.md](roadmap/2026-08-12-melhoria-biblioteca-e-resumo.md).

## Ícones Lucide (13/08/2026)

15 dos botões que eram emoji de texto (mistura `Segoe UI Symbol`/`Segoe UI
Emoji` inconsistente entre si) agora são ícones **Lucide** vetoriais, tingidos
com a cor do tema em runtime — os 13 do roadmap original mais `abrir_transcricao`
(📄, `file-text`) e `resumo_ia` (✦, `sparkles`), fechados no mesmo dia numa
segunda passada. Três camadas — decisão e trade-offs completos em
[roadmap/2026-08-12-icones-lucide.md](roadmap/2026-08-12-icones-lucide.md):

1. `assets/icons_src/*.svg` — fonte Lucide baixada (dev-time, versionada, NÃO
   entra no bundle).
2. `tools/gerar_icones.py` — rasteriza cada SVG numa máscara PNG
   branco-sobre-transparente em `assets/icons/*.png` (essas sim entram no
   `.exe`, `reco.spec` `datas`). Rodar de novo só se trocar/adicionar ícone.
3. `App._icon(nome, cor, tamanho)` (runtime) — abre a máscara com Pillow,
   recolore via `putalpha`, cacheia em `self._icon_cache` por
   `(nome, cor, tamanho)`. Troca de tema/`_rebuild_ui` só pede a cor nova; o
   cache velho fica órfão e não atrapalha.

⚠️ **Fallback nunca escondido em `_icon()`.** Se a máscara ou o Pillow faltar,
`_icon()` devolve `None` e quem chamou decide — `_btn`/`_link` só setam
`image=` quando `_icon()` teve sucesso, então o `text=` (emoji/palavra antigos)
sempre passado pelo caller aparece sozinho. **Nunca** passar `text=""` num
botão ícone-só sem essa rede: viraria botão em branco silencioso se o Pillow
faltar no bundle (era exatamente esse bug que o `reco.spec` excluindo `'PIL'`
de propósito ia reintroduzir — corrigido junto: `'PIL'` saiu dos `excludes`,
entrou em `hiddenimports` com `PIL._tkinter_finder`).

⚠️ **`compound` decide se o ícone substitui ou acompanha o texto.** Default de
`_btn`/`_link` é `compound="none"` (Tk: só a imagem aparece se ela existir,
senão o texto) — é o que faz o fallback funcionar. Botão com ícone **e**
palavra visíveis ao mesmo tempo (ex. "Gravar", "Transcrever") precisa passar
`compound="left"` explicitamente na chamada — sem isso o ícone esconde o
texto por padrão.

O "✓" de célula da coluna "tem .txt" do Treeview da biblioteca continua
texto — não é escolha de escopo, é limitação do widget: `ttk.Treeview` só
aceita `image=` na coluna `#0` (a árvore em si), célula de coluna comum é
texto e ponto. O cabeçalho dessa mesma coluna ("📄") também ficou como
glifo — `Treeview.heading()` aceita `image=`, mas trocar só o cabeçalho
sem poder trocar as células abaixo dele criava uma mistura pior que a
consistente hoje.

## Config e persistência

`~/.reco_config.json` via `load_config`/`save_config` (escrita atômica). Defaults
em `_CFG_DEFAULTS`. Ao adicionar uma opção nova, incluir o default lá.

⚠️ **Mudar um default NÃO alcança quem já usou o app.** `load_config()` deixa o
arquivo salvo sobrescrever os defaults — o que é correto (a escolha do usuário
tem de ganhar), mas significa que trocar `_CFG_DEFAULTS` é inócuo para qualquer
config existente. Se a mudança **precisa** chegar aos usuários atuais, suba
`CFG_MIGRACAO` e trate o caso em `_migra_config()`: ela roda uma vez (marcada em
`_migracao` dentro do próprio JSON) e só promove valores que eram o **default
antigo**, preservando escolha deliberada. Foi assim que `small` → `large-v3-turbo`
e `NPU` → `AUTO` chegaram na máquina do Gabriel em 29/07/2026.

## Armadilhas

[`docs/ARMADILHAS.md`](docs/ARMADILHAS.md) — o que **parece** funcionar e não
funciona, com sintoma e causa. Ler antes de mexer em transcrição, AEC ou
benchmark; várias entradas já custaram tempo uma vez.

## Ritual

Segue o ritual da raiz `C:\Dev` (plano em `roadmap/`, docs atualizadas,
consolidado datado em `docs/CONSOLIDADO-<data>.md` ao fim). A regra de compilar
acima é específica deste projeto e **não** é opcional.
