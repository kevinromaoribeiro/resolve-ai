"""Rodada 5 do M2.1 — o guardrail caiu num conserto de FORMATAÇÃO DE NOME.

Passou quatro rodadas intacto e foi derrubado pela janela crua que eu abri
pra consertar truncamento de nome de empresa. Os casos individuais estão
aqui; o invariante que varre todos os fixtures achatados em uma linha está
em `test_m21_invariantes.py` — é ele que teria pego isto antes.
"""
import datetime as _dt

import pytest

import boleto
import db
import tempo
import wa_bot
from conftest import TELEFONE

VAZAMENTO = ("Boleto Ficha de Compensacao Beneficiario: ENEL SP "
             "34191.79001 01043.510047 91020.150008 4 98110000018745 "
             "Valor do Documento R$ 187,45 Vencimento 20/08/2026")


def _futuro(dias=35):
    return (tempo.hoje() + _dt.timedelta(days=dias)).strftime("%d/%m/%Y")


# --- P0-15: o codigo de pagamento no nome -------------------------------

def test_linha_digitavel_na_mesma_linha_do_beneficiario():
    d = boleto.extrair(VAZAMENTO)
    assert d["beneficiario"] == "ENEL SP", d["beneficiario"]


def test_item_gravado_sem_codigo(usuario, monkeypatch):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: VAZAMENTO)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": "VAZ"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}
    reply = (wa_bot.handle_incoming(payload) or {}).get("text", "")
    assert "34191" not in reply and "98110000018745" not in reply, reply
    itens = db.list_items(usuario["id"])
    assert itens and "34191" not in itens[0]["descricao"], itens


@pytest.mark.parametrize("nome", [
    "SABESP", "ENEL SP", "VIVO", "Companhia Energetica Total",
    "Grupo Recibo", "Total Energia S.A.",
])
def test_nome_legitimo_continua_inteiro(nome):
    texto = (f"Boleto Ficha de Compensacao. Beneficiario: {nome}. "
             f"Vencimento 20/09/2026. Valor do Documento R$ 100,00")
    benef = (boleto.extrair(texto).get("beneficiario") or "").rstrip(".")
    assert benef.lower() == nome.rstrip(".").lower(), benef


# --- P0-16: evento datado nao e pagamento -------------------------------

@pytest.mark.parametrize("evento", [
    "cadastro efetuado em 01/08/2026",
    "contrato efetuado em 05/01/2026",
    "reajuste efetuado em 01/07/2026",
    "vistoria efetuada em 10/07/2026",
])
def test_evento_datado_nao_marca_como_pago(evento):
    texto = (f"Boleto Ficha de Compensacao. Recibo do Pagador. "
             f"Beneficiario: Academia Alfa. {evento}. "
             f"Vencimento {_futuro()}. Valor do Documento R$ 129,90")
    d = boleto.extrair(texto)
    assert d["status_sugerido"] == "pendente", (
        f"'{evento}' marcou a conta como paga: {d}")


def test_carimbo_continua_provando_sozinho():
    """A trava nova (data so vale com marca de recibo) nao pode enfraquecer
    o carimbo, que era o conserto do P0-14."""
    texto = (f"Boleto Ficha de Compensacao. Recibo do Pagador. "
             f"Beneficiario: Sabesp. Vencimento {_futuro()}. "
             f"Valor do Documento R$ 92,10. Autenticacao mecanica: 4455")
    assert boleto.extrair(texto)["status_sugerido"] == "concluido"


def test_data_de_pagamento_com_marca_de_recibo_vale():
    ontem = (tempo.hoje() - _dt.timedelta(days=1)).strftime("%d/%m/%Y")
    texto = (f"COMPROVANTE DE PAGAMENTO DE TITULO. Pagamento efetuado em "
             f"{ontem}. Cedente: Sabesp. Vencimento do titulo: {_futuro()}. "
             f"Valor Pago R$ 92,10")
    assert boleto.extrair(texto)["status_sugerido"] == "concluido"


