# -*- coding: utf-8 -*-
"""O UNICO MOMENTO DE CONVERSAO DO PRODUTO NAO PODE SAIR QUEBRADO.

`PAYMENT_LINK` vinha de env var com default "https://SEU-LINK-DE-PAGAMENTO".
Se a variavel nao estivesse configurada na VPS, quem respondesse "assinar" —
a pessoa mais valiosa que esse bot encontra — recebia o placeholder literal.
Falha silenciosa no lugar exato onde ela custa mais caro.
"""
import re

import pytest

import wa_bot


def _resposta_assinar(usuario, comando="assinar"):
    return wa_bot._handle_commands(usuario, usuario["telefone"], comando)


@pytest.mark.parametrize("comando", ["assinar", "planos", "quero assinar",
                                     "pagar"])
def test_todo_comando_de_assinatura_devolve_link_real(usuario, comando):
    txt = _resposta_assinar(usuario, comando) or ""
    assert "SEU-LINK" not in txt, (
        "placeholder de configuracao entregue a um cliente: %r" % (txt,))
    assert "mpago.la" in txt or "mercadopago" in txt, (
        "resposta de assinatura sem link de pagamento: %r" % (txt,))


def test_o_preco_mensal_aparece(usuario):
    txt = _resposta_assinar(usuario) or ""
    assert "19,90" in txt, txt


def test_link_nunca_sai_com_espaco_ou_quebra(usuario):
    """Link colado num texto do WhatsApp so vira clicavel se estiver limpo."""
    txt = _resposta_assinar(usuario) or ""
    for url in re.findall(r"https?://\S+", txt):
        assert url == url.strip(), url
        assert " " not in url, url
