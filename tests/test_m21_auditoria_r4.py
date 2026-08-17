"""Rodada 4 do M2.1 — o par que faltava: o MESMO boleto em dois estados.

O auditor pediu exatamente isto: o boleto limpo e o mesmo boleto com carimbo
de autenticação e vencimento no futuro. Um tem que ser pendente e o outro
concluído. É o teste que separa "documento de cobrança" de "documento de
cobrança já pago", que era o buraco que sobrou no invariante.
"""
import datetime as _dt

import pytest

import boleto
import db
import tempo
import wa_bot
from conftest import TELEFONE


def _futuro(dias=35):
    return (tempo.hoje() + _dt.timedelta(days=dias)).strftime("%d/%m/%Y")


def _passado(dias=4):
    return (tempo.hoje() - _dt.timedelta(days=dias)).strftime("%d/%m/%Y")


SABESP_LIMPO = """Boleto Ficha de Compensacao. Beneficiario: Companhia de Saneamento Basico
Recibo do Pagador
Vencimento {venc}
Valor do Documento R$ 92,10"""

SABESP_AUTENTICADO = SABESP_LIMPO + """
Autenticacao mecanica: 1234 5678 BANCO 341"""


def test_o_mesmo_boleto_em_dois_estados():
    """O par que o auditor pediu. Um documento, dois estados, dois destinos."""
    limpo = boleto.extrair(SABESP_LIMPO.format(venc=_futuro()))
    pago = boleto.extrair(SABESP_AUTENTICADO.format(venc=_futuro()))
    assert limpo["status_sugerido"] == "pendente", limpo
    assert pago["status_sugerido"] == "concluido", (
        f"boleto com carimbo do banco entrou como cobranca: {pago}")


# --- P0-14: a regex morta --------------------------------------------------

@pytest.mark.parametrize("carimbo", [
    "Autenticacao mecanica: 1234",
    "autenticação mecânica 9988",
    "AUTENTICACAO MECANICA 341",
    "Autenticacao Mec. 12",
])
def test_autenticacao_mecanica_casa(carimbo):
    """`autentica[çc][ãa]o\\s+mec\\b` nunca casava com "mecânica" — a única
    forma em que a expressão existe em papel."""
    assert boleto._AUTENTICACAO_RE.search(carimbo), carimbo


def test_carimbo_ganha_do_canhoto():
    """Todo boleto autenticado no caixa tem o canhoto. Se o canhoto vencer,
    o marcador de pagamento e inalcancavel por construcao."""
    texto = (f"Boleto Ficha de Compensacao. Recibo do Pagador. "
             f"Beneficiario: Enel. Vencimento {_futuro()}. "
             f"Valor do Documento R$ 187,45. Autenticacao mecanica: 998877")
    assert boleto.extrair(texto)["status_sugerido"] == "concluido"


# --- P0-13: pago ANTES do vencimento (o normal) ---------------------------

def test_comprovante_de_titulo_pago_adiantado():
    """Pagar no dia 12 uma conta que vence no dia 20 e fotografar o
    comprovante na hora e o comportamento NORMAL."""
    texto = (f"COMPROVANTE DE PAGAMENTO DE TITULO\n"
             f"Pagamento efetuado em {_passado(4)}\n"
             f"Cedente: ENEL DISTRIBUICAO SAO PAULO\n"
             f"Vencimento do titulo: {_futuro(35)}\n"
             f"Valor Pago: R$ 187,45")
    d = boleto.extrair(texto)
    assert d["status_sugerido"] == "concluido", (
        f"o bot vai cobrar no dia 19 uma conta paga no dia 12: {d}")


def test_data_do_item_e_a_do_pagamento():
    """Guardar o vencimento num recibo e registrar no historico uma data em
    que nada aconteceu."""
    pago = _passado(4)
    texto = (f"COMPROVANTE DE PAGAMENTO DE TITULO. Pagamento efetuado em "
             f"{pago}. Cedente: ENEL. Vencimento do titulo: {_futuro(35)}. "
             f"Valor Pago: R$ 187,45")
    d = boleto.extrair(texto)
    esperado = "-".join(reversed(pago.split("/")))
    assert d["data_vencimento"] == esperado, (
        f"guardou {d['data_vencimento']} em vez da data do pagamento "
        f"({esperado})")


def test_no_fluxo_o_pago_adiantado_nao_vira_cobranca(usuario, monkeypatch):
    import canal
    texto = (f"COMPROVANTE DE PAGAMENTO DE TITULO. Pagamento efetuado em "
             f"{_passado(4)}. Cedente: ENEL DISTRIBUICAO. Vencimento do "
             f"titulo: {_futuro(35)}. Valor Pago: R$ 187,45")
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: texto)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": "ADIANT"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}
    wa_bot.handle_incoming(payload)
    itens = db.list_items(usuario["id"])
    assert itens and itens[0]["status"] == "concluido", itens


def test_pagamento_com_data_futura_nao_e_consumado():
    """"pago em <data futura>" nao existe — se aparecer, e leitura errada e
    o documento continua sendo cobranca."""
    texto = (f"Boleto Enel. Recibo do Pagador. Pago em {_futuro(20)}. "
             f"Vencimento {_futuro(20)}. Valor do Documento R$ 100,00")
    assert boleto.extrair(texto)["status_sugerido"] == "pendente"


# --- P1-17: "COMPROVANTE" como titulo do documento ------------------------

def test_comprovante_no_cabecalho_vale():
    texto = ("COMPROVANTE\nCedente: ENEL DISTRIBUICAO\n"
             "Nosso Numero 12345678901\nData 12/08/2026\n"
             "Valor Pago R$ 187,45\nAutenticacao: X9Y8Z7")
    assert boleto.extrair(texto)["status_sugerido"] == "concluido"


def test_comprovante_no_meio_do_carne_nao_vale():
    """"Comprovante de entrega" impresso no corpo de um carne nao torna o
    carne um recibo."""
    texto = (f"Carne de mensalidade. Ficha de Compensacao. "
             f"Beneficiario: Escola Alfa. Comprovante de entrega em anexo. "
             f"Vencimento {_futuro(30)}. Valor do Documento R$ 800,00")
    assert boleto.extrair(texto)["status_sugerido"] == "pendente"


# --- P1-18: nome que TERMINA em palavra-rotulo ---------------------------

@pytest.mark.parametrize("empresa", [
    "Companhia Energetica Total", "Grupo Recibo", "Supermercado Total",
    "Centro de Pagamento", "Total Energia S.A.", "Data Center Brasil",
    "Valor Seguros S.A.", "Enel Distribuicao Sao Paulo",
])
def test_nome_inteiro_preservado(empresa):
    texto = (f"Boleto Ficha de Compensacao. Beneficiario: {empresa}. "
             f"Vencimento 20/09/2026. Valor do Documento R$ 100,00")
    d = boleto.extrair(texto)
    benef = (d.get("beneficiario") or "").rstrip(".")
    assert benef.lower() == empresa.rstrip(".").lower(), (
        f"'{empresa}' virou {benef!r}")
