"""Os 7 P0 da auditoria do M2.1, contra OCR de boleto REAL.

Todos falham contra a primeira versão do extrator. O fio que liga quase
todos: boleto de verdade tem rótulo capitalizado, quatro datas, cinco
valores e "Recibo do Pagador" no canhoto.
"""
import pytest

import boleto
import db
import wa_bot
from conftest import TELEFONE, responder
from fixtures_boleto import (CARTAO_MAIUSCULO, CONDOMINIO, COMPROVANTE_BANCO,
                             COMPROVANTE_PIX, ENEL, NAO_SAO_CONTA,
                             SABESP_SEM_CAUDA, BOLETO_ESPACO_NO_VALOR,
                             BOLETO_DATA_POR_EXTENSO)


def _foto(monkeypatch, texto, msg_id="IMG"):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: texto)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": msg_id},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}
    return (wa_bot.handle_incoming(payload) or {}).get("text", "")


# --- P0-5: o valor certo, entre cinco --------------------------------------

@pytest.mark.parametrize("texto,esperado", [
    (ENEL, 187.45),                 # tem desconto 0,50 e juros 0,00 antes
    (SABESP_SEM_CAUDA, 92.10),      # tem tarifa 7,44 antes
    (CARTAO_MAIUSCULO, 1234.56),    # tem pagamento minimo 89,00 antes
    (CONDOMINIO, 450.00),
])
def test_pega_o_valor_do_documento_nao_o_primeiro(texto, esperado):
    d = boleto.extrair(texto)
    assert d and d["valor_reais"] == esperado, (
        f"pegou {d and d['valor_reais']} em vez de {esperado}")


def test_nao_pega_desconto_zerado():
    d = boleto.extrair("(-) Desconto 0,00 (=) Valor do Documento R$ 55,00 "
                       "Vencimento 20/08/2026 Boleto")
    assert d["valor_reais"] == 55.00


# --- P0-6: a data certa, entre quatro --------------------------------------

@pytest.mark.parametrize("texto,esperado", [
    (ENEL, "2026-08-20"),           # documento 05/08, processamento 06/08
    (CONDOMINIO, "2026-09-10"),     # documento 01/09, parcela anterior 10/08
    (SABESP_SEM_CAUDA, "2026-08-25"),   # emissao 02/08
    (CARTAO_MAIUSCULO, "2026-09-15"),
])
def test_pega_vencimento_nao_emissao(texto, esperado):
    d = boleto.extrair(texto)
    assert d and d["data_vencimento"] == esperado, (
        f"pegou {d and d['data_vencimento']} em vez de {esperado} — "
        f"lembrete no dia errado e o item nasce vencido")


# --- P0-2: beneficiario com rotulo capitalizado ----------------------------

@pytest.mark.parametrize("texto,pedaco", [
    (ENEL, "enel"),
    (CONDOMINIO, "condominio"),
    (SABESP_SEM_CAUDA, "saneamento"),
    (CARTAO_MAIUSCULO, "nu pagamentos"),
])
def test_le_beneficiario_em_boleto_real(texto, pedaco):
    d = boleto.extrair(texto)
    assert d and d["beneficiario"], f"beneficiario vazio: {d}"
    assert pedaco in d["beneficiario"].lower(), d["beneficiario"]


def test_beneficiario_nao_engole_digito_nem_linha():
    d = boleto.extrair(ENEL)
    b = d["beneficiario"]
    assert "\n" not in b and not any(c.isdigit() for c in b), repr(b)


# --- P0-3: "Recibo do Pagador" nao faz boleto virar pago -------------------

@pytest.mark.parametrize("texto", [ENEL, CONDOMINIO])
def test_boleto_com_canhoto_de_recibo_continua_pendente(texto):
    d = boleto.extrair(texto)
    assert d["tipo"] == "boleto", d
    assert d["status_sugerido"] == "pendente", (
        "a conta entrou como paga: sai da lista e vence sem aviso")


@pytest.mark.parametrize("texto", [COMPROVANTE_PIX, COMPROVANTE_BANCO])
def test_comprovante_de_verdade_continua_pago(texto):
    d = boleto.extrair(texto)
    assert d["status_sugerido"] == "concluido", d


# --- P0-4: foto nao-financeira nao vira conta ------------------------------

@pytest.mark.parametrize("nome", sorted(NAO_SAO_CONTA))
def test_nao_inventa_conta(nome):
    d = boleto.extrair(NAO_SAO_CONTA[nome])
    assert d is None, f"{nome} virou conta: {d}"


@pytest.mark.parametrize("nome", sorted(NAO_SAO_CONTA))
def test_nao_inventa_item_no_fluxo(usuario, monkeypatch, nome):
    _foto(monkeypatch, NAO_SAO_CONTA[nome], msg_id=f"IMG_{nome}")
    itens = [i for i in db.list_items(usuario["id"])]
    assert not itens, f"{nome} virou item: {itens}"


# --- P0-1 e P1-1: dedup nao pode fundir contas diferentes ------------------

