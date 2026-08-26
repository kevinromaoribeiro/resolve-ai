# DECISÕES

Uma linha por escolha relevante: o que foi decidido e por quê. Ordem cronológica.
Se uma decisão for revertida, não apague — acrescente a reversão com a data.

## Motor 2 — M2.3 (heatmap de constância)

- **17/08/2026 — Dia sem uso é ZERO na série, nunca ausência.** Uma série que só traz os dias com atividade desenha dez usos esparsos como dez quadrados seguidos — o heatmap passa a mentir exatamente sobre a única coisa que ele existe pra mostrar. Por isso a série é construída a partir do calendário, não do resultado da consulta.
- **17/08/2026 — A média é por dia ATIVO, não pela janela.** Dividir por 90 dilui e esconde quem usa muito em poucos dias, que é justamente o perfil que precisa ser distinguido de quem usa pouco todo dia.
- **17/08/2026 — SVG inline, sem biblioteca.** Nada de CDN no painel: uma dependência externa no dash é mais um jeito de a tela quebrar quando o site de terceiro cai — e o painel é o que o Kevin abre pra saber se o resto está de pé.
- **17/08/2026 — O que é conta saiu do handler async (`_dados_do_painel`).** Campo montado dentro da rota é campo que ninguém testa sem subir servidor; a rota agora só serializa.

## Motor 2 — M2.2 (lembretes dinâmicos)

- **17/08/2026 — IPVA e licenciamento vêm de TABELA versionada no repo, não de API.** Não é preguiça: esse calendário não é um serviço, é uma tabela que cada estado publica uma vez por ano por final de placa, e não existe API oficial gratuita. Com a tabela no repo, o modo degradado é o normal — sem internet, a feature funciona igual. Atualizar uma vez por ano é o custo, e ele está documentado no topo do `calendario.py`.
- **17/08/2026 — Sem a tabela do ano, o bot NÃO cria lembrete nenhum.** Usar o calendário do ano anterior seria o jeito mais rápido de avisar todo mundo no dia errado — e lembrete com data errada parece que funcionou, que é pior do que não ter. Ele avisa que não sabe e oferece guardar a data que a pessoa souber.
- **17/08/2026 — O M2.2 acabou SEM nenhuma API externa, e isso é resposta ao pedido, não desvio dele.** O Kevin pediu "lembretes dinâmicos via APIs externas": a API é o meio, o lembrete é o fim. Três medições da auditoria mostraram que o meio era pior — a BrasilAPI não trazia informação que o cálculo local não tivesse (o endpoint devolve só os nacionais), trazia latência no caminho síncrono de resposta (duas requisições por mensagem), e virou no-op silencioso duas vezes seguidas. **Onde a rede faria diferença de verdade é na tabela do IPVA**, que é escrita à mão e vale dois anos: é ali que mora o risco de dado velho, e é ali que uma raspagem verificada da Sefaz valeria a pena. Está em `DECISOES_PENDENTES.md` como decisão do Kevin.
- **17/08/2026 — O bot cria só a PRÓXIMA ocorrência de cada tipo, nunca dois anos de uma vez.** Nos finais 9 e 0, o licenciamento dos dois anos ainda está no futuro, e nasciam dois itens com o nome idêntico — a lista ficava com duas linhas indistinguíveis e a BAIXA quebrava: `feito Licenciamento` não fechava nenhum e ainda criava um item chamado "feito Licenciamento". Num bot cujo contrato é "me diz *feito* que eu tiro da sua lista", isso é o pior defeito possível. Lembrete com 16 meses de antecedência também não é serviço.
- **17/08/2026 — Feriado é calculado, não buscado.** Fixos são constantes e os móveis derivam da Páscoa (algoritmo de Meeus). A BrasilAPI só ACRESCENTA (estaduais, pontos facultativos), e o que vier dela é descartado se não for ISO ou se for de outro ano. Fonte externa não contamina.
- **17/08/2026 — O calendário nunca sobrescreve data que a pessoa definiu.** Se ela já tem "IPVA" na lista, o bot não toca. O dono da agenda é ela.
- **17/08/2026 — O P2-1 da auditoria do M2.1 foi considerado e RECUSADO.** O auditor apontou que a dica ("se esse pagamento era da conta X, me diz *paguei X*") pode nomear justamente o par que o veto recusou. É verdade, e é de propósito: o veto impede o BOT de decidir sozinho; a dica entrega a decisão pra PESSOA, com a conta nomeada por extenso e uma frase condicional que ela precisa digitar. Suprimir a dica ali removeria exatamente a correção que a decisão do P1-28 promete — e sem correção, escolher "o erro visível" perde o sentido.

## Motor 2 — M2.1 (OCR de boletos)

