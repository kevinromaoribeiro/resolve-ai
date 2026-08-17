# DECISÕES PENDENTES — só o Kevin resolve

## M2.2 — a tabela do IPVA precisa de manutenção anual (e de conferência sua)

- **O que preciso de você, uma vez por ano:** conferir o calendário de IPVA
  e licenciamento publicado pela Sefaz/Detran e atualizar `calendario.py`.
  Os anos cobertos hoje são **2026 e 2027**, e só para **SP**.
- **Por que não peguei de API:** não existe API oficial gratuita desse
  calendário — é tabela publicada em edital. Preferi tabela versionada a
  raspagem de site, que quebra sem avisar e no silêncio vira data errada.
- **Enquanto não atualizar:** sem a tabela do ano, o bot **não cria lembrete
  nenhum** e avisa a pessoa que ainda não tem o calendário. Nada de chutar a
  data do ano anterior.
- **Onde a rede faria diferença de verdade (decisão sua):** o M2.2 acabou sem
  API externa nenhuma — feriado é calculado e IPVA é tabela. O risco de dado
  velho não está no feriado, está **nesta tabela**. Se você quiser dinamismo
  de verdade, o alvo é raspar o edital da Sefaz com validação automática
  contra as invariantes que os testes já checam (10 datas distintas por
  tipo/ano, final 0 por último, nenhuma em feriado ou fim de semana) — e
  fora do caminho síncrono de resposta. Enquanto isso não existir, quem
  segura a promessa é este arquivo e a sua conferência anual.
- **Decisão sua:** vale cobrir outros estados? Hoje quem é de fora de SP
  recebe as datas de SP com o aviso _"se o seu carro é de outro estado, me
  diz a data certa"_. Cobrir mais estados é copiar tabela — o custo é
  conferir cada uma, e conferir errado é pior que não ter.


## M2.1 — custo declarado: `month_spend` ignora o status

`db.month_spend` soma `tipo='despesa'` do mês **sem filtrar status**. Então,
sempre que uma conta pendente e o comprovante dela existem como itens
separados, o mesmo pagamento entra duas vezes no gasto do mês.

- **Por que isso não foi consertado aqui:** mudar `month_spend` altera o
  número que o painel e o resumo mostram, e não é escopo do M2.1. O M2.1 só
  reduziu a frequência (o comprovante que casa valor + vencimento do título
  dá baixa no pendente em vez de criar irmão).
- **Quando ainda acontece:** comprovante sem vencimento do título, ou de
  credor diferente do que está na lista.
- **Decisão sua:** `month_spend` deve contar só `concluido`, ou contar tudo?
  Isso muda a leitura do "quanto gastei este mês" — é decisão de produto, não
  de código.


Coisas que travam em credencial, chave de API ou decisão de produto com dinheiro.
Nada aqui bloqueou a entrega: cada item diz o que foi entregue no lugar.

## M2.0 — Templates

### 1. Submeter os 5 templates no Business Manager
- **O que preciso de você:** colar o catálogo de `templates/SUBMISSAO.md` no
  Business Manager e submeter. Só você tem acesso à conta.
- **Depois da aprovação:** setar no EasyPanel
  `TEMPLATES_APROVADOS=resolveai_lembrete_hora,resolveai_item_vencido,resolveai_resumo_do_dia,resolveai_reengajamento_pendentes,resolveai_fim_de_trial_aviso`
  (ou só os que passarem) e redeploy.
- **Enquanto isso:** o código está pronto e *fail-closed*. Fora da janela de
  24h, sem template aprovado, a mensagem não é enviada — fica registrada como
  `out_falhou` com o motivo (uma vez por dia por item+motivo, não a cada
  ciclo de 5 min — e o sinal reaparece todo dia enquanto durar) e o
  item **volta no próximo ciclo**: nada é marcado como avisado sem ter saído.
  Vale pro dedup por item, pros itens irmãos de um grupo de vencidos e pros
  nudges do trial.
- **Impacto real de não submeter:** quem responde ao bot continua recebendo
  tudo normalmente (janela aberta = texto livre). Quem some por mais de 24h
  para de receber proativa até os templates entrarem no ar. Hoje essas
  mensagens também não chegam — a Meta recusa com 131047 —, a diferença é
  que agora isso aparece no log em vez de falhar calado.
- **Se a Meta rejeitar algum:** não vou reescrever com linguagem promocional
  pra tentar passar (sua instrução). Resubmetemos como MARKETING, e aí a
  decisão de custo/opt-out é sua.