def test_contas_de_empresas_diferentes_sao_itens_diferentes(usuario,
                                                            monkeypatch):
    _foto(monkeypatch, ENEL, "I1")
    _foto(monkeypatch, SABESP_SEM_CAUDA, "I2")
    _foto(monkeypatch, CARTAO_MAIUSCULO, "I3")
    assert len(db.list_items(usuario["id"])) == 3, (
        f"contas diferentes viraram uma so: {db.list_items(usuario['id'])}")


def test_mesma_empresa_meses_diferentes(usuario, monkeypatch):
    _foto(monkeypatch, ENEL, "I1")
    setembro = ENEL.replace("20/08/2026", "20/09/2026").replace(
        "187,45", "342,10")
    _foto(monkeypatch, setembro, "I2")
    assert len(db.list_items(usuario["id"])) == 2, (
        "Enel de agosto e de setembro viraram uma conta so")


def test_foto_repetida_nao_duplica(usuario, monkeypatch):
    _foto(monkeypatch, ENEL, "I1")
    _foto(monkeypatch, ENEL, "I2")
    assert len(db.list_items(usuario["id"])) == 1


def test_comprovante_repetido_nao_dobra_o_gasto(usuario, monkeypatch):
    _foto(monkeypatch, COMPROVANTE_PIX, "C1")
    _foto(monkeypatch, COMPROVANTE_PIX, "C2")
    itens = db.list_items(usuario["id"])
    assert len(itens) == 1, f"o gasto do mes dobrou: {itens}"


# --- P1-2: "essa eu ja tenho" tem que falar do que ESTA guardado -----------

def test_resposta_de_duplicata_mostra_o_item_guardado(usuario, monkeypatch):
    _foto(monkeypatch, ENEL, "I1")
    reply = _foto(monkeypatch, ENEL, "I2")
    assert "187,45" in reply, f"mostrou dado da foto nova, nao do banco: {reply}"


# --- P0-7: a frase sugerida tem que funcionar de verdade -------------------

@pytest.mark.parametrize("texto", [ENEL, CONDOMINIO, CARTAO_MAIUSCULO,
                                   SABESP_SEM_CAUDA])
def test_frase_sugerida_da_baixa_de_verdade(usuario, monkeypatch, texto):
    """Beneficiario de 4+ palavras quebrava a cauda do _BAIXA_RE: o bot
    respondia 'Registrado' (item fantasma) e a conta real ficava pendente."""
    import re

    import motor_v8
    monkeypatch.setattr(motor_v8, "route", lambda *a, **kw: None)
    reply = _foto(monkeypatch, texto, "IF")
    m = re.search(r'_"([^"]+)"_', reply)
    assert m, f"a mensagem nao sugere frase: {reply!r}"

    responder(m.group(1))

    itens = db.list_items(usuario["id"])
    assert len(itens) == 1, f"criou item fantasma: {itens}"
    assert itens[0]["status"] == "concluido", (
        f"a frase {m.group(1)!r} nao deu baixa: {itens}")


# --- P1-3: a legenda do usuario nao pode ser ignorada ---------------------

def test_legenda_dizendo_que_ja_pagou(usuario, monkeypatch):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: ENEL)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": "IMG_CAP"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {
                            "caption": "essa eu ja paguei ontem"}}}}
    wa_bot.handle_incoming(payload)
    itens = db.list_items(usuario["id"])
    assert itens and itens[0]["status"] == "concluido", (
        f"a pessoa disse que pagou e o bot vai cobrar mesmo assim: {itens}")


# --- P1-5: falso negativo de OCR ------------------------------------------

def test_valor_com_espaco_na_centena():
    d = boleto.extrair(BOLETO_ESPACO_NO_VALOR)
    assert d and d["valor_reais"] == 119.90, d


def test_data_por_extenso():
    d = boleto.extrair(BOLETO_DATA_POR_EXTENSO)
    assert d and d["data_vencimento"] == "2026-09-20", d


# --- P1-4: OCR cru nao vaza codigo pro item -------------------------------

def test_ocr_sem_data_nao_leva_linha_digitavel_pro_item(usuario, monkeypatch):
    """Quando o extrator recusa (sem data), o OCR inteiro seguia pro menu e
    a linha digitavel virava descricao do item."""
    sem_venc = ENEL.replace("Vencimento 20/08/2026", "").replace(
        "vencimento=20/08/2026;", "")
    _foto(monkeypatch, sem_venc, "IMG_SV")
    tudo = " ".join(i["descricao"] for i in db.list_items(usuario["id"]))
    assert "03399" not in tudo and "12345678901234" not in tudo, (
        f"codigo de pagamento virou item: {tudo!r}")


# --- P2: formatacao -------------------------------------------------------

def test_valor_grande_tem_separador_de_milhar(usuario, monkeypatch):
    reply = _foto(monkeypatch, CARTAO_MAIUSCULO, "IMG_MIL")
    assert "1.234,56" in reply, reply