- **16/08/2026 — A linha digitável é DESCARTADA, não guardada.** Ela não ajuda a lembrar de nada: serve só pra pagar. Guardá-la seria o primeiro passo pra alguém — usuário, LLM ou uma feature futura — imaginar que o bot paga, e o guardrail de produto diz o contrário. O que fica é o que a promessa precisa: quanto, quando, pra quem. Tem teste que falha se o código voltar pro item.
- **16/08/2026 — Boleto legível pula o menu 1/2 da Regra de Ouro.** O menu existe pra imagem AMBÍGUA; perguntar "é despesa paga ou lembrete?" sobre um boleto onde o valor e o vencimento estão escritos é atrito sem serviço. Comprovante vira despesa concluída, boleto vira pendente, e a mensagem oferece a correção (`"paguei <conta>"`) — com teste que executa a frase sugerida, porque promessa em copy que o código não cumpre foi o P1-7 do M2.0.
- **16/08/2026 — Reaproveitei o `_read_image` que já existia (gpt-4o-mini com visão) em vez de somar dependência de OCR.** Uma chamada só: o prompt ganhou uma cauda estruturada (`DADOS: valor=...; vencimento=...`) e o parser em Python varre a cauda **ou** o texto livre. Cauda é atalho, não dependência — se o modelo ignorar, o resultado é o mesmo.
- **16/08/2026 — `pypdf` entrou no `requirements.txt`; é a única dependência nova do Motor 2.** Boleto de banco vem por e-mail em PDF de TEXTO, não imagem — dá pra ler sem OCR, e mandar a pessoa tirar print de um PDF é atrito bobo. É Python puro, sem binário, então o risco no build do Docker é baixo. O import é protegido: sem a lib, o PDF volta a pedir print em vez de derrubar o webhook. Não resolve PDF escaneado (imagem dentro de PDF) — esse continua pedindo print.
- **16/08/2026 — Data fora da janela de −1/+3 anos é descartada, não corrigida.** A visão troca dígito ("2026" vira "2049") e lembrete pra 2049 é lixo que a pessoa carrega pra sempre. Sem data, o item ainda entra; o que não entra é data inventada.
- **16/08/2026 — O nome do beneficiário só VETA a baixa automática; nunca a causa.** Como medida de semelhança ele falhou duas rodadas seguidas (fechava "ENEL SP" com comprovante da "ENEL RJ"; fechava a SABESP com comprovante da ENEL porque `conta` está em toda descrição). Como contradição, funciona: a chave (valor + vencimento) seleciona, e o nome pode recusar. As palavras genéricas saem dos **dois** lados antes da comparação — inclusive conectivos (`de`, `da`, `do`) e sufixos societários (`ltda`, `s.a.`, `cia`), que são o que mais aparece em razão social brasileira e por isso o que menos distingue.
- **16/08/2026 — TRADE-OFF ACEITO: sigla no comprovante × razão social no boleto não fecha automático.** "SABESP" e "Companhia de Saneamento Básico do Estado" não têm token em comum, então o veto barra e a conta paga fica pendente. A regra que resolveria (só vetar quando os dois lados têm 2+ tokens distintivos) **reabre** o caso "ENEL DISTRIBUICAO × conta SABESP", que tem exatamente a mesma forma — um lado com um token só, zero interseção — e voltaria a fechar a conta errada. Medido, e travado em teste. Entre os dois erros: conta paga que segue pendente gera cobrança a mais, visível e corrigível com "paguei X"; conta **não** paga que some da lista é perda silenciosa, a classe do incidente de 14/08. Fico com o erro visível.
- **16/08/2026 — Comprovante SEM beneficiário fecha pela chave sozinha.** Sem nome não há contradição possível; é a superfície que sobra, e é consciente.
- **16/08/2026 — A baixa automática pelo comprovante casa por VALOR + VENCIMENTO DO TÍTULO, nunca por semelhança de nome.** Esta é a única parte do M2.1 que **escreve estado a partir de inferência** — o resto só lê e grava o que leu — e foi a única que produziu achado grave em três rodadas seguidas, sempre pela mesma porta: interseção de uma palavra fechava "ENEL SP" com comprovante da "ENEL RJ" (sigla de 2 letras era descartada); depois o placar por sobreposição fechava a conta da SABESP com comprovante da ENEL, porque `descricao_de` gera sempre "conta &lt;quem&gt;" e a palavra `conta` está em toda descrição. Cada rodada consertava uma via e abria outra, porque o critério era **semelhança de texto**. Valor + vencimento é chave, não semelhança. O auditor recomendou cortar a função do bloco; preferi tirar a inferência e manter a peça, porque sem ela o mesmo pagamento entra duas vezes no gasto do mês. Sem o vencimento do título no comprovante, o bot **não fecha nada** — quem fecha é a pessoa, com o "paguei X" que a própria mensagem ensina.
- **16/08/2026 — Boleto vs comprovante é decidido por ESTRUTURA, não por vocabulário: documento com vencimento no futuro nunca é comprovante.** Três rodadas de auditoria trocaram de lado nesse par — lista de palavras marcava boleto como pago, aí marcava comprovante como a pagar, aí de novo. O motivo é que "cedente", "nosso número", "recibo" e "pagamento" existem **nos dois** documentos; cada rodada testava a direção que tinha acabado de consertar. Recibo não tem data a vencer: esse é o primeiro critério do arquivo que não depende de palavra-chave, e é ele que fecha o ciclo. Complementos: `valor pago` saiu da lista de comprovante (é nome de campo de boleto), `comprovante` solto saiu (casava "Comprovante de entrega"), e a precedência devolveu ao canhoto (`ficha de compensação`, `recibo do pagador`) a prioridade sobre a marca de pagamento.
- **16/08/2026 — Rótulo só corta o nome do beneficiário quando vem seguido de `:` ou de número.** Cortar na palavra solta apagava nome de empresa legítimo — "Total Energia S.A.", "Recibo Verde Ltda", "Data Center Brasil", "Valor Seguros S.A." viravam `None`, a descrição caía pra "conta a pagar" e duas contas diferentes ganhavam a mesma frase sugerida, ressuscitando o item fantasma.
- **16/08/2026 — Na legenda, a dúvida decide por PENDENTE.** O erro é assimétrico: marcar como paga uma conta pendente tira ela da lista e nenhum lembrete dispara; deixar pendente uma conta paga custa uma mensagem a mais. Por isso "paguei? não, ainda não", "paguei a luz, essa aqui é a água" e "essa conta é paga todo mês" não marcam nada.
- **16/08/2026 — Sem valor, não é conta.** É o único sinal que separa "isso é dinheiro" de "isso é uma foto qualquer". Sem ele o caminho devolve None e o menu antigo assume — foto de cachorro não vira item.

## Motor 2 — M2.0 (templates)

