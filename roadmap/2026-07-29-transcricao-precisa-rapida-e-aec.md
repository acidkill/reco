# 2026-07-29 — Transcrição precisa e rápida + cancelamento de eco

**Estado (29/07/2026):** Fases 1, 2 e 4-item-1 **executadas e validadas**.
Fase 3 (VAD) e Fase 4 itens 2-3 (dominância de canal, export limpo) pendentes.
Resumo do que foi entregue em § 6, no fim.
**Pedido do Gabriel (29/07/2026), literal:** (a) resolver o eco da caixa de som
entrando no microfone; (b) a transcrição do app está "lenta e problemática" — uma
palavra repete dezenas de vezes — a ponto de ele ter migrado para o NotebookLM;
(c) precisa dar conta de **arquivos de 10 min a 2 h**, com precisão e rapidez, no
notebook dele, **sem estragar a experiência em outros apps**.

Carta branca explícita para trocar modelo, tamanho e device.

---

## 1. O que foi medido (nada aqui é suposição)

Máquina: Intel Core Ultra 5 225H — CPU + iGPU Arc + NPU AI Boost.
Scripts de medição em `temp/` (`bench_transcricao.py`, `bench_final.py`,
`bench_convivencia.py`, `vizinho.py`, `medir_eco.py`, `aec_v2.py`).

### 1.1 A alucinação de repetição — causa confirmada

Caso real, no arquivo do próprio Gabriel
(`Documents/Reco/gravacao_reco_2026-07-18_18-26-08.txt`): o trecho `"o que é"`
aparece **147 vezes consecutivas** (~1.300 caracteres de lixo em 28.882 = 4,5% do
arquivo), no canal do interlocutor, logo após um segmento quase vazio.
Reproduzido no benchmark: `small` no CPU gerou o **mesmo** loop de 147× `"o que é"`.

Três causas somadas:

1. **`cfg.no_repeat_ngram_size = 4` (`reco.py:1340`) não faz nada.** O atributo
   existe no `WhisperGenerationConfig` (então não cai no `except`), mas o
   `WhisperPipeline` o ignora. Provado: rodando com `no_repeat=off` e `=4` os
   textos saem **byte-idênticos** (2590/2590 e 1427/1427 caracteres). A única
   defesa contra loop no código é decorativa.
2. **Não existe fallback por temperatura.** O `openvino_genai` 2026.2.1 não expõe
   `compression_factor_threshold`, `logprob_threshold` nem
   `condition_on_prev_tokens` — é com esse trio que o Whisper de referência
   detecta uma janela degenerada e a refaz. Precisa ser escrito no nosso código.
3. **Janela de 30 s cortada às cegas** (`reco.py:1359-1384`), sem VAD e sem
   overlap. O filtro de silêncio é um RMS da janela **inteira**: 2 s de fala em
   28 s de silêncio passam pela peneira e vão inteiros para o decoder — exatamente
   a condição em que ele degenera.

### 1.2 Modelo: `large-v3-turbo` praticamente elimina o problema

Janelas "fracas" (as de menor energia acima do limiar — onde o Whisper degenera),
6 janelas da gravação de 80 min:

| modelo/device | maior repetição | compressão | WER vs. referência |
| --- | --- | --- | --- |
| `small` \| GPU | **221×** `"que é"` | 4,44 | 232,6% |
| `small` \| NPU | 14× | 2,42 | 58,1% |
| `large-v3-turbo` \| GPU | 3× `"Alô?"` | 1,85 | — (referência) |
| `large-v3-turbo` \| NPU | 2× `"Oi."` | 1,85 | 7,4% |

Fala normal, 4 janelas: `small` diverge 13–16% do turbo; o turbo é consistente
entre devices (1,1–2,6%). Compressão > 2,4 é o limiar de "texto degenerado" do
Whisper — o `small` estoura, o turbo não.

