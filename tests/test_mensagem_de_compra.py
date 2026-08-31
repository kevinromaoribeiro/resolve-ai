# -*- coding: utf-8 -*-
"""A mensagem de compra (M7.2).

E o UNICO momento de conversao que o produto tem: a pessoa digitou
"assinar". Tudo aqui e medido com numero real — numero inventado numa
mensagem de venda custa a confianca inteira, e este arquivo existe pra que
isso nao aconteca por descuido de digitacao.
"""
import datetime as _dt

import db
import wa_bot
from conftest import TELEFONE, responder


def _itens(usuario, quantos):
    for i in range(quantos):
        db.add_item(user_id=usuario["id"], tipo="despesa",
                    categoria="Contas", descricao="conta %d" % i,
                    valor_reais=100.0,
                    data_vencimento=(_dt.date(2026, 9, 1) +
                                     _dt.timedelta(days=i)).isoformat(),
                    status="pendente")


def test_os_dois_links_saem(usuario):
    r = responder("assinar")
    assert wa_bot.PAYMENT_LINK in r
    assert wa_bot.PAYMENT_LINK_ANUAL in r


def test_diz_o_que_a_pessoa_ganhou_com_numero_real(usuario):
    _itens(usuario, 3)
    r = responder("assinar")
    assert "*3* compromissos seus" in r, r


def test_um_item_so_fala_no_singular(usuario):
    _itens(usuario, 1)
    r = responder("assinar")
    assert "*1* compromisso seu" in r, r


def test_sem_item_nenhum_a_linha_some(usuario):
    """"Guardei 0 compromissos" e argumento CONTRA a assinatura."""
    r = responder("assinar")
    assert "guardei" not in r.lower(), r
    assert wa_bot.PAYMENT_LINK in r, "o link tem que sair de qualquer jeito"


def test_banco_fora_nao_derruba_a_venda(usuario, monkeypatch):
    """Perder a contagem e ruim. Perder a VENDA e inaceitavel."""
    def explode(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, "list_items", explode)
    r = responder("assinar")
    assert wa_bot.PAYMENT_LINK in r, r


def test_a_economia_do_anual_e_derivada_dos_precos(usuario, monkeypatch):
    """Ja aconteceu de R$ 149 ser anunciado como "R$ 12,40/mes" (da 12,42).
    Preco escrito a mao sai de sincronia; preco derivado, nao."""
    monkeypatch.setattr(wa_bot, "PRECO_MENSAL", 10.0)
    monkeypatch.setattr(wa_bot, "PRECO_ANUAL", 100.0)
    r = responder("assinar")
    assert "R$ 8,33/mês" in r, r          # 100/12
    assert "economiza R$ 20,00" in r, r   # 10*12 - 100
    assert "2 meses de graça" in r, r


def test_anual_sem_vantagem_nao_inventa_desconto(usuario, monkeypatch):
    monkeypatch.setattr(wa_bot, "PRECO_MENSAL", 10.0)
    monkeypatch.setattr(wa_bot, "PRECO_ANUAL", 120.0)
    r = responder("assinar")
    assert "economiza" not in r, r


def test_economia_menor_que_um_mes_nao_fala_em_meses_gratis(usuario,
                                                            monkeypatch):
    """"0 meses grátis" e pior que nao dizer nada."""
    monkeypatch.setattr(wa_bot, "PRECO_MENSAL", 10.0)
    monkeypatch.setattr(wa_bot, "PRECO_ANUAL", 115.0)
    r = responder("assinar")
    assert "economiza R$ 5,00" in r, r
    assert "de graça" not in r, r


def test_o_pedido_entra_na_fila_de_aprovacao(usuario):
    """Pedir o link e pagar sao eventos diferentes: sem este registro, o
    cliente paga e fica esperando uma ativacao que ninguem lembrou de dar."""
    responder("assinar")
    assert db.dispatched_ever("link-pagamento", usuario["id"])


def test_a_mensagem_cabe_no_whatsapp(usuario):
    _itens(usuario, 12)
    r = responder("assinar")
    assert len(r) < 1024, len(r)


def test_diz_como_sair(usuario):
    """Assinatura recorrente sem saida visivel vira pedido de reembolso."""
    r = responder("assinar")
    assert "cancelar" in r.lower()
    assert "renova sozinho" in r.lower()