- **16/08/2026 — Status de aprovação dos templates vive em `TEMPLATES_APROVADOS` (env, CSV), default vazio.** Alternativa era tabela no banco. Env ganhou porque a fonte da verdade é o Business Manager, não o nosso banco: banco daria a ilusão de aprovado sem a Meta ter aprovado. Default vazio é *fail-closed* — enquanto o Kevin não aprovar nada no BM, nada sai fora da janela, e a falha aparece no log em vez de virar mensagem que nunca chega.
- **16/08/2026 — `canal.falar()` é a porta única de saída para mensagem proativa.** Decidir janela/template em cada chamador seria repetir a regra em N lugares e esquecer em N+1. A regra que protege o número mora em um arquivo só.
- **16/08/2026 — Fora da janela, sem template aprovado, a mensagem NÃO é enviada nem no canal reserva (WasenderAPI).** Foi exatamente texto livre fora da janela que rendeu duas restrições da Meta em 24h. Consequência aceita e registrada: se um dia cairmos no canal reserva, as proativas param — mas param *ruidosamente* (log + `out_falhou` no painel), não em silêncio.
- **16/08/2026 — As variáveis de cada template são derivadas em UM lugar (`templates.para_disparo`), não em cada gerador do scheduler.** Menos superfície de mudança, e o scheduler continua responsável só pelo texto livre. Se a Meta reprovar um template, muda-se o catálogo sem tocar no motor proativo.
- **16/08/2026 — Templates v1 saem SEM botões de resposta rápida.** O corpo já instrui ("responda *feito*"), e botão em template é mais superfície pra reprovação na primeira submissão. Quando os cinco estiverem aprovados, botão vira melhoria incremental.
- **16/08/2026 — `resolveai_fim_de_trial_aviso` não menciona assinatura, preço ou "assine".** Aviso factual de fim de acesso é utility; pitch de venda é marketing. O convite de assinatura continua saindo dentro da janela, onde já funciona.
- **16/08/2026 — `resolveai_reengajamento_pendentes` fala do dado da pessoa ("você tem N itens pendentes"), nunca "sentimos sua falta".** Utility se sustenta no dado dela; saudade é marketing e seria reprovado.
- **16/08/2026 — Incidente de ferramenta, não de produto:** bumpei o BUILD com `Get-Content -Raw | Set-Content -Encoding utf8` e corrompi 730 acentos do `wa_bot.py` (lido em ANSI, gravado em UTF-8 com BOM). O arquivo continuou compilando; quem pegou foi a suíte. Restaurado com `git checkout -- wa_bot.py` e refeito pela ferramenta de edição. Registrado na seção 6 do `CLAUDE.md` — é a segunda vez que este projeto apanha de "o arquivo compila, logo está certo".
- **16/08/2026 — A auditoria do M2.0 reprovou com 4 P0, e a raiz de dois deles era o HARNESS, não o código.** Meus testes gravavam `msg_log` com `user_id`; a produção grava `None` (o webhook não conhece o usuário na hora de logar). Resultado: `dentro_da_janela` nunca devolveria True no ar, e o motor proativo inteiro teria parado — com a suíte verde. Consertos: `dentro_da_janela` casa por telefone também, `ts` normalizado nos dois lados (o corte usava espaço e o log usa `T`, e `'T' > ' '` esticava a janela pra quase 48h), e os testes novos gravam a entrada **como o webhook grava**. Regra que fica: teste de caminho de produção reproduz a ESCRITA de produção, não uma escrita conveniente.
- **16/08/2026 — Quem marca dedup é quem ENVIA, nunca quem gera.** Valia pro `log_dispatch` e não valia pro `mark_nudge_sent` (trial guiado) nem pros itens irmãos do grupo de vencidos. Com o M2.0 recusando envio, isso queimava o nudge de conversão (`d6_fim`, o único com link de pagamento) e apagava itens vencidos pra sempre.
- **16/08/2026 — `KIND_TEMPLATE` não cobre "vencimento" nem as variantes "atrasado"/"escalonado" do alarme.** O texto livre dessas tem conteúdo diferente do template ("vence em 20/08" ≠ "chegou a hora"; e o escalonamento do M1.5 promete PARAR de cobrar). Fora da janela elas não saem — melhor não falar do que falar outra coisa.
- **16/08/2026 — `ver tudo` / `lista` viraram comando determinístico em Python.** Os templates mandam a pessoa responder isso, e o corpo aprovado é contrato com a Meta: mudar depois exige nova aprovação. Antes, "ver tudo" caía no "não identifiquei conta, data nem valor". Quem se ajusta é o código, não o contrato.
- **16/08/2026 — Os 4 caminhos de alerta do dono (`_alertar_dono`, `relatorio_matinal`, `maybe_admin_report`, `watchdog_check`) NÃO passam pela porta única.** Passar o alerta de falha por uma porta que pode recusar o envio faria o alerta sumir exatamente quando o sistema quebrou. O auditor concordou e pediu que a exceção fosse documentada com arquivo e motivo — está na seção M2.0 do `CLAUDE.md`. Custo aceito: no canal oficial, esses avisos não chegam se o Kevin não falar com o bot há 24h. Fechar isso direito pede um template UTILITY de operação para o número dele; ficou no backlog.
- **16/08/2026 — Comando novo (`ver tudo`) vai DEPOIS dos gates de acesso, nunca em `_handle_commands`.** Eu pus no lugar errado e a auditoria pegou: usuário bloqueado pelo admin recebia a lista inteira de itens, e quem cancelou recebia a lista em vez do convite de reativação. O comentário que proíbe isso já estava no arquivo, com a data do incidente anterior.
- **16/08/2026 — Descoberta durante o M2.0: hoje, no canal oficial, proativa para quem está fora da janela JÁ FALHA** (`meta_cloud.send_text` recebe erro 131047 da Meta). Ou seja, os templates não são otimização — são o que faz lembrete de gente inativa existir. Isso reforça a prioridade que o Kevin deu ao M2.0.

