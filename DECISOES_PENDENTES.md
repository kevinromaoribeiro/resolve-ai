# DECISÕES PENDENTES — só o Kevin resolve

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
