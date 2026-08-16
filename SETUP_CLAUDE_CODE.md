# SETUP — Claude Code na sua máquina

Roteiro de instalação. 20 minutos, uma vez só.

---

## 1. Git

Você tem o **GitHub Desktop** instalado e logado — ele já traz o Git embutido, mas nem sempre
no PATH do terminal. Teste no PowerShell:

```powershell
git --version
```

Se der erro, instale o Git for Windows: https://git-scm.com/download/win
(aceite os padrões; a opção "Git from the command line" precisa estar marcada)

Depois configure quem você é:

```powershell
git config --global user.name "Kevin Ribeiro dos Santos"
git config --global user.email "kevin.ribeirodosantos@gmail.com"
```

## 2. Clonar o repositório

Pelo GitHub Desktop é mais simples: **File → Clone repository → kevinromaoribeiro/resolve-ai**.
Ele já resolve a autenticação, então `git push` funciona sem token.

Anote a pasta onde ele clonou (algo como `C:\Users\kevinsantos\Documents\GitHub\resolve-ai`).

## 3. Instalar o Claude Code

Precisa de Node.js: https://nodejs.org (versão LTS). Depois:

```powershell
npm install -g @anthropic-ai/claude-code
```

Abra o terminal **dentro da pasta do repositório** e rode:

```powershell
claude
```

Na primeira vez ele pede pra você fazer login na conta Anthropic. É a mesma conta.

## 4. Colocar o CLAUDE.md no lugar

Copie o `CLAUDE.md` que eu gerei para a **raiz do repositório** (mesma pasta do `wa_bot.py`).
Ele carrega sozinho em toda sessão — é o que faz o Claude Code já começar sabendo de tudo.

Vale commitar junto com o código. Assim a memória do projeto vive no repositório.

## 5. Conectores (MCPs)

Boa parte do que eu uso aqui o Claude Code **já tem nativo, e melhor**:

| O que eu uso aqui | No Claude Code |
|---|---|
| Desktop Commander (rodar comandos) | nativo (Bash) |
| Ler/escrever arquivo via navegador | nativo (Read/Write/Edit) |
| Colar código no editor do GitHub | `git commit` + `git push` |
| Conferir hash contra o repo | `git diff` / `git status` |
| Pyodide no navegador pra rodar Python | `python` de verdade + `pytest` |
| Subagente auditor | nativo (Task) |

Ou seja: **a maior parte do meu encanamento simplesmente deixa de existir.**

O que vale adicionar:

**GitHub** — pra ler issues, criar PR, ver Actions:

```powershell
claude mcp add --transport http github https://api.githubcopilot.com/mcp/
```

Na primeira chamada ele abre o fluxo de autorização no navegador. Você aprova ali —
não precisa colar token em lugar nenhum, e nunca me passe token por mensagem.

**Google Drive** — se quiser continuar salvando as versões e documentos lá:
adicione pelo painel de conectores da sua conta Anthropic, aí aparece no Claude Code também.

**Chrome** — só se for mexer no EasyPanel ou no painel pelo navegador. Para o dia a dia
de código, não precisa.

## 6. Deploy daqui pra frente

O EasyPanel não tem auto-deploy: o build acontece **no clique do Implantar**.
Então o ciclo é:

```powershell
git add -A
git commit -m "v23.4 ..."
git push
```

E depois: EasyPanel → projeto `resolveai` → serviço `bot` → **Implantar**.

Confirme sempre no final:

```powershell
curl https://resolveai-bot.jrtrsg.easypanel.host/health
```

O campo `build` tem que ser o novo. Se vier o antigo, subiu imagem velha — implante de novo.

**Rollback**, se algo quebrar:

```powershell
git checkout <commit-anterior> -- wa_bot.py
git commit -m "rollback wa_bot.py"
git push
```

E Implantar. Isso já salvou a produção em 3 minutos uma vez.

## 7. Primeira mensagem

Cole o conteúdo de `PROMPT_INICIAL_CLAUDE_CODE.md`.

---

## Uma dica que muda o custo

Dentro do Claude Code, use `/clear` entre tarefas diferentes. O histórico inteiro é reenviado
a cada mensagem — conversa longa fica cara mesmo quando o assunto já mudou. Terminou a FASE 1,
`/clear`, começa a FASE 2. O `CLAUDE.md` recarrega sozinho, então você não perde contexto do
projeto, só o bate-papo acumulado.