---

# M2.5 — os cinco ajustes antes de subir (18/08/2026)

**Tabela de IPVA: apaguei o ano 2027 em vez de mantê-lo.** Ele era invenção
minha (dias de 2026 deslocados pra fugir do fim de semana) e passava em todos
os testes de forma que existiam. O calendário de SP não é derivável — o
primeiro dia útil de janeiro que abre a fila é decisão da Sefaz, não regra.
Sem edital conferido, nenhum lembrete. `ANOS_CONFERIDOS` é a trava que impede
a próxima "ajudinha", e o dono é avisado 150 dias antes de a tabela acabar.

**Licenciamento: guardo o MÊS e derivo o dia, não o contrário.** O edital dá
mês limite. Guardar a data pronta convida a erro de transcrição — foi assim
que a versão anterior nasceu com o final 0 repetindo a data do final 7. E o
dia derivado é o último dia ÚTIL do mês: 31/10/2026 é sábado, o prazo legal
não se estende por isso, e quem só for lembrado no dia 31 encontra banco
fechado. O lembrete anda pra trás, nunca pra frente.

**Os finais do licenciamento vêm pareados (1-2, 3-4, 5-6, 7-8).** O teste
antigo exigia "dez datas distintas" e teria reprovado a tabela CERTA. Teste
que pede simetria onde a fonte não tem simetria mede a si mesmo. Trocado por
comparação com o edital literal.

**Antecedência de veículo é D-30/D-7/D-1, e o gatilho é a CATEGORIA.**
Farejar a palavra "licenciamento" na descrição seria a mesma regra por
palavra-chave que custou quatro rodadas de auditoria no M2.1. `Veículo` é
campo estrutural, com lista fechada. E fica restrito de propósito: se os 30
dias vazarem pro resto, o bot vira o que avisa da conta de luz um mês antes.

**A janela de consulta virou função, não constante.** `DUE_WINDOW_DAYS` é o
filtro de SQL e `DUE_ALERT_DAYS` é o filtro em Python; mudar um sem o outro
desliga o aviso em silêncio. Aconteceu comigo nesta mudança — o `wa_bot`
sobrescreve a janela para 1 no import, e o D-30 não disparava com o código
todo certo. Agora `definir_politica_de_aviso()` recalcula os dois juntos, e
um teste checa a invariante DEPOIS de importar produção.

**Prazo vencido é dito com todas as letras.** Em agosto o IPVA de janeiro já
foi e o licenciamento dos finais 1 e 2 também: no meio do ano esse é o caso
COMUM. O erro de produto aqui não seria criar o lembrete errado — seria o
silêncio, com a pessoa saindo da conversa achando que está coberta. A
resposta enumera o que venceu e diz explicitamente o que acontece com o ano
seguinte.

**Parcelamento do IPVA: menciono, não agendo.** 12/04/2026 é domingo, ou
seja, "a parcela cai no mesmo dia todo mês" não sobrevive ao calendário real.

**Submissão de template por API, seca por padrão.** O passo que fala com a
Meta é o que você digita. "Já existe" é o resultado NORMAL da segunda
execução e não pode derrubar o lote — senão você corrige um template
reprovado e os outros seis nunca sobem. Credencial faltando diz o nome exato
da variável: quem roda isso está no terminal do EasyPanel.

**Dois templates novos, e o mais importante é o `conta_a_vencer`.** O aviso
de vencimento é o disparo mais comum do produto e era o único sem template —
o M2.0 tinha tirado ele do mapa porque o único template disponível diria
"Chegou a hora", urgência falsa sem data. A decisão estava certa; a
consequência (quem passa 24h calado não recebe aviso de conta) não. Agora o
kind tem template próprio, que diz a data.

**`KINDS_PROATIVOS` + `KINDS_SEM_TEMPLATE`:** para "esqueci de mapear" e
"decidi não mapear" pararem de ter a mesma aparência. Um teste varre o
código-fonte e cobra a volta, senão a lista envelhece em silêncio — que é o
defeito que ela existe pra evitar.

**Resumo de gastos conta o que foi REGISTRADO, não o que foi pago.** Medir
pela baixa dependeria de a pessoa responder "paguei", e quem não responde é
exatamente quem o resumo deveria alcançar: chegaria vazio pra quase toda a
base. A mensagem diz "registradas" com essas palavras — não afirma que o
dinheiro saiu, e por isso não herda a ambiguidade do `month_spend`.

**Quem não tem dado não recebe o resumo, e o convite varia.** Resumo vazio é
o jeito mais rápido de ensinar alguém a ignorar o bot — e depois disso os
lembretes também passam batido, que é o produto inteiro. O rodízio de
convites é determinístico (semana + id), então não repete na semana seguinte
e não precisa guardar estado. Nenhum convite promete pagar nada: o guardrail
de produto vale na copy também.

**Relatório do dono: de descritivo para acionável.** A versão anterior estava
correta e era pouco útil — empilhava saúde técnica, métrica de negócio e
dinheiro na mesma altura visual, e mostrava sempre o valor de hoje sem
comparação. Agora: seção *FAZER HOJE* no topo, no máximo 3 itens, cada um com
o verbo do que fazer, e **só aparece quando existe algo** (seção que aparece
todo dia vira cabeçalho, e cabeçalho a gente pula). Depois um número só —
hábito — sempre com a tendência contra a semana anterior. O resto comprime em
três linhas, porque detalhe é papel do dash.

**Cada métrica do relatório é lida em `try` próprio.** Ele é o único lugar
onde o dono descobre que algo quebrou; uma consulta ruim não pode apagar o
relatório inteiro justamente no dia em que a falha aconteceu.