**Custo:** velocidade limpa (máquina ociosa), turbo/GPU 11,1× tempo real contra
small/GPU 14,9× — 25% mais lento por uma qualidade incomparavelmente melhor.
Modelo já baixado (828 MB) em `~/AppData/Local/Reco/models/`.

### 1.3 Device

Velocidade limpa, sem carga concorrente:

| | CPU | iGPU | NPU |
| --- | --- | --- | --- |
| `small` | 6,8× | **14,9×** | 9,8× |
| `large-v3-turbo` | 4,4× | **11,1×** | 5,5× |

⚠️ **A NPU levou 415 s para compilar o turbo na primeira vez.** Fica em
`CACHE_DIR` (`reco.py:1325`), então é uma vez só — mas é uma vez só *por modelo*,
e sem aviso na tela o usuário acha que travou.

⚠️ **O CPU não é apenas lento: com `small` ele alucina muito mais** (2.590 chars
contra 1.427 da GPU no mesmo áudio; compressão 4,05 vs 2,17). Com o turbo esse
efeito desaparece (compressão 2,26).

### 1.4 Convivência com outros apps

Primeira tentativa **descartada por erro de método**: o "vizinho" usava
`numpy @ numpy`, que aciona BLAS multi-thread e satura todos os núcleos —
simulava um segundo job pesado, não um app interativo. Produziu resultado
incoerente (combinações aparecendo mais rápidas que a máquina ociosa).

Refeito em `temp/bench_convivencia.py`: vizinho em **processo separado**,
single-thread, acordando a cada 50 ms para um trabalho fixo — o perfil de um app
de interface. Métrica: **latência** (quantas vezes mais devagar esse trabalho fica
durante a transcrição), não throughput.

| combinação | 2 h de áudio | app vizinho | acorda p95 |
| --- | --- | --- | --- |
| turbo \| **iGPU** | **12,1 min** | 1,0× | 2,0 ms |
| turbo \| NPU | 18,8 min | 1,0× | 1,2 ms |
| turbo \| CPU | 64,5 min | 0,7× | 0,7 ms |
| `small` \| NPU | 12,7 min | 1,0× | 1,8 ms |
| `small` \| iGPU | 16,6 min | 1,0× | 1,7 ms |
| `small` \| CPU | 28,8 min | 0,9× | 0,8 ms |

(baseline ocioso: trabalho 4,2 ms, acorda p95 1,4 ms)

**Nenhum device degradou o app vizinho** — todos em 1,0×. O "0,7×" do CPU não é
erro: com a máquina ociosa o processador fica em frequência baixa, e a carga da
transcrição faz o governor subir o clock, então o vizinho roda *mais rápido* do
que no baseline. É o baseline que estava medido com a CPU dormindo.

⚠️ **Limitação declarada:** o vizinho é puro CPU. Este teste **não** mede a iGPU
disputando com renderização de tela e vídeo — o cenário "transcrever durante uma
reunião com câmera", onde a iGPU é o único dos três que compete pelo mesmo
hardware que desenha a imagem.

**Sinal indireto de que essa disputa é real:** sob carga concorrente, `small` na
iGPU caiu de 14,9× para 7,2× (−52%); na NPU foi de 9,8× para 9,5× (−3%). A NPU é
praticamente imune à contenção; a iGPU não é.

⚠️ **Variância entre rodadas é alta** (turbo/iGPU: 11,1× limpa, 9,9× com vizinho;
turbo/NPU: 5,5× e 6,4×). Os números valem como ordem de grandeza e para comparação
relativa, não como cronometragem exata.

### 1.5 O eco

| gravação | acoplamento caixa→mic | ERLE do `cancel_echo` atual |
| --- | --- | --- |
| 27/07 (5,7 min) | −17,9 dB | **+3,2 dB** |
| 23/07 (80 min) | −23,7 dB | **+3,7 dB** |

