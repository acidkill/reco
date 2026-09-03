# Reco — duração de MP3 inflada + salvamento instantâneo (28/07/2026)

## O pedido

> "tem um bug onde a duração dos arquivos estão inflados
> `C:\Users\Gabriel dos Anjos\Documents\Reco`; inclusive, abrindo pelo VLC, o tempo
> 'restante pra acabar' fica 'pulando' em diferentes valores ao invés de diminuir
> apenas 1s a cada segundo. (…) se necessário, poderíamos ver uma nova forma de
> gravar ou codificar, pra ser tão instantâneo quanto o OBS."

São **dois** problemas independentes, com uma causa cada.

## Diagnóstico 1 — duração inflada: MP3 VBR **sem header Xing**

`write_mp3` (reco.py:803) encoda em VBR (`enc.set_vbr(4)` = `vbr_mtrh`) e escreve
apenas `encode() + flush()`. O python-`lameenc` **não expõe**
`lame_get_lametag_frame` — confira: a API só tem
`encode/flush/set_*` (nenhum `get_lametag_frame`). Resultado: o arquivo sai sem o
frame `Xing`/`Info` que declara a contagem total de frames.

Medido no arquivo real `gravacao_reco_2026-07-28_15-41-38.mp3`:

| medida | valor |
| --- | --- |
| primeiro byte | `ff f3 18 64` — frame de áudio direto, sem ID3, **sem Xing/Info/VBRI** |
| bitrate do 1º frame | índice 1 = **8 kbps** (silêncio do começo da gravação) |
| duração declarada | **10.172 s (2h49)** |
| duração real (33.589 frames × 576 amostras ÷ 16 kHz) | **1.209 s (20min09)** |

Sem o header, todo player estima `duração ≈ tamanho ÷ bitrate dos primeiros frames`.
Como a gravação começa em silêncio (8 kbps) e depois sobe para ~90 kbps, a
estimativa infla **~8×** — e o VLC, que re-estima conforme lê frames de bitrates
diferentes, mostra o "tempo restante" pulando. É exatamente o sintoma relatado.

## Diagnóstico 2 — salvar demora: encoda tudo só no `stop()`

`DualRecorder` acumula **todo** o áudio em memória (`_mic_chunks`/`_sys_chunks`,
float32 a 48 kHz) e só no `stop()` faz resample + ganho + encode do arquivo inteiro
(`_save`, reco.py:1088). Medido nesta máquina, para 20 min de gravação:

| etapa | tempo |
| --- | --- |
| `resample_poly` 48k→16k (2 canais) | 1,4 s |
| `lameenc` VBR (estéreo 16 kHz) | 9,4 s |
| **total** | **~11 s** — e cresce linearmente: ~50 s numa reunião de 1h30 |

O OBS parece instantâneo porque encoda **durante** a captura; ao parar, só restam
o flush e o fechamento do arquivo. Bônus da abordagem atual que também some: 2h de
gravação hoje ocupam ~2,8 GB de RAM (48 kHz float32, dois canais).

## Decisão: encoder incremental via PyAV (libmp3lame), ABR 96 kbps

`av` já é dependência do projeto (decodificação/`extract_mp3`) e vem bundlada no
executável. Usá-la como **encoder** resolve os dois problemas de uma vez:

1. o muxer mp3 do ffmpeg escreve o **header Xing** no `close()` → duração exata;
2. `av.AudioResampler` é **stateful**, então dá para resamplear bloco a bloco sem
   descontinuidade nas bordas (o que `resample_poly` por bloco não permite);
3. encodando ao longo da gravação, `stop()` vira flush + close (milissegundos).

### Por que ABR 96k e não `-q:a` (VBR por qualidade)

Testado sobre 300 s do áudio real do Gabriel (fala + áudio de sistema), medindo
bitrate e energia por banda no canal do mic:

| encoder | tamanho | bitrate | 0-1k | 1-2k | 2-4k | 4-6k | 6-8k |
| --- | --- | --- | --- | --- | --- | --- | --- |
| original (lameenc VBR, hoje) | 3,47 MB | 92 kbps | -7,0 | -11,8 | -19,5 | -31,0 | -34,9 |
| PyAV `global_quality` q4 | 1,09 MB | 29 kbps | -6,9 | -11,7 | -19,5 | **-45,8** | **-71,9** |
| PyAV ABR 80k | 3,06 MB | 81 kbps | -7,4 | -12,2 | -19,9 | -31,3 | -35,2 |
| **PyAV ABR 96k** | **3,56 MB** | **95 kbps** | -7,4 | -12,2 | -19,9 | -31,3 | -35,3 |

O caminho `global_quality`/qscale via PyAV aplica um lowpass agressivo (corta tudo
acima de ~4 kHz: −46 dB e −72 dB nas duas últimas bandas) — inaceitável para voz e
ruim para o Whisper. **ABR 96k reproduz o bitrate e o espectro atuais**, então a
mudança não altera qualidade nem tamanho percebidos.