**Reset de trial escreve em `trial_base`, nunca em `data_criacao`.**
`data_criacao` alimenta "novos por dia" e a idade da base — mexer nela faria
os 11 usuários parecerem ter entrado hoje. Um lugar só decide o relógio do
trial (`_base_do_trial`), senão o trial guiado e o fim de trial passam a
contar dias diferentes pra mesma pessoa.

**O reset não toca em item, não ressuscita quem cancelou e não repete a régua
do trial.** Comando que varre a base inteira de uma vez é o pior lugar
possível pra "aproveitar e limpar" qualquer coisa (regra 10). Voltar o
relógio sem preservar `trial_nudges_sent` faria 11 pessoas receberem de novo
a mensagem de boas-vindas — o oposto de "testem as melhorias". Idempotente
por dia: rodar de novo porque a primeira "pareceu não funcionar" não vira 28
dias.

**O comando é frase exata, não prefixo.** `startswith("resetar")`
transformaria "me lembra de resetar o trial amanhã" numa escrita sobre a base
inteira — o mesmo modo de falha do menu 1/2 que custou a FASE 1.

**`admin_acoes`:** rastro de quem mexeu por fora do fluxo normal. Sem ele,
"o trial de todo mundo voltou" não tem resposta pra "quem fez, quando, e em
quantos".

**Três vazamentos de estado entre testes, achados aqui e fechados na
conftest:** `dispatches` (o dedup do relatório do dono usa `user_id=0` e
escapava da limpeza — o primeiro teste passava e todos os seguintes recebiam
string vazia), `trial_base` e `data_criacao` (um teste que envelhecia o
usuário pra simular fim de trial deixava o motor proativo mudo em arquivos
que não falam de trial nenhum). O padrão é o mesmo de sempre: teste que mede
o banco acumulado em vez do próprio caso.

## M2.5 — a rodada de auditoria (REPROVADO na primeira, 18/08/2026)

Duas das cinco features não funcionavam em produção, com 1064 testes verdes, e
as duas vieram do **conserto recém-escrito** — o padrão que o CLAUDE.md já
registrava, confirmado pela 24ª rodada.

**P0-1 — o resumo de gastos nunca era enviado.** `gastos_dispatches` foi
criado no scheduler, entrou no `total`, ganhou template e ganhou teste, e
nunca saía: a lista de chaves do `dispatch_proactive` era escrita à mão e
ninguém acrescentou a nova. Sem erro, sem log, suíte verde. O conserto não é
"acrescentar a chave" — é **derivar as chaves da resposta do motor**, porque
a lista à mão transformava cada checagem nova numa chance de repetir isso. O
teste que existia só afirmava `"gastos_dispatches" in out`; o novo vai do
motor até o `falar`, e há um segundo que injeta uma chave inventada e exige
que ela chegue ao envio.

**P0-2 — o aviso de vencimento saía "vence em *em breve*".** O template lia
`d["quando"]`, e o `check_due_items` nunca punha esse campo. 100% dos casos,
e é a única mensagem que chega em quem passou 24h sem falar com o bot. O
teste que existia **fabricava** `"quando"` num dict que a produção nunca
produz — atestava o que não verificou. Agora o campo é preenchido na origem
e o template é **fail-closed**: sem data, não sai. Texto que promete data e
entrega "em breve" é o mesmo defeito de data errada, com outro nome.

**P1-3 — o reset rebaixava assinante e desbloqueava banido.** A guarda só
recusava `cancelado`. `set_status` aceita quatro estados; o único que faz
sentido resetar é `trial`. Hoje há 0 pagantes — é o primeiro que paga a
conta.

**P1-4 — cinco dos sete corpos começavam em `{{1}}`.** O repo declarava a
regra num comentário e aplicava só a metade "termina". E o
`resolveai_resumo_de_gastos` tinha `{{4}}\n\n{{5}}` — parâmetros adjacentes,
outra reprovação certa. O "💡" entre os dois não é enfeite. Nenhum estava
submetido, então corrigir custou zero; se estivessem, custaria uma rodada de
espera por causa de um "Oi".

**P1-5 — a ação que vale dinheiro era a primeira cortada.** As ações saíam na
ordem em que eu escrevi os `if`, e com teto de 3 itens "decidem em até 3 dias"
caía fora sempre que houvesse três problemas técnicos — a única linha do
relatório em que um dia de atraso custa um assinante. Agora cada ação tem
PESO, e o corte diz quantas ficaram de fora. E o aviso do calendário passou a
cobrar **só às segundas**: diário, ele faria a seção *FAZER HOJE* aparecer
todos os dias de agosto a dezembro, virando o cabeçalho que a própria seção
promete não ser. Depois que a tabela estoura, aí sim é todo dia — é falha em
curso, não aviso preventivo.

**P1-6 — a resposta da placa calava sobre 2027 justamente para quem recebia
lembrete.** A frase só existia no ramo "não criei nada". Quem recebe o item
terminava lendo "eu te aviso com antecedência" e supunha estar coberto —
inclusive o final 0, cujo licenciamento cai em 31/12 e cujo IPVA vem 23 dias
depois. Era o mesmo "pular calado pra 2027" proibido pelo dono, no ramo que
ninguém tinha testado.

**Achado meu, lendo as dez respostas depois do conserto:** a nota do
parcelamento tinha ficado colada na linha do LICENCIAMENTO, descrevendo a
coisa errada — "pague em 5x" embaixo do licenciamento é informação falsa
sobre dinheiro. Ela agora gruda no IPVA, e quando o IPVA já venceu ela vira
"no ano que vem dá pra...".