Acoplamento forte (abaixo de −35 dB seria desprezível). O AEC atual entrega ~3 dB
onde um AEC decente entrega 20–40. O docstring (`reco.py:704`) afirma 37 dB, e é
verdade — em eco **sintético**, linear e invariante. Não é bug: o modelo é que
está errado. Um ganho complexo fixo por bin, estimado uma vez por janela de 30 s,
assume que o caminho acústico é invariante por 30 s e cabe num quadro de FFT
(64 ms). Nenhuma das duas vale numa sala real.

**Protótipo v2** (`temp/aec_v2.py`): mínimos quadrados em blocos de 2 s com 6 taps
(~96 ms de cobertura) + pós-supressão residual. Estável por construção (solução
fechada com regularização de Tikhonov — a primeira tentativa, um NLMS, divergiu
para −38 dB porque a normalização explode quando a referência silencia).
Resultado: **+7,2 dB**, com apenas **+0,3 dB** de perda na voz do Gabriel.

**Por que não chega a 20 dB — deriva de clock.** Mic e loopback são dois relógios
de hardware independentes:

| gravação | atraso mic↔loopback | desvio-padrão | deriva |
| --- | --- | --- | --- |
| 27/07 (5,7 min) | 608 amostras (38 ms) | 0 | 0,1 ppm |
| 23/07 (80 min) | 2283 amostras (143 ms) | 2314 | **−65,8 ppm** (≈ −237 ms/h) |

Em gravação curta o alinhamento é estável; em 80 min ele passeia. Nenhum filtro
linear sobrevive a isso sem compensação contínua de deriva.

**Consequência de projeto:** perseguir AEC perfeito é caro e incerto. Para o
objetivo real — diarização e transcrição corretas — o caminho barato é decidir o
locutor por **dominância de energia entre canais alinhados**, que é robusto a eco
residual e não depende de cancelar nada.

---

## 2. Decisões

1. **Modelo padrão passa a ser `large-v3-turbo`.** Justificado por 1.2: mata a
   alucinação, que é a queixa principal, ao custo de 25% de velocidade.
2. **A defesa anti-loop é escrita no nosso código**, não configurada no GenAI —
   porque o parâmetro que existe é ignorado e o mecanismo certo não existe (1.1).
3. **Device: decidir por 1.3 + 1.4**, com o critério do Gabriel (rápido **e** sem
   atrapalhar outros apps), não por preferência.
4. **AEC v2 entra** (dobra o ERLE, custo baixo, não danifica a voz), mas **sem
   prometer que o eco some**. O ganho grande para a transcrição vem da dominância
   de canal, não do AEC.
5. **Não processar o áudio na gravação.** O MP3 L/R separado é o que permite
   refazer o processamento depois com um filtro melhor; gravar já processado é
   irreversível. Limpeza é exportação sob demanda.

---

## 3. Fases

### Fase 1 — Matar a alucinação (maior ganho, menor risco)

**Arquivo:** `reco.py`, classe `OVTranscriber`.

1. Escrever `_degenerado(txt) -> bool`: `True` se a taxa de compressão zlib do
   texto passar de 2,4 **ou** se algum n-grama de 1–8 palavras se repetir mais de
   3× consecutivas. (Referência de implementação: `pior_loop`/`compressao` em
   `temp/bench_final.py`.)
2. Em `_transcribe_channel` (`reco.py:1348`), após `pipe.generate`, testar o
   resultado. Se degenerado: repetir a janela com `cfg.temperature` escalando
   0,2 → 0,4 → 0,6 (e `cfg.do_sample = True`). Se as três tentativas
   degenerarem, **descartar a janela** e registrar no log — texto ausente é
   melhor que 147 repetições.
3. Remover o `try/except` mudo do `no_repeat_ngram_size` (`reco.py:1339-1342`) ou
   deixá-lo com um comentário dizendo que o `WhisperPipeline` o ignora — para
   ninguém "consertar" achando que funciona.

