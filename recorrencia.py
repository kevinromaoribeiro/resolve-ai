# -*- coding: utf-8 -*-
"""Depois da baixa, perguntar se já marca o próximo.

Ideia do Kevin (28/08/2026): unha, sobrancelha, dentista e barbeiro repetem
por natureza, e ninguém marca o próximo saindo do salão. Três semanas depois
a pessoa percebe que passou do ponto. O bot já sabe o que era e quando foi —
falta só perguntar.

APROVEITA O MOTOR QUE JÁ EXISTE: baixa → pergunta → item novo. Não há
scheduler novo nem template novo; é uma pergunta dentro da janela de 24h, para
quem acabou de interagir.

DUAS REGRAS QUE DECIDEM SE ISSO AJUDA OU IRRITA:

1. Só serviço que REPETE. "Paguei a conta de luz" não ganha "quer marcar a
   próxima?" — a próxima chega sozinha, e perguntar isso faz o bot parecer que
   não entende a vida da pessoa.

2. Não pergunta na hora da baixa. Ela acabou de sair do salão; perguntar no
   segundo seguinte é afobado. Espera algumas horas — foi o desenho do Kevin,
   e é o mesmo motivo pelo qual ninguém gosta de pesquisa de satisfação que
   chega antes de você guardar o troco.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Optional

import tempo

# Horas entre a baixa e a pergunta. Dez porque a maioria dos serviços acontece
# de dia: a pergunta cai na manhã seguinte, quando a pessoa está com a agenda
# na cabeça — e não às 23h do mesmo dia.
HORAS_DE_ESPERA = 10

# Serviço -> de quantos em quantos dias costuma repetir.
#
# Os intervalos vêm do uso real, não de teoria: unha a cada 3 semanas,
# sobrancelha e cabelo mensal, dentista a cada 6 meses. Errar pra mais é
# melhor que errar pra menos — lembrete cedo demais vira ruído, e a pessoa
# pode adiar com um toque.
_SERVICOS = (
    (21, (r"\bunhas?\b", r"manicure", r"pedicure", r"esmalta")),
    (21, (r"barbeiro", r"barba\b")),
    (30, (r"sobrancelhas?\b", r"design de sobrancelha")),
    (30, (r"cabelo", r"cortar o cabelo", r"corte de cabelo", r"cabeleireir")),
    (30, (r"massagem", r"massoterapia", r"drenagem")),
    (30, (r"depila[çc]", r"cera\b")),
    (45, (r"est[ée]tica", r"limpeza de pele", r"botox", r"peeling")),
    (180, (r"dentista", r"odonto", r"limpeza dos dentes", r"profilaxia")),
)

# O que NUNCA entra, mesmo que a palavra apareça: conta e compromisso pontual
# não se "remarcam". Explícito porque um dia alguém escreve "conta do
# dentista" e o casamento por palavra solta acertaria pelo motivo errado.
_NUNCA = re.compile(
    r"\b(conta|boleto|fatura|ipva|iptu|aluguel|cart[ãa]o|internet|luz|[áa]gua|"
    r"g[áa]s|condom[íi]nio|sal[áa]rio|parcela|presta[çc][ãa]o)\b", re.I)


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t or "")
                   if unicodedata.category(c) != "Mn").lower()


def sugestao(descricao: Optional[str],
             hoje: Optional[date] = None) -> Optional[dict]:
    """O serviço repete? Devolve {"dias", "proxima"} ou None.

    `None` é a resposta certa na dúvida: perguntar sobre algo que não repete
    custa a confiança de que o bot entende o contexto.
    """
    if not descricao:
        return None
    texto = str(descricao)
    if _NUNCA.search(texto):
        return None
    plano = _sem_acento(texto)
    for dias, marcas in _SERVICOS:
        for m in marcas:
            if re.search(_sem_acento(m), plano):
                base = hoje or tempo.hoje()
                return {"dias": dias,
                        "proxima": (base + timedelta(days=dias)).isoformat()}
    return None


BOTOES = ["Confirmar", "Outra data", "Não precisa"]


def pergunta(sug: Optional[dict], descricao: str) -> Optional[dict]:
    """A pergunta que a pessoa responde com um toque.

    Mostra a data JÁ CALCULADA em vez de perguntar "quando?": decidir entre
    sim e não é mais fácil que lembrar de um intervalo, e quem discorda tem a
    saída "Outra data".
    """
    if not sug:
        return None
    p = sug["proxima"]
    br = "%s/%s" % (p[8:10], p[5:7])
    return {
        "texto": ("Vi que você resolveu *%s*. ✅\n\n"
                  "Quer que eu já guarde o próximo pra *%s*?\n\n"
                  "_Aí eu te aviso antes e você não perde o ponto._"
                  % (descricao, br)),
        "botoes": list(BOTOES),
        "sugestao": sug,
        "descricao": descricao,
    }


def pendentes_de_pergunta(ref: Optional[datetime] = None,
                          limite: int = 50) -> list[dict]:
    """Itens de serviço recém-concluídos que ainda não geraram a pergunta.

    Usa `data_conclusao` (M2.8) — sem ela não dá pra saber QUANDO a baixa
    aconteceu, e a espera de 10h seria impossível de medir.

    O dedup é o de sempre (`dispatches` com kind "retorno"), então perguntar
    duas vezes pela mesma unha não acontece nem se o ciclo rodar de novo.
    """
    import db

    agora = ref or tempo.agora()
    corte = (agora - timedelta(hours=HORAS_DE_ESPERA)
             ).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_conn() as conn:
        linhas = conn.execute(
            """SELECT i.id, i.user_id, i.descricao, i.data_conclusao,
                      u.telefone, u.nome AS user_nome
                 FROM items i JOIN users u ON u.id = i.user_id
                WHERE i.status = 'concluido'
                  AND i.data_conclusao IS NOT NULL
                  AND i.data_conclusao <= ?
                  AND NOT EXISTS (SELECT 1 FROM dispatches d
                                   WHERE d.item_id = i.id AND d.kind='retorno')
                ORDER BY i.data_conclusao DESC LIMIT ?""",
            (corte, limite)).fetchall()

    out = []
    for r in linhas:
        sug = sugestao(r["descricao"], hoje=agora.date())
        if not sug:
            continue
        d = dict(r)
        d["sugestao"] = sug
        out.append(d)
    return out