**Mutação depois dos consertos: 18 de 18 mortos.** Os que sobreviveram na
auditoria (`engajamento` sem limite superior de janela, porta larga no reset,
licenciamento sem o "mês inteiro", tendência sempre "igual", teto de ações,
relatório com a base fora do ar) ganharam teste cada um.

## M2.5 — rodada 2 da auditoria (REPROVADO de novo, 18/08/2026)

Nenhum dos oito consertos da rodada 1 estava errado. **A reprovação foi pelo
que eles encostaram** — três P1, e o primeiro é o mais caro do projeto.

**O reset de trial DESLIGAVA a extensão de 7 dias, em silêncio.** O M2.5 criou
`trial_base` e `_base_do_trial`, que lê `trial_base` primeiro. O
`admin_extend_trial` continuou escrevendo em `data_criacao`. Depois de um
reset, `trial_base` existe → a extensão vira no-op. Três estragos numa
tacada: a frase se contradiz na mesma linha ("liberei +7 dias — agora são
14"), a pessoa perde 7 dias, e o `log_dispatch("extensao-trial")` é queimado
sem a ação ter acontecido — e a extensão é **uma por usuário**, então some
pra sempre. É exatamente o padrão que o CLAUDE.md registra como o defeito
mais caro daqui ("quem marca dedup é quem envia"), do lado errado. E atingia
os 11 usuários que o reset existe pra atender.

Conserto: **um relógio só.** Todo mundo que mexe no prazo do trial escreve em
`trial_base` — `admin_extend_trial` e `set_created_days_ago` inclusive. Esse
último é utilitário de teste, e virava no-op depois de um reset: qualquer
teste futuro que simulasse fim de trial mediria nada e passaria. Teste cego é
pior que teste ausente.

**"Quando sair, eu crio o lembrete sozinho" era mentira.** O final da placa
não era guardado em lugar nenhum — vivia numa variável local e morria no
`return`. Não existe job que releia a tabela. Quando 2027 entrasse, nada
aconteceria, e a pessoa não teria como saber. E o conserto do P1-6 tinha
acabado de espalhar essa frase para as outras 8 respostas.

Conserto em duas pontas: o final passou a ser **guardado** (`users.placa_final`
— o "Anotei o final N" virou verdade), e a copy passou a dizer **"me manda a
placa de novo que eu crio na hora"**. Promessa que o código não cumpre é pior
que promessa nenhuma: ela faz a pessoa parar de procurar a informação em
outro lugar.

**O conserto do P0-1 ligou dois digests semanais na mesma manhã.** Enquanto o
resumo de gastos não era enviado, ninguém via; assim que passou a sair, a
pessoa recebia duas proativas em segundos, com conteúdo sobreposto — o
`dia_resumo` default é segunda e o gastos estava fixo na segunda. Num número
com duas restrições da Meta no histórico, isso é comprar a terceira.

Conserto: o dia do resumo de gastos é **derivado** (`dia_de_gastos`) e nunca
coincide com o `dia_resumo` da pessoa — quem tem o resumo na segunda recebe os
gastos na terça. A escolha é por `dia_resumo` e **não** por "já disparou
hoje", porque o dedup só é marcado no ENVIO: no momento do check o resumo do
dia ainda não saiu, e depender disso seria corrida entre dois checks.

**Dois `else` mudos que eram o P0-1 esperando outro tipo de dado:** valor
`*_dispatches` que não fosse `list` sumia sem log (com o log dizendo "0 pra
enviar"), e template mapeado sem ramo de montagem devolvia `(None, [])` calado
— fora da janela, nunca sairia, sem sinal. Os dois viraram `log.error`.

**A composição das ações estava fora do `_seguro`**, enquanto a docstring
prometia o contrário: um nome só com espaço estourava `IndexError`, o
relatório das 8h não saía, o `log_dispatch` não gravava, e o cron repetia a
falha a cada ciclo das 8h às 12h — todo dia.

**"+N no dash" apontava pra um lugar que não tem o item** (o aviso de
calendário não existe no dash). Virou "+N não mostrada(s)".

**A `NOTA_IPVA` estava inalcançável e duplicada:** a constante só rodava de 01
a 23 de janeiro, e o texto que de fato saía era uma cópia à mão que perdia o
"de fevereiro a maio". Duas versões da mesma informação sobre dinheiro, e a
canônica era a que não rodava. Agora são duas constantes no `calendario.py`,
usadas nos três ramos — inclusive no dos finais 1 e 2, que perderam tudo
neste ano e eram os únicos sem a informação útil pro ano que vem.

**A ressalva sobre 2027 passou a vir DEPOIS da promessa.** No conserto da
rodada 1 ela ficava no meio, e a última linha voltava a ser "eu te aviso com
antecedência", sem qualificação.

**`DUE_ALERT_DAYS={1}` virou o default do `scheduler`** em vez de override do
`wa_bot`: o `app.py` importa `scheduler` sem importar `wa_bot`, então o painel
simulava D-3/D-1/D-0 enquanto a produção mandava D-1.

**A tendência não inventa mais crescimento**: comparar contra uma semana sem
base dava "▲ 2.00 vs. semana passada", que lê como progresso e é só o primeiro
dado existindo. E `total_anterior` passou a usar o mesmo filtro do lado atual
— sem isso a comparação tinha viés embutido, sempre na mesma direção.

**Vazamento de estado nº 4, causado por um teste meu:** um caso trocava o
`dia_resumo` pra provar a regra e não restaurava, e todos os seguintes
mediam o dia errado — com sintoma de "o motor proativo não dispara", que
parece defeito de produção. A `conftest` agora restaura `dia_resumo`,
`placa_final`, `trial_base` e `data_criacao`.

## M2.5 — rodada 3 da auditoria (REPROVADO, 2 P1 de uma linha)

Escopo pequeno pela primeira vez, e os dois achados são o conserto da rodada
2 encostando em código antigo.

**O painel mentia sobre o trial.** `admin_list_users` era o único `SELECT` de
usuário escrito à mão no projeto — todo o resto usa `SELECT *`. Por isso foi o
único que não viu a coluna `trial_base` nascer: o painel contava pelo
`data_criacao` enquanto o bot contava pelo relógio novo. O dono clicava em
"+7 dias", o número não mexia, ele clicava de novo — e **cada clique dava +7
de verdade**, invisível. Virou `SELECT u.*`: coluna nova não pode depender de
alguém lembrar de vir aqui.

**A extensão ainda queimava a chance única sem executar.** Eu tinha escrito na
docstring do `admin_extend_trial` que esse padrão estava consertado, e deixei
o único chamador de usuário sem o `if`. Falha o UPDATE → a pessoa lê "liberei
+7 dias" (o `faltam` relê o usuário não alterado) → `dispatched_ever` a
bloqueia para sempre, porque a extensão é uma por usuário.

**Seção de ação quebrada some inteira.** O `_seguro` devolvia `[]`, e ausência
de seção lê como "não tem nada a fazer" — que é o estado default. Silêncio que
imita normalidade é o pior no-op. Agora a seção aparece dizendo que o cálculo
falhou.

**Quatro testes meus eram cegos** — passavam com o conserto e sem ele:

- o do cooldown ia de terça pra quarta, e `dia_de_gastos` nunca devolve quarta
  (só segunda ou terça): a lista vinha vazia por outro motivo;
- o mesmo teste, refeito, ainda sobrevivia a `COOLDOWN=1`, porque os dois
  envios tinham a mesma hora e 24h pra trás ainda alcançavam o primeiro. O
  cron roda a cada 5–15 min: basta o primeiro sair 8h30 e o segundo 9h;
- o ramo "não tenho o calendário desse ano" não tinha teste nenhum — e a
  partir de 01/01/2027 ele é a **única** resposta que o bot dá pra placa;
- a ordem da ressalva de 2027 (o P2-7 era exatamente a ordem) só era checada
  por presença.

Lição que fica: **teste que verifica presença não verifica posição, e teste de
cooldown que usa a mesma hora nos dois pontos não verifica duração.**

## M2.5 — rodada 4: APROVADO, com uma condição nomeada (18/08/2026)

Nenhum P0/P1 no código de produção. O auditor nomeou uma condição e apontou
quatro P2 — fechei os cinco.

**A condição: um teste meu era placebo.** `test_em_2027_a_unica_resposta_da_
placa_nao_promete_nada` proibia o literal `"sozinho"`. Qualquer redação nova
("pode deixar comigo", "eu te aviso quando chegar a hora") passava, e o teste
contava como cobertura que não existia — no ramo que, a partir de 01/01/2027,
vira a **única** resposta que o bot dá pra placa.

Proibir a lista de palavras também não serve: _"me manda a placa que **eu
crio** os lembretes"_ é legítimo — a promessa está condicionada à pessoa agir.
O que separa uma da outra é a **posição**: toda promessa tem que vir DEPOIS do
pedido. Promessa antes do pedido é promessa que o bot faz sozinho, e essa ele
não cumpre. Virou invariante de posição, não lista de palavras.

**Vazamento que o meu próprio conserto causou.** O `SELECT u.*` do P1-1
consertou o painel e passou a linha INTEIRA de `users` para o `/api/pulso` —
`idade`, `profissao`, `carro_modelo`, `pet_info`, `placa_final`,
`lgpd_aceite_em` — da base toda, num endpoint cuja chave viaja em query
string. Não sai do perímetro do token, mas o estrago de uma URL vazada saltou
de "nome e telefone" para dossiê.

Conserto com os dois lados no lugar certo: **o SQL continua trazendo tudo**
(coluna nova não pode depender de alguém lembrar de ir no SELECT) e **a saída
entrega só o que o painel usa**. Whitelist na serialização, não na consulta.

**`admin_extend_trial` desbloqueava quem foi bloqueado** — `status='trial'`
incondicional. É a mesma porta que o `resetar_trial` fechou nesta fase, e esta
função tinha ficado aberta. O botão "+dias" do painel devolvia acesso.

**`_base_do_trial` engolia `ValueError` sem log:** data ilegível virava trial
que nunca expira, em silêncio, sem uma linha no servidor. A direção do
fallback (não cortar acesso por campo torto) agora tem teste, e não só um
comentário dizendo que era de propósito.

---

# M2.6 — o que a Meta chama de utilidade (26/08/2026)

Submetendo os sete templates na mão, o Business Manager recusou dois **antes
mesmo da submissão**, com o aviso *"A categoria não corresponde — este modelo
será rejeitado"* e recomendando MARKETING: o `reengajamento_pendentes` e o
`resumo_de_gastos`.

**A régua dela não é o tom do texto, é o MOTIVO da mensagem.** Falar de UM
item que a pessoa cadastrou, com data, é utilidade. Falar de VOLTAR a usar é
marketing. Os quatro que passaram (`lembrete_hora`, `item_vencido`,
`resumo_do_dia`, `conta_a_vencer`) têm em comum estarem presos a um
compromisso com data; os dois recusados, não.

**E ela classificou certo.** "Você tem 2 itens pendentes" é uma mensagem sobre
o PRODUTO. "Sua conta de luz está parada desde 12/08" é uma mensagem sobre a
vida da pessoa — e é a segunda que faz alguém pagar R$ 19,90. O texto fraco
era nosso, não o critério dela.

**Reengajamento: reescrito, e virou utilidade de verdade.** Saiu a contagem,
entrou o item mais antigo com a data em que ele foi criado. O `min()` por
`data_criacao` não é detalhe: o mais antigo é o que a pessoa mais deixou
parado, e portanto o que mais justifica interromper alguém que sumiu.

**Fail-closed na data**, igual ao `conta_a_vencer`: o corpo promete "desde
{{3}}", e sem data legível o template não sai. Template que promete data e
entrega vazio é o mesmo defeito de data errada com outro nome — e `_dia_e_mes`
devolve string vazia em vez de inventar "hoje", porque inventar dado sobre a
vida da pessoa pra não deixar a mensagem morrer é exatamente o que este
produto não pode fazer.

**Resumo de gastos: saiu do catálogo.** Um resumo semanal é um AGREGADO, e não
existe versão dele que fale de um item só sem deixar de ser um resumo. Não dá
pra consertar o texto; dá pra escolher onde ele vive. Escolha: **dentro da
janela de 24h, como texto livre**, e `"gastos"` entrou em `KINDS_SEM_TEMPLATE`
como exceção declarada.

O custo é pequeno, e o motivo importa: o resumo só é montado pra quem
registrou 2+ despesas na semana, ou seja, pra quem está usando o bot — e quem
usa quase sempre falou com ele nas últimas 24h. Quem sumiu há dias não tem
gasto registrado e não receberia o resumo de jeito nenhum.

A alternativa era submeter como MARKETING: mais caro por mensagem, opt-out
obrigatório, e contando na cota de marketing de um número que já levou duas
restrições da Meta. Não compensa por um digest.

**Regra nova do repo, virada em teste:** `test_todo_template_cita_item_ou_
hora_marcada` exige que todo corpo do catálogo cite o item ou a data. Template
novo que só conte quantidade reprova aqui, e não numa rodada de submissão com
dias de espera.

**Estado na Meta:** 4 em análise (`lembrete_hora`, `item_vencido`,
`resumo_do_dia`, `conta_a_vencer`). Faltam submeter o `reengajamento_pendentes`
reescrito e o `fim_de_trial_aviso`, que ainda não foi testado contra a régua.

## M2.6 — as duas rodadas de auditoria

**Rodada 1: REPROVADO, e o P0 foi meu.** Ao reescrever o corpo do
reengajamento eu troquei "Responda *ver tudo*" por "Responda *feito*" — e o
disparo de reengajamento tem `item_id: None` **por construção** (anti-churn e
winback). Quem resolve `feito` sozinho é o `_alvo_da_baixa`, que só enxerga
disparos COM item_id. Então o item citado no template é inalcançável, e o
`feito` fecha **o item do último alarme**.

O modo de falha completo, reproduzido pelo auditor: a pessoa some por 10 dias,
tem uma conta de luz vencendo e uma lâmpada parada há 40 dias. No mesmo ciclo
recebe o aviso da luz e depois o reengajamento citando a lâmpada. Responde
`feito`. O bot dá baixa **na conta de luz**. Perda de dado, regra 10 — e é o
mesmo P1-7 do M2.0 que o comentário três linhas acima do corpo jura estar
honrando: *prometer no corpo só o que o Python garante.*

Voltou pra "ver tudo", e a regra virou teste geral em vez de caso:
`test_so_promete_feito_quem_manda_item_id` varre o `KIND_TEMPLATE` inteiro.

**A data podia aparecer no futuro.** `_dia_e_mes` devolvia só `dd/mm`, e o
`min()` do reengajamento escolhe justamente o item mais antigo — ou seja,
maximiza a chance de o carimbo ser de outro ano. Item parado desde 03/09/2025
aparecia como *"desde 03/09"*, uma data que ainda não aconteceu. Agora inclui
o ano quando não é o ano corrente.

**`fromisoformat` com fatiamento.** A leitura passou a ser
`date.fromisoformat(texto[:10])`. O `[:10]` é load-bearing e o auditor
confirmou por quê: `fromisoformat("2026-08-12 09:31:00")` levanta `ValueError`
no 3.12 — sem o corte, a função recusaria **100% dos carimbos reais**. E de
quebra matou o `IndexError` que uma string de 5 caracteres provocava, exceção
que subia por `para_disparo` e matava o ciclo inteiro do `dispatch_proactive`.

**Descrição só com espaço.** `or "seu item"` pega `None` e `""`, não pega
`"   "` — e o collapse de espaço em branco transformava isso num parâmetro
VAZIO, que a Cloud API recusa. A única mensagem que aquela pessoa receberia
morreria na borda. Virou `.strip() or "seu item"`.

**O teste-régua que eu escrevi aprovava o que a Meta recusou.** Ele tentava
DEDUZIR o critério ("cita item ou cita data") e aceitava
`["primeiro_nome","quantidade","mais_antigo"]` — exatamente o conjunto
reprovado — pelo ramo do "mais_antigo". Teste que dá falsa segurança sobre um
critério externo é pior que teste nenhum: faz a gente parar de perguntar.

Substituído por `VEREDITO_DA_META`: um **registro** do que ela de fato
respondeu, template por template, sem dedução nenhuma. O auditor foi honesto
ao dizer que isso não é uma guarda — a trava real é a allowlist
`TEMPLATES_APROVADOS` no `canal.falar`. É documentação executável, e o teste
só cobra que template novo tenha veredito anotado, porque descobrir a régua da
Meta custa uma rodada de submissão com dias de espera.

**Rodada 2: APROVADO**, com dois P2 que ele mandou fechar antes do commit e
ambos fechados: testes que virariam vermelhos sozinhos em 01/01/2027 (usavam
"hoje menos 13 dias" e 2026 escrito à mão como ano corrente) e o
`DECISOES_PENDENTES.md` ainda mandando configurar um template que não existe
mais.

Mutação: 6 de 7 mortos. O sobrevivente troca o texto da mensagem de log
mantendo o log — equivalente, e o auditor concordou.