**Pronto quando:** transcrever `gravacao_reco_2026-07-18_18-26-08.mp3` e o
`"o que é"` 147× não aparecer; nenhum n-grama repetindo > 3×.

### Fase 2 — Modelo e device

1. `_CFG_DEFAULTS` (`reco.py:110`): `"model": "large-v3-turbo"`, e `"device"`
   com o vencedor de 1.3/1.4.
2. `resolve_device` (`reco.py:742`): a ordem `NPU → GPU → CPU` do fallback
   (`reco.py:749`) foi escolhida sem medição. Reordenar conforme 1.3/1.4.
3. Reexpor o seletor de modelo e device na tela avançada — foi removido
   (`reco.py:2068-2069`) quando tudo virou automático; com dois modelos válidos
   volta a fazer sentido. As strings de tradução para isso **ainda existem**
   (`reco.py:277-278`).
4. **Aviso de compilação:** na primeira vez em cada (modelo, device), mostrar
   "Preparando modelo… pode levar alguns minutos (só na primeira vez)". Sem isso,
   os 415 s da NPU parecem travamento.

**Pronto quando:** app novo transcreve com o turbo no device escolhido e o tempo
de 2 h de áudio bate com o previsto em 1.3 (±20%).

### Fase 3 — VAD, para ganhar velocidade e reduzir alucinação

Hoje o corte é cego a cada 30 s. Com VAD por energia em quadros de 30 ms
(histerese de ~300 ms para não picotar), montar janelas de até 30 s que **terminem
em silêncio**. Dois ganhos: pula silêncio de verdade (hoje 30 s com 2 s de fala
custam 30 s de decoder) e nunca corta no meio de uma palavra.

**Pronto quando:** o tempo total cair em gravação com muito silêncio, sem perda de
texto (comparar com a saída da Fase 2 no mesmo arquivo).

### Fase 4 — Eco

1. Portar `cancel_echo_v2` de `temp/aec_v2.py` para `reco.py`, substituindo
   `cancel_echo` (mantendo a assinatura). Guardar a rede de segurança que devolve
   o original se a saída ficar maior que a entrada.
2. **Dominância de canal na diarização:** por bloco de 100 ms, com os canais
   alinhados, se a energia do canal do sistema domina a do mic por mais de X dB,
   o bloco é do interlocutor — não do Gabriel. Calibrar X com as gravações reais.
3. **Exportar MP3 limpo sob demanda** (botão na tela de transcrever), nunca no
   caminho de gravação.

**Pronto quando:** ERLE medido por `temp/aec_v2.py` ≥ +7 dB com perda de voz
≤ 1 dB, e a diarização parar de atribuir ao Gabriel falas do interlocutor.

---

## 4. Fora de escopo / registrado como impraticável

- **AEC de 20–40 dB.** Exigiria compensação contínua de deriva de clock (−66 ppm
  medidos) e tratamento de não-linearidade da caixa. Caro, incerto, e desnecessário
  se a diarização passar a usar dominância de canal.
- **WebRTC AEC3 em tempo real** (`pywebrtc-audio`): dependência binária nova no
  PyInstaller e o mesmo problema de deriva. O Reco não precisa ser tempo real —
  processar offline é uma vantagem que o Discord não tem.
- **RNNoise / supressão de ruído** (`arnndn` já está disponível no PyAV instalado,
  só falta um modelo `.rnnn` de ~85 KB). É outro problema — ruído de fundo, não
  eco. Só entra se o Gabriel reclamar de ventilador/teclado.
- **Fone de ouvido elimina o eco na origem**, custo zero. Continua sendo a solução
  mais eficaz; o AEC é para quando não dá para usar fone.

---

## 6. O que foi executado em 29/07/2026

### Fase 1 — anti-loop ✅

