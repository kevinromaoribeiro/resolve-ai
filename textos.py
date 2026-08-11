
# ── M1.2: abertura do onboarding em 3 mensagens ────────────────────────
# WELCOME_MSG (acima) NAO foi removida — mas atencao: ela ficou SEM NENHUM
# CHAMADOR. Nao e "fallback", ninguem cai nela; e referencia de copy ate a
# M1.2 assentar. Mantida de proposito (regra: nao apagar o que existe), e
# marcada aqui pra ninguem achar que ainda esta no ar.
# O problema dela era juntar TRES coisas numa mensagem so: apresentacao +
# LGPD ("ao continuar voce aceita") + pedido do nome. Com 11 pessoas reais
# em trial, aceite enterrado em "ao continuar" nao e consentimento: e
# exposicao juridica.
# Agora: (1) esta abertura, (2) jornada.LGPD_AVISO com botao, (3) o pedido
# — que so dispara depois do clique.
WELCOME_MSG_ABERTURA = (
    "Oi! Eu sou o *Resolve AI* 🟢 — o assistente que tira da sua cabeça "
    "contas, lembretes, manutenções e compras.\n\n"
    "Eu te aviso *antes* de vencer, sozinho, aqui no Zap.\n\n"
    "🎁 Você ganhou *{trial_days} dias grátis* pra testar. "
    "Sem cartão, sem pegadinha."
)
# -*- coding: utf-8 -*-
"""
textos.py — CENTRAL DE TEXTOS DO RESOLVE AI
============================================
Este arquivo guarda TODAS as mensagens que o bot manda. A ideia é que você
possa AJUSTAR A COPY sem mexer em nenhuma lógica do motor.

COMO EDITAR (sem quebrar nada):
- Mude só o texto DENTRO das aspas.
- NÃO apague as chaves (as palavras à esquerda dos ":"), os {campos} nem
  as vírgulas.
- Os {campos} entre chaves são preenchidos automaticamente pelo motor
  (ex.: {nome} vira o nome da pessoa, {trial_days} vira 14). Mantenha-os.
- Depois de editar: suba este arquivo no GitHub (repo resolve-ai) e peça um
  redeploy no EasyPanel. Como aqui só tem texto, o risco de erro é mínimo.

DICA: para trocar as sugestões do trial (14 dias), edite USE_CASE_EXAMPLES.
"""

# ── Boas-vindas (primeira mensagem) ──────────────────────────────────────
WELCOME_MSG = (
    "Oi! Eu sou o *Resolve AI* 🟢 — o assistente que tira da sua cabeça "
    "contas, lembretes, manutenções e compras.\n\n"
    "Você ganhou *{trial_days} dias grátis* para testar, sem cartão.\n\n"
    "🔒 Suas mensagens são processadas com segurança só para te atender — "
    "nada é vendido ou compartilhado. Ao continuar, você aceita os Termos: "
    "{terms_url}\n"
    "_(a qualquer momento: mande *apagar meus dados* e tudo some)_\n\n"
    "Pra começar: *como você quer ser chamado?*"
)

# ── Menu de interesses (depois que a pessoa diz o nome) ──────────────────
INTERESSES_MSG = (
    "Prazer, {nome}! 🤝\n\n"
    "*Pra que você quer me usar?* Responda com os números (ex.: *1 3 7*) "
    "ou escreva do seu jeito:\n\n"
    "*1* 💡 Contas de casa\n"
    "*2* 🛒 Compras de mercado\n"
    "*3* 🚗 Manutenções do carro\n"
    "*4* 🩺 Consultas e exames\n"
    "*5* 🎂 Aniversários e datas\n"
    "*6* 📦 Encomendas e prazos\n"
    "*7* 🐾 Cuidados com pet\n"
    "*8* 📄 Documentos e burocracias\n\n"
    "_(pode escolher vários — ou responder *pular*)_"
)

