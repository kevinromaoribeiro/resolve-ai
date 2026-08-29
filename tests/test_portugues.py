# -*- coding: utf-8 -*-
"""PORTUGUES CORRETO NO QUE O CLIENTE LE.

O Kevin, 28/08/2026: "revise TODAS as escritas pra evitar erros de
portugues". O gatilho foi o botao "Quero comecar" sem cedilha — que ja esta
aprovado na Meta e vai ficar assim, porque mudar exige nova submissao.

Este teste NAO varre comentario nem docstring: varre o texto que sai pelo
WhatsApp. Comentario com acento faltando e irrelevante; "voce nao" na tela do
cliente e o produto parecendo amador na primeira frase.
"""
import re

import pytest

import templates
import wa_bot


# Palavra sem acento -> como deveria ser. Lista curta e de propósito: cobre o
# que de fato aparece nas mensagens do produto.
SEM_ACENTO = {
    "voce": "você", "voces": "vocês", "nao": "não", "sao": "são",
    "ja": "já", "so": "só", "ate": "até", "tambem": "também",
    "amanha": "amanhã", "mes": "mês", "proximo": "próximo",
    "proxima": "próxima", "ultimo": "último", "ultima": "última",
    "servico": "serviço", "comeca": "começa",
    "gratis": "grátis", "sera": "será", "tera": "terá", "vao": "vão",
    "entao": "então", "alguem": "alguém", "ninguem": "ninguém",
    "historico": "histórico", "codigo": "código", "numero": "número",
    "periodo": "período", "credito": "crédito", "duvida": "dúvida",
    "possivel": "possível", "disponivel": "disponível", "facil": "fácil",
    "rapido": "rápido", "automatico": "automático", "unico": "único",
    "proprio": "próprio", "atencao": "atenção", "opcao": "opção",
    "opcoes": "opções", "estao": "estão", "tres": "três",
    "usuario": "usuário", "relatorio": "relatório", "veiculo": "veículo",
    "saude": "saúde", "sabado": "sábado", "terca": "terça",
    "invalido": "inválido", "obrigatorio": "obrigatório",
}

RE_PALAVRA = re.compile(r"[a-zà-ÿ]+", re.IGNORECASE)

# O QUE ESTA APROVADO NA META NAO MUDA SEM NOVA SUBMISSAO.
#
# `reativar_boas_vindas` foi escrito e aprovado sem acentos ("Voce", "gratis",
# "comecar"). Corrigir o texto aqui deixaria o repo divergente do que a Meta
# entrega — pior que o erro, porque esconderia o erro. Fica registrado como
# divida, nao como bug.
CONGELADOS_NA_META = {"reativar_boas_vindas"}


def _erros(texto):
    achados = []
    for pal in RE_PALAVRA.findall(texto or ""):
        certo = SEM_ACENTO.get(pal.lower())
        if certo:
            achados.append((pal, certo))
    return achados


@pytest.mark.parametrize("nome", sorted(templates.CATALOGO))
def test_corpo_de_template_sem_erro_de_acento(nome):
    if nome in CONGELADOS_NA_META:
        pytest.skip("congelado: mudar exige nova aprovação da Meta")
    erros = _erros(templates.CATALOGO[nome].corpo)
    assert not erros, "%s: %s" % (nome, erros)


@pytest.mark.parametrize("nome", sorted(templates.CATALOGO))
def test_rotulo_do_painel_sem_erro_de_acento(nome):
    """O rótulo é o que o Kevin lê na hora de escolher o que disparar."""
    erros = _erros(templates.CATALOGO[nome].rotulo)
    assert not erros, "%s: %s" % (nome, erros)


COMANDOS = ["ver tudo", "assinar", "planos", "meus dados", "quero começar",
            "quero comecar", "ajuda", "parar"]


@pytest.mark.parametrize("comando", COMANDOS)
def test_resposta_de_comando_sem_erro_de_acento(comando, usuario):
    resp = wa_bot._handle_commands(usuario, usuario["telefone"], comando)
    if not resp:
        pytest.skip("comando sem resposta direta")
    erros = _erros(resp)
    assert not erros, "%r responde com %s\n%s" % (comando, erros, resp[:200])


def test_botoes_do_motor_sem_erro_de_acento():
    for kind, botoes in wa_bot.BOTOES_POR_KIND.items():
        for b in botoes:
            assert not _erros(b), (kind, b)