`reco.py`: `OVTranscriber._degenerado()`, `_texto_de()`, `_generate_sem_loop()`,
ligados em `_transcribe_channel`. O `no_repeat_ngram_size` saiu, substituído por um
comentário explicando que o `WhisperPipeline` o ignora (para ninguém "consertar").

**Validação** (`temp/test_antiloop.py`, nas 6 janelas mais fracas da gravação de
80 min, `small` @ iGPU — o caso que produzia o pior loop):

| | caracteres | pior repetição |
| --- | --- | --- |
| sem defesa | 2276 | **221×** `"que é"` |
| com defesa | 945 | **2×** |

Uma janela foi descartada por degenerar nas três temperaturas — comportamento
desejado. Custo 1,53×, **medido no pior caso possível** (janelas onde toda
retentativa dispara); em fala normal não há retentativa e o custo é ~1,0×.

### Fase 2 — modelo e device ✅

- `_CFG_DEFAULTS["model"] = "large-v3-turbo"`.
- `resolve_device`: ordem `GPU → NPU → CPU` (era NPU-first, sem medição).
- **Migração de config** (`_migra_config` + `CFG_MIGRACAO`) — sem isto a mudança
  não alcançaria ninguém: o `~/.reco_config.json` do Gabriel tinha
  `"model": "small"` e `"device": "NPU"` gravados, e `load_config()` deixa o
  arquivo salvo vencer os defaults. Testada: promove os defaults antigos, não
  repete, e **preserva escolha deliberada** (`medium`/`CPU` ficam intactos).
- Seletor de **device** de volta na tela avançada (o de modelo continua
  automático), porque o trade-off iGPU × NPU é do usuário.
- Aviso de primeira compilação em `_pipeline()`, com marcador
  `.compilado-<size>-<device>` no `CACHE_DIR`.

### Fase 4, item 1 — AEC v2 ✅

`cancel_echo` substituído pela versão em blocos. Medido dentro do app: **+7,2 dB**
de ERLE (era +3,2), custando 0,3 dB da voz. O comentário no código documenta o
teto (deriva de clock) para ninguém tentar alongar o filtro.

### Validação ponta a ponta ✅

`tools/test_e2e.py` no arquivo inteiro de 27/07 (5,8 min), pelo caminho real do
app (decode + diarização + AEC + anti-loop), com o config já migrado
(`model=large-v3-turbo`, `device=AUTO` → iGPU):

- **54 s para 345 s de áudio = 6,4× tempo real → 2 h em ~19 min.**
- texto: 4872 caracteres, compressão 2,26, pior repetição **3×** — passa.
- o aviso de primeira compilação apareceu na 1ª execução e sumiu na 2ª (o
  marcador `.compilado-` funciona).

⚠️ **Os ~19 min contradizem os ~12 min de § 1.3, e ambos estão certos** — medem
coisas diferentes. § 1.3 mede o decoder sobre **um** canal; com diarização ligada
o app transcreve **os dois** (mic e sistema), dobrando o trabalho. Sem diarização,
~10 min. Ao citar tempo, dizer sempre se é com ou sem diarização.

⚠️ **A diarização ainda erra por causa do eco** — visível na saída: `"Eu: É sempre
nove"` é fala do interlocutor, e a primeira resposta dele aparece *antes* da
pergunta. É exatamente o que a dominância de canal (Fase 4, item 2) conserta; o
AEC a 7 dB não chega lá.

### Documentação ✅

- `docs/ARMADILHAS.md` **criado** (o projeto não tinha) com 7 armadilhas.
- `CLAUDE.md` do projeto atualizado: transcrição, anti-loop, AEC, migração de
  config e ponteiro para as armadilhas.

### Pendente

- **Fase 3 (VAD)** — não executada. É otimização de velocidade e uma segunda
  camada contra alucinação; o ganho principal já veio do modelo + anti-loop.
