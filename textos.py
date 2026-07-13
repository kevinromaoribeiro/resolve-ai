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
  (ex.: {nome} vira o nome da pessoa, {trial_days} vira 7). Mantenha-os.
- Depois de editar: suba este arquivo no GitHub (repo resolve-ai) e peça um
  redeploy no EasyPanel. Como aqui só tem texto, o risco de erro é mínimo.

DICA: para trocar as sugestões dos primeiros 7 dias, edite USE_CASE_EXAMPLES.
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

# ── Sugestões dos 7 dias (o que provar valor rápido) ─────────────────────
# EDITE À VONTADE. Cada linha é um exemplo prático que aparece no onboarding
# conforme os interesses que a pessoa escolheu.
USE_CASE_EXAMPLES = {
    "contas": "💡 _\"conta de luz 187 vence dia 20\"_ → eu te aviso 3 dias antes, 1 dia antes e no dia. Multa nunca mais.",
    "mercado": "🛒 _\"comprei arroz, café e sabão em pó\"_ → eu controlo e aviso quando for hora de repor.",
    "carro": "🚗 _\"troquei o óleo, 74.200 km\"_ → calculo a próxima troca e te aviso com folga.",
    "saude": "🩺 _\"consulta com a dermato dia 15/08 às 14h\"_ → te lembro na véspera e no dia. Sem remarcar.",
    "datas": "🎂 _\"aniversário da minha mãe é 03/09\"_ → nunca mais passa em branco.",
    "encomendas": "📦 _\"minha encomenda chega até sexta\"_ → eu fico de olho no prazo por você.",
    "pet": "🐾 _\"vacina da Mel vence dia 30\"_ → te aviso antes, e lembro da ração quando estiver acabando.",
    "burocracia": "📄 _\"IPVA vence dia 15/01\"_ → lembrete com antecedência, sem susto de multa.",
}

# Frase que abre o bloco de sugestões
SUGESTOES_ABERTURA = (
    "Perfeito, {nome}! Seus *{trial_days} dias grátis* começaram agora. ✅\n\n"
    "Bora tirar umas coisas da sua cabeça? *Testa comigo:*\n"
)
SUGESTOES_RODAPE = (
    "\n\nÉ só me mandar do seu jeito — *texto, áudio ou foto*. "
    "A qualquer momento: *assinar*, *cancelar* ou *apagar meus dados*."
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