# --- P1-19: a mascara de historico -------------------------------------

def test_historico_nao_come_o_vencimento():
    d = boleto.extrair("Boleto do condominio, historico da parcela anterior, "
                       "vencimento 10/09/2026, valor do documento R$ 450,00")
    assert d["data_vencimento"] == "2026-09-10", d


def test_parcela_anterior_continua_ignorada():
    d = boleto.extrair("BOLETO Ficha de Compensacao. Beneficiario: Condominio. "
                       "PARCELA ANTERIOR PAGO EM 10/08/2026. "
                       f"VENCIMENTO {_futuro()}. VALOR DO DOCUMENTO R$ 450,00")
    assert d["status_sugerido"] == "pendente", d


# --- P1-20: comprovante do que ja esta na lista da baixa ----------------

def test_comprovante_quita_o_pendente_em_vez_de_criar_irmao(usuario,
                                                            monkeypatch):
    """O fluxo que a propria mensagem convida: guardar a conta e depois
    mandar o comprovante."""
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")

    venc = _futuro()
    conta = (f"Boleto Ficha de Compensacao. Beneficiario: SABESP. "
             f"Vencimento {venc}. Valor do Documento R$ 92,10")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: conta)
    wa_bot.handle_incoming({"data": {"key": {
        "remoteJid": f"{TELEFONE}@s.whatsapp.net", "fromMe": False,
        "id": "C1"}, "pushName": "Kevin",
        "message": {"imageMessage": {"caption": ""}}}})

    # O vencimento do titulo e a chave: sem ele o bot NAO fecha nada (ver
    # test_sem_vencimento_do_titulo_nao_fecha, abaixo).
    ontem = (tempo.hoje() - _dt.timedelta(days=1)).strftime("%d/%m/%Y")
    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em {ontem}. "
                   f"Beneficiario: SABESP. Vencimento do titulo: {venc}. "
                   f"Valor Pago R$ 92,10")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: comprovante)
    reply = (wa_bot.handle_incoming({"data": {"key": {
        "remoteJid": f"{TELEFONE}@s.whatsapp.net", "fromMe": False,
        "id": "C2"}, "pushName": "Kevin",
        "message": {"imageMessage": {"caption": ""}}}}) or {}).get("text", "")

    itens = db.list_items(usuario["id"])
    assert len(itens) == 1, (
        f"gasto do mes contado duas vezes, e o lembrete da conta paga "
        f"continua armado: {itens}")
    assert itens[0]["status"] == "concluido", itens
    assert "baixa" in reply.lower(), reply


def test_comprovante_de_outra_conta_nao_da_baixa_errada(usuario, monkeypatch):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    conta = (f"Boleto Ficha de Compensacao. Beneficiario: SABESP. "
             f"Vencimento {_futuro()}. Valor do Documento R$ 92,10")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: conta)
    wa_bot.handle_incoming({"data": {"key": {
        "remoteJid": f"{TELEFONE}@s.whatsapp.net", "fromMe": False,
        "id": "O1"}, "pushName": "Kevin",
        "message": {"imageMessage": {"caption": ""}}}})

    ontem = (tempo.hoje() - _dt.timedelta(days=1)).strftime("%d/%m/%Y")
    outro = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em {ontem}. "
             f"Beneficiario: ENEL. Valor Pago R$ 92,10")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: outro)
    wa_bot.handle_incoming({"data": {"key": {
        "remoteJid": f"{TELEFONE}@s.whatsapp.net", "fromMe": False,
        "id": "O2"}, "pushName": "Kevin",
        "message": {"imageMessage": {"caption": ""}}}})

    pendentes = db.list_items(usuario["id"], status="pendente")
    assert pendentes and "SABESP" in pendentes[0]["descricao"], (
        f"deu baixa na conta errada, so porque o valor batia: "
        f"{db.list_items(usuario['id'])}")