- **Fase 4, itens 2-3** — dominância de canal na diarização e export de MP3
  limpo. A dominância de canal é o que de fato conserta "atribuir ao Gabriel a
  fala do interlocutor"; o AEC sozinho não chega lá (§ 1.5).

> Auditado em 2026-09-03 (revisão geral): esta seção está **desatualizada** e
> contradiz o § 8 deste mesmo arquivo — a Fase 3 (VAD) e a Fase 4 item 2
> (dominância de canal, `k_db=15`) foram entregues em
> [2026-07-29-melhoria-transcricao-ao-vivo-vad-diarizacao.md](2026-07-29-melhoria-transcricao-ao-vivo-vad-diarizacao.md)
> (§§ 8 e 8.1). Segue pendente só o item 3 (export de MP3 limpo). As duas
> decisões do § 7 também já foram respondidas lá (Dec1 iGPU, Dec2 rascunho +
> passada final).

---

## 7. Transcrição ao vivo — medido, e o resultado inverte a intuição

Pedido do Gabriel (29/07, mesma conversa): transcrever **durante** a gravação,
disparando ao detectar X ms de silêncio, com pontuação/interrogação corretas.

**Previsão teórica, que estava ERRADA:** "o encoder do Whisper sempre processa
30 s, então segmentar fino multiplica o custo; e trechos curtos perdem contexto,
então a pontuação piora". Medido (`temp/exp_ao_vivo.py`, trecho de 180 s de fala
real, `large-v3-turbo` @ iGPU):

| corte por silêncio | chamadas | compute | latência típica | `?` /100 palavras | WER vs. hoje |
| --- | --- | --- | --- | --- | --- |
| **hoje** (janelas cegas de 30 s) | 6 | 21,1 s | só no fim | 2,41 | — |
| 0,4 s | 31 (5,2×) | 22,6 s (1,1×) | ~2,8 s | 2,89 | 8,8% |
| **0,8 s** ← recomendado | 21 (3,5×) | **18,7 s (0,9×)** | **~3,2 s** | **2,91** | 7,7% |
| 1,5 s | 9 (1,5×) | 14,3 s (0,7×) | ~5,9 s | 2,07 | 9,9% |

**Por que o custo não explode:** o piso por chamada existe (é o encoder) mas não
domina. Medido isoladamente: 2 s → 0,37 s; 30 s → 0,62 s. Razão de 1,7×, não de
10×. E o VAD **deixa de gastar chamadas com silêncio**, que hoje são transcritas
à toa — o saldo fica neutro ou favorável.

**Por que a pontuação melhora:** o corte cego de 30 s parte frases no meio, e o
modelo pontua mal um fragmento sem começo nem fim. O VAD entrega **unidades de
fala completas**. Contexto não é só o que vem antes — é a frase estar inteira.

**Por que 1,5 s piora:** segmentos ficam grandes demais (média 18 s, **máx 76 s**)
e estouram a janela de 30 s do Whisper, que trunca (2201 chars contra 2362).
⚠️ **Requisito de implementação: partir à força qualquer segmento acima de ~28 s**,
sem esperar o silêncio.

**Consequência para a Fase 3:** o VAD estava classificado como otimização de
velocidade. Medido, é também **ganho de qualidade** — sobe de prioridade, e serve
aos dois modos (normal e ao vivo) com o mesmo código.

**Desenho proposto (não implementado):** um quarto thread, ao lado do
`_encode_loop`, consumindo uma fila — o laço de captura **não** pode ser tocado
(o `CLAUDE.md` já registra que trabalho no thread de captura atrasa o WASAPI e
estoura o buffer). Se a transcrição atrasar, atrasa sozinha.

**Duas decisões pendentes, que são do Gabriel:**

1. **Device.** O modo ao vivo ocupa o acelerador durante a reunião inteira — o
   cenário exato em que a iGPU disputa com o vídeo da chamada. Testar na NPU é
   obrigatório antes de fechar (ver § 1.4).
