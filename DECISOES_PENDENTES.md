# DECISÕES PENDENTES — só o Kevin resolve

Coisas que travam em credencial, chave de API ou decisão de produto com dinheiro.
Nada aqui bloqueou a entrega: cada item diz o que foi entregue no lugar.

---

## M2.5 — o calendário de 2027 não existe, e não vou inventar

**Status:** aberto. É o único item deste arquivo que causa perda de função
por si só.

- **O que preciso de você, uma vez por ano:** o edital da Sefaz-SP com o
  calendário de IPVA e de licenciamento do ano seguinte. Ele sai no fim do
  ano anterior.
- **Onde entra:** `calendario.py` — `IPVA` (dias), `LICENCIAMENTO_MES`
  (meses) e `ANOS_CONFERIDOS` (a trava). O teste reprova ano que não passou
  pela sua conferência, de propósito.
- **Por que 2027 sumiu do repo:** ele existia e era **invenção minha**. Eu
  tinha produzido os dez dias de 2027 deslocando os de 2026 pra fugir do fim
  de semana, e aquilo passava em todos os testes que existiam — dez datas
  distintas, nenhuma em sábado, final 0 por último — porque teste de forma
  não enxerga data errada. Seriam doze meses de lembrete no dia errado, sem
  log, sem exceção, sem nada quebrando. Data errada é pior que lembrete
  ausente, e a sua conferência da tabela de 2026 é o que provou isso.
- **Enquanto não atualizar:** o bot não cria lembrete de carro para 2027 e
  **diz isso na resposta** ("o calendário de 2027 ainda não foi publicado…
  não vou chutar data"). O relatório das 8h passa a te cobrar em **agosto**
  (150 dias antes de a tabela acabar).
- **Decisão sua:** vale cobrir outros estados? Hoje quem é de fora de SP
  recebe as datas de SP com o aviso _"se o seu carro é de outro estado, me
  diz a data certa"_. Cobrir mais estados é copiar tabela — o custo é
  conferir cada uma, e conferir errado é pior que não ter.

## M2.5 — as parcelas 2 a 5 do IPVA não viram lembrete

- **O que o bot faz hoje:** cria o lembrete da **cota única / 1ª parcela** e
  **menciona** que dá pra pagar em 5x ou em cota única com desconto.
- **Por que não agendei as outras quatro:** "a parcela cai no mesmo dia todo
  mês" é regra de boca que o calendário real não cumpre — 12/04/2026 é
  domingo. Eu criaria quatro datas chutadas por pessoa, e o produto inteiro
  se apoia em a data estar certa.
- **Decisão sua:** se o edital trouxer as datas das parcelas 2 a 5 por final
  de placa, elas entram na tabela e viram lembrete no mesmo dia.

## M2.5 — submeter os templates (agora são 7, e dá pra fazer por API)

- **Como disparar:** `python templates/submeter.py` mostra o payload sem
  mandar nada; `--enviar` cria de verdade. Ele usa `META_WABA_ID` e
  `META_TOKEN`, que já existem no ambiente do bot, e rodar duas vezes é
  seguro (template repetido é reportado, não derruba o lote).
- **Depois da aprovação:** setar no EasyPanel e dar redeploy —

  ```
  TEMPLATES_APROVADOS=resolveai_lembrete_hora,resolveai_item_vencido,resolveai_resumo_do_dia,resolveai_reengajamento_pendentes,resolveai_fim_de_trial_aviso,resolveai_conta_a_vencer,resolveai_resumo_de_gastos
  ```

- **Os dois novos:** `resolveai_conta_a_vencer` (o aviso mais comum do
  produto, que até agora não tinha template nenhum e sumia fora da janela) e
  `resolveai_resumo_de_gastos` (o resumo de segunda).
- **Enquanto isso:** o código está *fail-closed*. Fora da janela de 24h, sem
  template aprovado, a mensagem **não é enviada** — fica registrada com o
  motivo, e o item volta no próximo ciclo. Nada é marcado como avisado sem
  ter saído. Vale pro dedup por item, pros itens irmãos de um grupo de
  vencidos e pros nudges do trial.
- **Impacto real de não submeter:** quem responde ao bot continua recebendo
  tudo (janela aberta = texto livre). Quem some por mais de 24h para de
  receber proativa. Hoje essas mensagens também não chegam — a Meta recusa
  com 131047 —, a diferença é que agora isso aparece no log.
- **Se a Meta rejeitar algum:** não vou reescrever com linguagem promocional
  pra tentar passar (sua instrução). Resubmetemos como MARKETING, e aí a
  decisão de custo/opt-out é sua.

## M2.1 — custo declarado: `month_spend` ignora o status

`db.month_spend` soma `tipo='despesa'` do mês **sem filtrar status**. Então,
sempre que uma conta pendente e o comprovante dela existem como itens
separados, o mesmo pagamento entra duas vezes no gasto do mês.

- **Por que continua aberto:** mudar `month_spend` altera o número que o
  painel e o resumo mostram há meses, e trocar isso sem você decidir é mudar
  a leitura de "quanto gastei" pelas suas costas.
- **O resumo de gastos do M2.5 não herdou o problema:** ele conta o que foi
  **registrado** na semana e diz isso com essas palavras — não afirma que o
  dinheiro saiu, então não depende de status nenhum.
- **Quando o erro ainda acontece:** comprovante sem vencimento do título, ou
  de credor diferente do que está na lista.
- **Decisão sua:** `month_spend` deve contar só `concluido`, ou contar tudo?
  Isso muda a leitura do "quanto gastei este mês" — é decisão de produto,
  não de código.
