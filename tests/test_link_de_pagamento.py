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


# ---------------------------------------------------------------------------
# o numero do bot — o mesmo furo em dois lugares (28/08/2026)
# ---------------------------------------------------------------------------

def test_bot_phone_tem_numero_de_verdade():
    """Sem ele o convite do recado sai sem link e ninguem vira usuario."""
    import re as _re
    assert wa_bot.BOT_PHONE, "BOT_PHONE vazio"
    assert _re.fullmatch(r"55\d{10,11}", wa_bot.BOT_PHONE), wa_bot.BOT_PHONE


def test_o_recado_pra_terceiro_leva_o_convite(usuario):
    """A promessa 'avisa minha esposa' so vira aquisicao com o link."""
    txt = wa_bot._link_delegacao("pagar a conta de luz amanha", "esposa")
    assert "wa.me/" in txt
    assert wa_bot.BOT_PHONE in txt, txt
    assert "SEU-" not in txt and "000000" not in txt


def test_a_landing_nao_aponta_pra_numero_falso():
    """A landing ficou com placeholder por semanas: todo CTA levava a um
    WhatsApp inexistente e ela nao convertia ninguem."""
    import io
    import re as _re
    html = io.open("landing.html", encoding="utf-8").read()
    m = _re.search(r'WHATSAPP_NUMBER\s*=\s*"(\d+)"', html)
    assert m, "nao achei WHATSAPP_NUMBER na landing"
    numero = m.group(1)
    assert numero != "5511000000000", "landing com numero placeholder"
    assert _re.fullmatch(r"55\d{10,11}", numero), numero
    assert numero == wa_bot.BOT_PHONE, (
        "landing (%s) e bot (%s) apontam pra numeros diferentes"
        % (numero, wa_bot.BOT_PHONE))