# ── Sugestões do trial (o que prova valor rápido) ────────────────────────
# EDITE À VONTADE. Cada linha aparece no onboarding conforme os interesses
# que a pessoa marcou no formulário da landing page.
#
# REGRA DE OURO DESTA LISTA (não é enfeite):
# O momento em que a pessoa vira cliente NÃO é quando ela anota — é quando
# ela é AVISADA de algo que tinha esquecido. Então toda sugestão aqui precisa:
#   1. caber numa mensagem que ela consegue copiar e mandar AGORA;
#   2. gerar uma DATA no banco (sem data, não existe aviso, não existe "aha");
#   3. deixar claro o que eu faço que uma listinha não faz.
# Sugestão que não gera data é sugestão que adia a conversão.
USE_CASE_EXAMPLES = {
    "contas": "💡 *Contas* — manda a foto do boleto, ou escreve _\"luz 187 vence dia 20\"_. Eu te cutuco *um dia antes*, no horário em que você consegue pagar. Não é lista: é o aviso chegando sozinho.",
    "mercado": "🛒 *Mercado* — escreve _\"comprei café hoje\"_. Eu aprendo quanto tempo dura e te aviso *antes de acabar*. Você para de descobrir na hora do café.",
    "carro": "🚗 *Carro* — manda _\"troquei o óleo hoje, 74.200 km\"_. Eu guardo os *dois gatilhos* (5.000 km ou 6 meses) e aviso no que vencer primeiro. Nenhuma agenda faz isso.",
    "saude": "🩺 *Saúde* — escreve _\"dentista dia 15 às 14h\"_ ou _\"pediatra em 6 meses\"_. Consulta marcada com meio ano de antecedência é exatamente o que a cabeça não segura.",
    "datas": "🎂 *Datas* — diz _\"aniversário da minha mãe é 03/09\"_. Eu aviso com *uma semana* de folga — tempo de comprar presente, não de pedir desculpa.",
    "encomendas": "📦 *Encomendas* — escreve _\"encomenda chega até sexta\"_. Se passar do prazo, eu te lembro de cobrar. Prazo que ninguém acompanha é dinheiro parado.",
    "pet": "🐾 *Pet* — manda _\"comprei ração de 15kg hoje\"_ e _\"vacina da Mel dia 30\"_. Eu calculo quando a ração acaba e aviso antes do pote raspar.",
    "burocracia": "📄 *Documentos* — diz _\"IPVA vence 15/01\"_ ou _\"renovar CNH em março\"_. Prazo de documento é o que dá multa e você só lembra depois.",
}

# Frase única de reforço por interesse — usada nos toques da segunda semana.
# Mais curta que a de cima: aqui a pessoa já conhece o bot, o que falta é
# ela usar a função que ainda não experimentou.
USE_CASE_REFORCO = {
    "contas": "me manda a próxima conta que chegar — quero te provar o aviso de véspera",
    "mercado": "me diz uma coisa que você compra todo mês, que eu passo a avisar",
    "carro": "me fala o km atual do carro, que eu já calculo a próxima revisão",
    "saude": "tem consulta marcada? me passa a data que eu seguro pra você",
    "datas": "me dá 3 aniversários de uma vez — eu cuido deles todo ano",
    "encomendas": "tem encomenda a caminho? me fala o prazo",
    "pet": "quando você comprou a última ração? eu calculo a próxima",
    "burocracia": "tem documento vencendo esse ano? IPVA, CNH, seguro?",
}

# Abertura das sugestões — CTA forte pra AGIR já (prova de valor no minuto 1)
SUGESTOES_ABERTURA = (
    "Prazer, {nome}! Seus *{trial_days} dias grátis* começaram. 🎉\n\n"
    "Vou te mostrar na prática — *escolhe UMA coisa abaixo e me manda "
    "agora*. Em 10 segundos você sente como é ter alguém cuidando disso "
    "pra você:\n"
)
SUGESTOES_RODAPE = (
    "\n\n👆 *Escolhe uma e manda agora* (foto, áudio ou texto — do seu "
    "jeito). Quanto antes você testar, mais eu tiro da sua cabeça. 🧠"
)