⚠️ Nota sobre `MP3_BR`: o valor **128** de hoje era inerte — `lame_set_VBR_mean_bitrate_kbps`
só vale no modo `vbr_abr`, e o projeto usa `vbr_mtrh`. Medido: com e sem o
`set_vbr_mean_bitrate_kbps(128)` o arquivo sai idêntico (3,47 MB). O bitrate real
sempre foi ~92 kbps. Por isso a constante passa a **96** — que agora *de fato*
significa alguma coisa (ABR real).

## ✅ EXECUTADO — conferido 21/08/2026 (evidência por grep em `reco.py`/`tools/`)

## Passos

1. [x] **`MP3Writer`** (nova classe em `reco.py`, perto de `write_mp3`): abre o
   container, recebe blocos float32 dos dois canais em 48 kHz, aplica ganho, faz o
   resample stateful, encoda e muxa. `close()` fecha o container (Xing escrito);
   `discard()` fecha e apaga o arquivo. Critério de pronto: teste isolado gerando
   N segundos em blocos de 1024 amostras produz arquivo cuja duração declarada bate
   com a real (tolerância < 0,1 s) e cujos canais L/R continuam alinhados.
   ✅ `reco.py:704` `class MP3Writer`, `reco.py:758` `def close`, `reco.py:772`
   `def discard`; `tools/test_encoder.py` existe e é listado no `CLAUDE.md` como
   o teste a rodar "sempre que mexer em `MP3Writer`/`_pump`" — cobre exatamente
   o critério (duração declarada == real, header Xing, L/R separados).
2. [x] **`DualRecorder` streaming**: thread encoder consome `_mic_chunks`/`_sys_chunks`
   em lockstep (`min(len(mic), len(sys))`, guardando o resto), canal que falhou vira
   silêncio, arquivo nomeado no `start()`. `stop()` = drena + `close()`. `abort()` =
   `discard()`. Critério de pronto: `stop()` de uma gravação de 10 min retorna em
   < 0,5 s.
   ✅ `reco.py:1428` `class DualRecorder`, `reco.py:1560`
   `self._enc_thread = threading.Thread(target=self._encode_loop, daemon=True)`,
   `reco.py:1707` `def _encode_loop`; `CLAUDE.md` § encode em streaming documenta
   `stop()` em "~450 ms, contra ~11 s para 20 min de gravação antes" — dentro do
   critério de < 0,5 s.
3. [x] **Fallback sem `av`** — **DESCARTADO** (ver a nota abaixo; marcado em
   03/09/2026 para o passo parar de contar como trabalho aberto): mantém o
   caminho em memória, mas com `write_mp3(vbr=False)`
   (CBR). Sem Xing, só o CBR dá duração exata — é a correção de uma linha para um
   caminho que praticamente nunca roda (o exe traz `av`).
   ⚠️ **Não implementado — descartado na prática, não corrigido.** Grep por
   `lameenc`/`write_mp3`/`import av` em `reco.py` mostra `import av` sem
   `try/except` em `_open_mp3`/`MP3Writer.__init__` (linhas 595, 695, 718, 733,
   798) — não há caminho sem `av`. Consistente com o `CLAUDE.md` § MP3 sempre
   por container: "Não voltar para `lameenc` (removido em 28/07)". `av` virou
   dependência dura (o exe sempre traz), então o fallback nunca foi construído.
4. [x] **Reparar os arquivos já gravados** em `Documents\Reco`: remux `ffmpeg -c copy`
   (lossless, sem re-encode) para inserir o header. Requer autorização — mexe em
   arquivos do usuário.
   ✅ `tools/reparar_duracao.py` existe; `CLAUDE.md` § MP3 sempre por container
   confirma: "Rodado em 28/07/2026 nos 16 arquivos de `Documents\Reco`."
5. [x] **Recompilar** (`build.ps1`, regra do projeto) + documentar.
   ✅ `build.ps1` existe na raiz; documentação extensa em `CLAUDE.md` (§ MP3
   sempre por container, § encode em streaming) e o recurso está em produção
   sem regressão relatada.

## Efeitos colaterais aceitos (e o porquê)

- **Ganho por canal deixa de ser retroativo.** Hoje o multiplicador é lido no
  `_save` e aplicado ao arquivo inteiro; encodando ao vivo, ele passa a valer do
  momento do ajuste em diante — que é como um mixer se comporta, e é o que o VU
  meter já mostrava. Não há alternativa com encode incremental.
- **O timestamp no nome do arquivo passa a ser o do início da gravação** (hoje é o
  do fim, porque o arquivo só nascia no `stop()`). Melhor para achar a reunião
  depois.
- **O arquivo passa a existir durante a gravação.** Se o app morrer no meio, resta
  um MP3 parcial válido em vez de nada.

> Auditado em 2026-09-01 (overhaul): FECHADO — MP3Writer/_open_mp3 em produção (CLAUDE.md § MP3 por container); § EXECUTADO conferido 21/08
> Auditado em 2026-09-03 (revisão geral): o único `[ ]` que restava era o passo 3, **descartado na prática** desde 28/07 (`av` virou dependência dura) — marcado como descartado, sem mudança de código; o md deixa de aparecer como Ativo no índice.
