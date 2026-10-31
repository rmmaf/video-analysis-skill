# `/video-analysis setup`: preparar a máquina

Este é o roteiro do comando `setup`. **Nesta chamada não se analisa vídeo.** O objetivo é o
`verificar_ambiente.py` sair com código 0 ("Pronto."). O setup pode rodar quantas vezes for preciso: o
que já está pronto sai `[OK]` e é pulado.

**O que o código 0 quer dizer:** Python 3.10 ou mais novo, `ffmpeg` e `ffprobe` no PATH, numpy e Pillow
instalados, a pasta central `C:\Users\digom\video-analysis\` aceita gravação, e as regras de permissão
cobrem essa pasta. Com isso os agentes do Workflow leem frames e gravam relatórios sem pedir permissão a
cada arquivo.

**Os agentes e o workflow instalados nunca saem `[FALTA]`, só `[AVISO]`.** Sem as cópias em
`~/.claude/agents/`, ou com cópias diferentes das da skill, a análise roda no modo `inline`, que funciona
igual e gasta um pouco mais por agente. Sem o workflow salvo em `~/.claude/workflows/`, ou com uma cópia
diferente da da skill, a skill passa o script inteiro ao Workflow a cada run, o que custa ~13k tokens a
mais.

O que só se confirma no uso:

- a primeira análise prova as regras de permissão: nenhum pedido de permissão durante o run. Comece por
  uma curta, com `--max-lotes 1` ou `2`;
- a primeira análise numa sessão aberta depois do setup prova o modo `agents` e o workflow chamado pelo
  nome (`name: "video-analysis"`).

## O que o setup não pede

A skill não usa **nenhum segredo** nem login: trabalha com arquivos locais, o `ffmpeg` e o Python. Fora
da pasta central, o setup grava em só três lugares, e só depois do "sim" da pessoa:

| o quê | onde | como desfazer |
|---|---|---|
| os dois papéis dos agentes | `C:\Users\digom\.claude\agents\video-analysis-*.md` | apagar os dois arquivos |
| o workflow salvo | `C:\Users\digom\.claude\workflows\video-analysis.js` | apagar o arquivo |
| as regras de permissão da pasta central | `C:\Users\digom\.claude\settings.json` | restaurar o `settings.json.bak-<data>` |

Você também não aceita termos nem faz instalação que peça elevação em nome da pessoa.

## Como conduzir

1. **Veja primeiro se há Python e ffmpeg**, só lendo, porque o verificador é um script Python. Em
   PowerShell:
   `Get-Command python, py, ffmpeg, ffprobe, winget -ErrorAction SilentlyContinue | Select-Object Name, Source`.
   Um `python` em `...\WindowsApps\` só conta se `python --version` mostrar a versão: sem o Python da
   Microsoft Store instalado, ele é só o atalho que abre a loja. Sem Python, comece pelo passo 1.
2. **Rode o verificador completo** e mostre o resultado à pessoa. Use o comando da seção **Setup** do
   `SKILL.md`, que já vem com o caminho da skill resolvido e casa com a pré-aprovação; rode pela
   ferramenta Bash:

   ```bash
   python "<pasta da skill>/verificar_ambiente.py"
   ```

   - `<pasta da skill>` é o `${CLAUDE_SKILL_DIR}` do `SKILL.md` (o *base directory* informado quando a
     skill carrega). O verificador fica na raiz da skill, não em `scripts\`.
   - Com `--json`, os itens saem em formato de máquina.
   - A saída marca cada item com `[OK]`, `[FALTA]` ou `[AVISO]`, e cada `[FALTA]`/`[AVISO]` aponta o passo
     deste roteiro que o resolve. Saída 0 = "Pronto." (aviso não conta); 1 = falta alguma coisa.
   - `--home` e `--claude-home` (ou a variável `VA_CLAUDE_HOME`) apontam outra pasta central ou outra
     pasta do Claude Code; servem para teste, não para o setup normal.
3. **Resolva os `[FALTA]` na ordem dos passos, um de cada vez.** A ordem segue as dependências: cada
   passo só precisa do que os anteriores deixaram pronto. Os passos 4 e 5 valem mesmo com o resto `[OK]`.
4. **Cada ação traz uma de duas marcas:**
   - **você executa**: mostre o comando, diga em uma frase o que ele muda na máquina e rode só depois do
     "sim" da pessoa;
   - **a pessoa faz**: entregue o passo a passo, espere ela dizer que terminou e rode o verificador de
     novo.
5. **Um item vale como feito quando o verificador mostra `[OK]`**, não quando a pessoa diz que fez.
6. **Termine** quando o verificador sair com código 0. Não declare o setup pronto com código 1.
7. **Se o setup começou no meio de uma análise** (o `prepare.py` saiu com 3, ou acusou
   `permissions_ok: false`), ofereça retomá-la quando terminar.

## Passo 1 — ferramentas: Python e ffmpeg

**A pessoa faz, no terminal dela.** Na primeira execução, o `winget` pode pedir o aceite dos termos da
fonte, e o Windows pode pedir elevação (UAC). São aceites que só ela dá. Entregue só os comandos do que
saiu ausente:

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Gyan.FFmpeg -e
```