# ── Recebimento de midia (sem expor bastidor tecnico ao usuario) ──────
# Estas aparecem só se a IA de leitura estiver indisponível no momento.
# Recebem o conteúdo naturalmente e pedem a decisão do usuário.
AUDIO_INDISPONIVEL = (
    "Recebi seu áudio! 🎤 Pra garantir que eu anote certinho, me confirma "
    "em uma linha o que é (ex.: _\"comprei ração, 89 reais\"_)."
)
AUDIO_LONGO = (
    "Seu áudio ficou um pouco longo 😅 — me manda uma versão mais curta "
    "(até {audio_max_min} min) ou escreve em uma linha, que eu resolvo na hora."
)
IMAGEM_PEDIR_CONTEXTO = (
    "Recebi sua imagem! 📷 Me diz em uma linha o que é (ex.: _\"boleto da "
    "Enel, 187 reais, vence dia 20\"_) que eu registro agora."
)

# ── Confirmação após ler um documento por foto ───────────────────────────
# {desc}, {valor}, {venc} são preenchidos com o que a IA leu.
CONFIRMA_LEITURA = (
    "Li aqui: *{desc}* — {valor} — vencimento {venc}.\n\n"
    "Tá certo? Responda:\n"
    "*1* ✅ Sim, pode salvar\n"
    "*2* ✏️ Corrigir (me manda o dado certo)"
)

# ── Fim de trial / pagamento ─────────────────────────────────────────────
PAGAMENTO_MSG = (
    "{nome}, seus {trial_days} dias grátis terminaram — espero ter tirado "
    "umas boas coisas da sua cabeça. 🙂\n\n"
    "Pra continuar sem interrupção:\n"
    "💳 *R$ 19,90/mês* (cancela quando quiser): {payment_link}{anual}\n\n"
    "Seus dados ficam guardados 30 dias te esperando."
)

# ── Privacidade (comando "privacidade") ──────────────────────────────────
PRIVACIDADE_MSG = (
    "🔒 *Privacidade em 4 linhas:*\n"
    "• Suas mensagens, fotos e áudios são usados só para te atender.\n"
    "• Nunca vendemos nem compartilhamos seus dados.\n"
    "• Eu *lembro* você de pagar — nunca pago, compro ou transfiro nada.\n"
    "• *apagar meus dados* remove tudo, na hora (LGPD).\n\n"
    "Termos completos: {terms_url}"
)

# ── Ajuda (comando "ajuda") ──────────────────────────────────────────────
AJUDA_MSG = (
    "Eu entendo o seu jeito de falar — manda texto, áudio ou foto. "
    "Comandos úteis:\n"
    "*assinar* · *cancelar* · *apagar meus dados* · *privacidade* · *ajuda*"
)

# ── M1.2: abertura do onboarding em 3 mensagens ────────────────────────
# WELCOME_MSG (acima) NAO foi removida — mas atencao: ela ficou SEM NENHUM
# CHAMADOR. Nao e "fallback", ninguem cai nela; e referencia de copy ate a
# M1.2 assentar. Mantida de proposito (regra: nao apagar o que existe), e
# marcada aqui pra ninguem achar que ainda esta no ar.
# O problema dela era juntar TRES coisas numa mensagem so: apresentacao +
# LGPD ("ao continuar voce aceita") + pedido do nome. Com 11 pessoas reais
# em trial, aceite enterrado em "ao continuar" nao e consentimento: e
# exposicao juridica.
# Agora: (1) esta abertura, (2) jornada.LGPD_AVISO com botao, (3) o pedido
# — que so dispara depois do clique.
WELCOME_MSG_ABERTURA = (
    "Oi! Eu sou o *Resolve AI* 🟢 — o assistente que tira da sua cabeça "
    "contas, lembretes, manutenções e compras.\n\n"
    "Eu te aviso *antes* de vencer, sozinho, aqui no Zap.\n\n"
    "🎁 Você ganhou *{trial_days} dias grátis* pra testar. "
    "Sem cartão, sem pegadinha."
)
