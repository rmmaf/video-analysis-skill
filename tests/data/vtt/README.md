# VTT snippets for the parser tests

These are short excerpts of the user's real subtitles. **They stay on this machine only**: do not publish, share or commit them anywhere else.

The real snippets are byte-exact. Each one is the source's header bytes (`WEBVTT` plus the blank line) followed by one contiguous cue range of the source, ending with the blank line that follows the last cue. Line endings (LF or CRLF) are kept, and none of the real sources has a BOM. `manifest.json` holds the same list in machine-readable form, with byte sizes, timing-line counts, newline and BOM status.

Cue numbers are 1-based and inclusive, and refer to the **source** file.

| Snippet | Source | Cues | What it covers |
|---|---|---|---|
| `whisperx_criando_pr.vtt` | `C:/Users/digom/Documents/solar/databricks  schema/docs-solar/02. Conteúdos/01.Interno/Datalake/Criando sua primeira PR.vtt` | 1–25 of 50 | WhisperX `SPEAKER_00:` / `SPEAKER_01:` prefixes; sentences split across speakers ("Quando eu" / "abrir aqui…"); LF |
| `whisperx_falante_da1608.vtt` | `C:/Users/digom/Documents/solar/databricks  schema/docs-solar/01.Arquitetura/3.DA/Repasse - Fluxo de Carga dos DA’s-20260818_160841-Gravação de Reunião.vtt` | 50–63 of 155 | the only `FALANTE_?:` cue on disk (cue 58); 4 speakers; a 26 s cue (61). The source name is NFD and contains U+2019 |
| `teams_dias_estoque.vtt` | `C:/Users/digom/Documents/solar/PES/pes_arquivos/dias_estoque.vtt` | 1–25 of 156 | Teams: CRLF; `uuid/N-k` cue ids (utterance parts `/9-0`, `/9-1`); `<v Name>` spanning 2 lines; 5 overlaps between different speakers (cues 1/2, 10/11, 12/13, 18/19, 23/24) |
| `teams_scanntech.vtt` | `C:/Users/digom/Downloads/call_promocoes_scanntech.vtt` | 1–25 of 133 | Teams-like: CRLF; `<v Name>` without cue ids; numeric entities (`&#233;`, `&#231;&#245;`, `&#39;`, `&#201;`); overlaps between speakers |
| `plain_venda_zero_clientes.vtt` | `C:/Users/digom/Downloads/venda_zero_clientes.vtt` | 1–25 of 98 | plain: no speakers, back-to-back cues; LF |
| `nohours_call_promocoes_scantech.vtt` | `C:/Users/digom/Downloads/call_promocoes_scantech.vtt` | 1–25 of 220 | `MM:SS.mmm` timings (no hours) |
| `hourswitch_treinamento_playbook.vtt` | `C:/Users/digom/Downloads/treinamento_playbook.vtt` | 962–977 of 1,306 | `59:59.040 --> 59:59.460`, then `01:00:00.460 --> 01:00:00.740` (cue 970): the format switches after 1 h |
| `stutter_mix_digital.vtt` | `C:/Users/digom/Downloads/mix_digital.vtt` | 94–128 of 145 | cues 97–126: 30 consecutive cues of 20–100 ms with the same sentence (17:26.240–17:27.780), which should merge into one; cue 128: a 27.2 s, 935-char repetitive cue (long-cue split) |
| `long_mix_digital.vtt` | `C:/Users/digom/Downloads/mix_digital.vtt` | 59–61 of 145 | cue 60: a 223.7 s, 2,820-char Whisper merge, the longest on disk |
| `long_nonmono_pes_pedido_sugerido.vtt` | `C:/Users/digom/Downloads/pes_pedido_sugerido.vtt` | 1–16 of 99 | cue 1: 125.88 s, 1,684 chars; cue 12: end < start (`02:29.020 --> 02:27.420`); cue 13 starts before cue 12 (non-monotonic) |
| `rolling_red_aderencia.vtt` | `C:/Users/digom/Videos/red_aderencia.vtt` | 1–25 of 826 | rolling caption: cue 1 "A rede de aderência" grows into cue 2 "A rede de aderência pesquisa"; cue 12: 175 chars in 0.82 s (too fast); cue 13: 0.02 s; cue 14: 84 chars in 0.72 s |
| `edge_cases.vtt` | synthetic | 21 timing lines | see the next section |
| `cp1252_accented.vtt` | synthetic: cues 1–6 of `whisperx_criando_pr.vtt` re-encoded as cp1252 | 6 | not valid UTF-8, so the reader must fall back to cp1252 with a warning; the decoded text equals the UTF-8 snippet |

Notes on the sources:

