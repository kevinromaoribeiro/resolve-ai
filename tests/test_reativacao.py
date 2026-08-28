# -*- coding: utf-8 -*-
"""O TEMPLATE DE REATIVACAO, QUE JA ESTAVA APROVADO NA META.

O Kevin criou `reativar_boas_vindas` em 05/08/2026 e ele estava ATIVO na
Meta — mas nao existia no catalogo do repo, entao o codigo nao sabia manda-lo
e o painel nao o oferecia. Template aprovado que o produto nao usa e dinheiro
parado: e a unica mensagem que temos capaz de alcancar quem esfriou.

Ele resolve tres coisas de uma vez: pede desculpa pelo apagao, diz que os 14
dias estao valendo, e ENSINA a usar com dois exemplos concretos.
"""
import pytest

import templates
import wa_bot


# SEM o prefixo `resolveai_`: o nome no catalogo TEM que ser identico ao
# aprovado na Meta, porque e ele que viaja na chamada. Inventar um alias aqui
# faria todo envio falhar com "template nao existe".
NOME = "reativar_boas_vindas"


def test_esta_no_catalogo():
    assert NOME in templates.CATALOGO, sorted(templates.CATALOGO)


def test_o_nome_bate_com_o_aprovado_na_meta():
    """O nome e o contrato: se divergir, a Meta recusa o envio."""
    assert templates.CATALOGO[NOME].nome == "reativar_boas_vindas"


def test_e_marketing_e_esta_autorizado():
    from tests.test_m26_utilidade_de_verdade import MARKETING_AUTORIZADO
    t = templates.CATALOGO[NOME]
    assert t.categoria == "MARKETING"
    assert NOME in MARKETING_AUTORIZADO, (
        "marketing novo sem entrar na lista documentada")


def test_uma_variavel_so_o_primeiro_nome():
    assert templates.CATALOGO[NOME].variaveis == ["primeiro_nome"]


def test_o_painel_oferece_ele():
    nomes = [t["nome"] for t in wa_bot._templates_com_rotulo()]
    assert NOME in nomes, nomes


def test_quem_clica_em_comecar_recebe_o_guia(usuario):
    """O botao do template e "Quero comecar".

    Sem tratar isso, o clique cai no LLM e pode virar "nao entendi" — para
    quem escolheu o caminho mais facil, no primeiro contato depois de semanas
    sumido. E o pior momento possivel pra parecer quebrado.
    """
    for texto in ("Quero comecar", "Quero começar", "quero comecar"):
        resp = wa_bot._handle_commands(usuario, usuario["telefone"], texto)
        assert resp, "sem resposta pra %r" % texto
        # tem que ENSINAR: exemplo concreto, nao so "manda ai"
        assert "vence" in resp.lower() or "dia" in resp.lower(), resp


def test_o_botao_do_template_e_tratado():
    """Fecha o ciclo: todo botao declarado tem tratamento."""
    for b in templates.CATALOGO[NOME].botoes:
        assert wa_bot.entende_comando(b), (
            "botao %r do template nao vira comando" % b)