2. **Rascunho ou definitivo?** O texto ao vivo diverge do final em ~8% (WER).
   Recomendação: rascunho ao vivo + passada final ao parar. Mas é escolha de
   produto.

---

## 8. Fase 1 do roadmap de melhoria (VAD + contexto no modo lote) — implementada

Executada a Fase 1 de
[`2026-07-29-melhoria-transcricao-ao-vivo-vad-diarizacao.md`](2026-07-29-melhoria-transcricao-ao-vivo-vad-diarizacao.md):
`segmentar_por_vad`/`agrupar_segmentos` (portados de `tools/exp_contexto.py`) e
contexto via `initial_prompt` substituindo o laço de janelas cegas de 30 s em
`OVTranscriber._transcribe_channel`.

**Validação ponta a ponta** (`tools/test_e2e.py`, arquivo real de 27/07, 345 s,
diarização + AEC ligados), texto de ambos comparado no mesmo arquivo:

| | chars | `?`/100p | `,`/100p | tempo (par imediato) |
| --- | --- | --- | --- | --- |
| legado (janela cega 30 s) | 4872 | 2,56 | 9,87 | 57,9 s |
| **novo (VAD + agrupar 3 s + contexto)** | 4471 | **3,78** | **10,84** | **55,5 s** |

Pontuação melhora nos dois eixos, como o roadmap previa (D1/D5).

⚠️ **Correção (revisão por par, mesmo dia):** a linha "tempo (par imediato)"
acima **não sustenta** a conclusão de paridade que este documento chegou a
registrar. Rodando os dois códigos repetidas vezes na mesma sessão: legado
42/46/46 s (~45 s de média), novo 46/53/53/54 s (~52 s de média) — um par
isolado favorável ao novo (o legado rodou **depois**, numa sessão com deriva
térmica monotônica já documentada acima, o que enviesa a favor de quem roda
primeiro) não é evidência suficiente. **Correto dizer:** o caminho novo mede
**mais lento** que o legado nesta máquina para transcrição+diarização+AEC —
a Fase 1.3 do roadmap de melhoria não bateu a própria meta de velocidade
("tempo total não é maior que o de hoje"), mesmo com a pontuação melhor. Isso
segue registrado como aceito (o ganho de qualidade compensa, e a Fase 2 mediu
de onde vem boa parte do custo — ver § 8.1), mas sem o rótulo "paridade".

**Achado que não estava nas tabelas de `exp_contexto.py`:** aquele script nunca
chama `cancel_echo` — as tabelas de compute do § 7 acima e do roadmap de melhoria
(E3) são só transcrição, sem AEC. No pipeline real, `cancel_echo` **por grupo**
(não por span `ini:fim`, e não pré-limpando o canal inteiro em blocos de 30 s)
foi a variante mais barata testada: limpar o span inteiro do grupo (incluindo
silêncio interno) chegou a **6,4x tempo real** vs os **7,5-8,1x** do legado;
concatenar só a fala do grupo antes de cancelar o eco recuperou a paridade.
Pré-limpar o canal inteiro em blocos de 30 s antes do VAD (tentativa
intermediária) foi a **pior** opção — reintroduz o custo de rodar AEC sobre o
silêncio, que o VAD existe para evitar. Fica registrado para não repetir a
tentativa.

`ALVO_ACUMULO_S = 3.0` mantido como decidido em E3 (não foi preciso ajustar —
o gargalo era o AEC por span, não o alvo de acúmulo).

⚠️ **Correção:** a frase original desta seção atribuía o salto de 53s→68s
(medido ao ligar a dominância de canal, Fase 2) à correlação cruzada de
`_alinhar_canais` ("soma ~15-20% ao tempo"). Essa causa **não foi medida** —
uma correlação FFT sobre o canal inteiro é sub-segundo; o salto real é dentro
do ruído de 20%+ desta máquina já documentado acima (nenhuma repetição de
"legado logo antes" foi feita naquele ponto da sessão). Correto dizer: **esta
máquina não resolve diferenças de tempo abaixo de ~20% sem repetição
pareada**, não atribuir uma causa específica sem ter isolado a variável.