- Qualquer Python 3.10 ou mais novo serve; quem já tem um não precisa trocar.
- O pacote `Gyan.FFmpeg` traz o `ffmpeg` e o `ffprobe` juntos.
- Sem `winget`, use os instaladores oficiais: <https://www.python.org/downloads/> e
  <https://www.gyan.dev/ffmpeg/builds/> (acrescentando a pasta `bin` ao PATH).

**Depois de instalar**, a pessoa fecha e abre o Claude Code e chama `/video-analysis setup` de novo. O
PATH só é relido por processo novo: `[FALTA]` logo depois da instalação costuma ser isso, e não falha da
instalação.

## Passo 2 — bibliotecas: numpy e Pillow

**Você executa**, depois do "sim":

```bash
python -m pip install numpy Pillow
```

Se o pip recusar por permissão, repita com `--user`. A skill não usa OpenCV nem outra dependência.

## Passo 3 — pasta central

A pasta `C:\Users\digom\video-analysis\` e as subpastas (`cache`, `runs`, `pedidos`, `roles`, `logs`) são
criadas no primeiro uso. As regras de permissão e o `pedido.md` apontam para ela: não a troque de lugar.

Se o item sair `[FALTA]`, a pasta existe mas não aceita gravação. **A pessoa faz:** confere se há um
arquivo, e não uma pasta, com esse nome; se o disco tem espaço; e se um antivírus bloqueia a gravação.
Depois, rode o verificador de novo.

## Passo 4 — agentes: instalar os papéis e o workflow

**Você executa**, depois do "sim":

```bash
python "<pasta da skill>/verificar_ambiente.py" --instalar-agentes
```

- Cria `C:\Users\digom\.claude\agents\`, se não existir, e copia para lá `video-analysis-curator.md` e
  `video-analysis-analyst.md`, da pasta `agents\` da skill.
- Cria `C:\Users\digom\.claude\workflows\`, se não existir, e copia para lá `video-analysis.js`, da pasta
  `workflow\` da skill. É o workflow salvo que o passo 6 do `SKILL.md` chama pelo nome, sem passar o
  script inline.
- Se o workflow mudou, o verificador avisa (`[AVISO]`): esta sessão pode manter na memória a versão
  anterior, então nesta conversa o passo 6 do `SKILL.md` não o chama pelo nome; numa conversa nova, sim.

Rode de novo sempre que a skill for atualizada: o verificador avisa quando as cópias instaladas diferem
das da skill. Enquanto isso, o `prepare.py` usa o modo `inline` e acusa `env.workflow_current: false`,
e a skill passa o script inline ao Workflow em vez de chamar pelo nome a cópia antiga.

## Passo 5 — permissões: ler e gravar na pasta central sem perguntar

Sem estas regras, cada frame que um agente abre e cada relatório que ele grava pede permissão: centenas
de pedidos por run.

1. **Mostre o trecho à pessoa** antes de gravar:

   ```json
   "permissions": { "allow": [
     "Read(~/video-analysis/**)", "Edit(~/video-analysis/**)", "Write(~/video-analysis/**)",
     "Read(//c/Users/digom/video-analysis/**)", "Edit(//c/Users/digom/video-analysis/**)", "Write(//c/Users/digom/video-analysis/**)"
   ]}
   ```

   - As regras valem só para a pasta central: nada fora dela fica liberado.
   - As duas grafias (`~/…` e `//c/…`) ficam até a primeira análise mostrar qual o Windows aceita; depois,
     a que não funcionar pode sair.
2. **Você executa**, só depois do "sim":

   ```bash
   python "<pasta da skill>/verificar_ambiente.py" --aplicar-permissoes
   ```

   O verificador faz backup em `settings.json.bak-<data>` e mescla as regras no `settings.json`, sem
   mexer em `enabledPlugins` nem no resto.

## Ao terminar

Rode o verificador uma última vez e mostre o resultado. Lembre à pessoa:

- **Agente novo em `~/.claude/agents` só é carregado quando o Claude Code abre.** Depois de instalar,
  feche e abra o Claude Code; até lá a skill roda no modo `inline`, que funciona igual, gastando um pouco
  mais por agente.
- O workflow salvo em `~/.claude/workflows` pode precisar do mesmo: se o Workflow disser que não conhece
  o nome `video-analysis`, feche e abra o Claude Code. Até lá a skill passa o script inline, que funciona
  igual e custa ~13k tokens a mais por run.
- Se os pedidos de permissão continuarem depois de gravar as regras, feche e abra o Claude Code.
- Se havia uma análise antes do setup, ofereça retomá-la.