- `red_aderencia` lives in `C:/Users/digom/Videos`, not in Documents or Downloads. Its fastest long cue is 175 chars in 0.82 s; the spec says 220 chars.
- `dias_estoque.vtt` is byte-identical to `C:/Users/digom/Downloads/Indicadores Supply - PES (Entendimento de indicadores).vtt`.

## `edge_cases.vtt`: what a spec-conforming parser should produce

The file starts with a UTF-8 BOM and uses LF line endings. The last three cues switch line endings: the first has CRLF, the other two have bare CR. The header line is `WEBVTT - casos de borda (sintético)`, followed by `Language: pt-BR` and `Kind: captions`.

NOTE, STYLE and REGION blocks are skipped. One NOTE block sits between cues.

The table applies the rules of `docs/design-deterministico.md` section 4 in order: read, blocks, payload, times, order, merges, repairs. It is guidance for the parser tests; the parser package owns the final semantics.

| # | Timing line in the file | Expected cue | Rule exercised |
|---|---|---|---|
| 1 | `00:00:01.000 --> 00:00:03.000 align:start …` (id `1`) | 1.000–3.000 "Configuração com itálico e negrito" | cue settings ignored; `<c.amarelo>`, `<i>`, `<b>` stripped |
| 2 | `00:00:03.000 --> 00:00:05.000` (id `2`) | 3.000–5.000 "Karaokê palavra por palavra" | karaoke timestamp tags stripped |
| 3 | `00:00:05.000 --> 00:00:07.000` (id `voz-com-classe`) | 5.000–7.000 speaker "Maria Silva": "Voz com classe & entidade <tag>" | `<v.alto Name>`; entities decoded after tag stripping |
| 4 | `00:00:07.000 --> 00:00:09.000` | 7.000–9.000 "Espaço duro e marca invisívelzero" | `&nbsp;` becomes a space; `&lrm;` and `&#8203;` are removed |
| 5 | `00:00:10.000 --> 00:00:10.000` | 10.000–10.800 "Duração zero", approx | zero length becomes 0.1 s; then too fast (12 chars in 0.1 s), so end = min(next start, start + 12/15) |
| 6 | `00:00:13.000 --> 00:00:12.000` | 13.000–13.500 "Fim antes" | end < start gives end = start + 0.5 s, with a warning |
| 7–8 | `00:00:14.000 --> 00:00:16.000` + `00:00:16.000 --> 00:00:18.000` in one block | 14.000–16.000 "Primeira sem linha em branco"; 16.000–18.000 "Segunda colada na anterior" | missing blank line: split at the second timing line |
| 9 | `00:00:18,500 --> 00:00:20,5` | 18.500–20.500 "Vírgula e fração curta" | `,` separator; fraction padded to the right |
| 10 | `00:21.000 --> 00:23.000` | 21.000–23.000 "Sem horas" | hours optional, per cue |
| 11 | `00:00:23.000-->00:00:25.000` | 23.000–25.000 speaker `SPEAKER_02`: "Falante WhisperX" | no spaces around `-->`; WhisperX prefix |
| 12 | `00:00:25.000 --> 00:00:27.000` | 25.000–27.000 speaker `FALANTE_?` (tag `S?`): "Falante desconhecido" | `FALANTE_?` |
| 13 | `00:00:27.000 --> 00:00:29.000` (id `fb880527-…/18-1`) | 27.000–29.000 no speaker: "A pergunta é: isto não é falante"; utt = 18 | no generic `Name:` rule; Teams-style id |
| 14 | `00:00:29.000 --> 00:00:31.000` | two cues, both 29.000–31.000: "Ana": "Primeira voz" and "Bruno": "Segunda voz" | several voices in one cue split into sub-cues |
| 15 | `00:00:31.000 --> 00:00:33.000` | 31.000–33.000 "漢kan rubi" | ruby tags stripped |
| 16 | `00:00:33.000 --> 00:00:35.000` | dropped (counted as empty) | payload `<i></i>` is empty after stripping |
| 17 | `00:00:35.000 --> 00:00:36.000` | dropped (counted as empty) | whitespace-only payload line ends the block |
| 18 | `00:01:61.000 --> 00:01:62.000` | skipped, with a warning | seconds ≥ 60 |
| 19 | `00:00:37.000 --> 00:00:39.000` (CRLF) | 37.000–39.000 "Linha CRLF" | CRLF normalized |
| 20–21 | `00:00:39.000 --> 00:00:41.000`, `00:00:41.000 --> 00:00:43.000` (CR only) | 39.000–41.000 "Linha só CR um"; 41.000–43.000 "Linha só CR dois" | bare CR normalized |

The expected result is 19 cues. Speaker tags follow first appearance: S1 is Maria Silva, S2 is SPEAKER_02, S? is FALANTE_?, S3 is Ana and S4 is Bruno. The style is `mixed`. No merge applies: the cues next to each other differ in speaker or text.