## 8.1 Fase 2 (dominância de canal) — dois bugs achados na revisão, corrigidos

Uma segunda revisão (consulta ao `advisor`, 29/07) achou dois problemas reais
no que tinha acabado de ser implementado e commitado — registrados aqui porque
os testes automáticos (`test_e2e.py`) **não detectam nenhum dos dois**: eles
checam "não é lixo" (repetição, compressão), não "não falta nada".

**Bug 1 — alinhamento global, não por bloco.** `dominancia_sistema` calculava
`_alinhar_canais` **uma vez** para o canal inteiro. `cancel_echo` reestima a
cada 2 s de propósito (ver ARMADILHAS "o teto do AEC é deriva de clock"):
medido agora no arquivo de 80 min (23/07), o atraso ótimo entre mic e sistema
vai de **48 ms a 155 ms** e **inverte de sinal** perto dos 60 min. Um
alinhamento só, feito no início, comparava mic no instante *t* com sistema em
*t±0,5s* na maior parte de um arquivo de 2h — invertendo decisões de
dominância no início e no fim de cada fala. **Corrigido:** `dominancia_sistema`
agora reestima o alinhamento a cada 30 s (parâmetro `realinhar_s`), como
`cancel_echo` já fazia — custo baixo, é só uma correlação cruzada por bloco,
não o STFT+solve do AEC.

**Bug 2 — `k_db` calibrado contra a população errada, apagou fala real.** A
calibração original (`tools/calibrar_dominancia.py`) só media falso-positivo
contra blocos "só o mic fala" (sistema mudo) — nessa população a dominância
quase não dispara por construção, então FP≤2% não protegia do risco real, que
o próprio roadmap de melhoria nomeia: "dominância corta fala legítima em
double-talk". Rodando o A/B (legado × novo) numa gravação real de 20 min
(28/07), com `k_db=12` (o valor calibrado antes), **duas falas reais do
Gabriel desapareceram inteiras**: `"Onde é que tá esse manual?"` e `"eu estou
vendo isso..."` — ausentes no texto novo, presentes no legado e presentes com
Fase 1 sozinha (sem dominância). Isso é **pior** que o defeito original (D3 era
atribuição errada de locutor; isso é perda de conteúdo).
**Corrigido:** o script agora também mede o flag em blocos "ambos" (double-talk,
sem rótulo verdadeiro, mas onde o corte pode estar apagando fala real) e exige
`≤ 10%` além do `FP_MAX` de antes. `K_DB_DOMINANCIA` subiu de 12 para **15** —
confirmado por transcrição real (não só pela métrica de bloco) que as duas
falas voltam. Recall de eco cai de 34,9% para 24,7% — aceito, porque "na
dúvida, manter o segmento" (§ Riscos) pesa mais que pegar todo eco.
**Lição para not repetir:** métricas de bloco (recall/FP) são um proxy; a
prova real é diff de transcrição pareada num arquivo onde o mecanismo dispara
de verdade — o arquivo de validação original (27/07) mal acionava a
dominância (só 3,7% do canal era "só sistema"), o que escondeu os dois bugs.

---

## 5. Regra da casa que vale aqui

`CLAUDE.md` do projeto: **toda mudança em `reco.py` exige recompilar** com
`powershell -ExecutionPolicy Bypass -File "C:\Dev\Reco\build.ps1"`. O executável é
frozen; sem rebuild a mudança não chega ao app que o Gabriel usa.

> Auditado em 2026-09-01 (overhaul): FECHADO — Fases 1, 2, 4.1 executadas 29/07; Fase 3 e 4.2 saíram pelo md de VAD/diarização; iGPU×NPU virou card c6754eb610d70
