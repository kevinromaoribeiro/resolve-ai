"""Rodada 3 do M2.1 — o TESTE CRUZADO, e o invariante que quebra o ciclo.

Três rodadas de auditoria trocaram de lado no par boleto/comprovante: lista
de palavras marcava boleto como pago, depois marcava comprovante como a
pagar, depois de novo. Cada rodada testava a direção que tinha acabado de
consertar.

O motivo é estrutural: "cedente", "nosso número", "recibo" e "pagamento"
existem NOS DOIS documentos. O que os separa não é vocabulário, é uma
propriedade que só um deles pode ter — **recibo não tem data a vencer**.

Por isso o teste aqui é cruzado: cada boleto ganha uma frase de pagamento
enxertada, e cada comprovante ganha um campo de boleto enxertado.
"""
import datetime as _dt

import pytest

import boleto
import db
import tempo
import wa_bot
from conftest import TELEFONE
from fixtures_boleto import CONDOMINIO, ENEL, SABESP_SEM_CAUDA
from fixtures_boleto2 import (COMPROVANTE_DE_TITULO,
                              COMPROVANTE_DE_TITULO_COM_CAUDA)

FRASES_DE_PAGAMENTO = [
    "Parcela anterior: Valor Pago R$ 100,00",
    "Comprovante de entrega em anexo",
    "Recibo de pagamento do mes anterior",
    "Historico: pagamento realizado no mes anterior",
]

CAMPOS_DE_BOLETO = [
    "Cedente: EMPRESA X",
    "Nosso Numero: 12345678901",
    "Linha Digitavel: 03399.63290 64000.000006",
    "Codigo de barras 03399632906400000000600125201020",
]


def _futuro(dias=30):
    return (tempo.hoje() + _dt.timedelta(days=dias)).strftime("%d/%m/%Y")


# --- P0-11: boleto com frase de pagamento continua sendo boleto ----------

@pytest.mark.parametrize("frase", FRASES_DE_PAGAMENTO)
@pytest.mark.parametrize("base", [ENEL, CONDOMINIO, SABESP_SEM_CAUDA])
def test_boleto_com_frase_de_pagamento_enxertada(base, frase):
    texto = base + "\n" + frase
    d = boleto.extrair(texto)
    assert d, "nao reconheceu o documento"
    assert d["status_sugerido"] == "pendente", (
        f"'{frase}' fez o boleto virar conta paga: some da lista e nenhum "
        f"lembrete dispara")


@pytest.mark.parametrize("campo", CAMPOS_DE_BOLETO)
def test_comprovante_com_campo_de_boleto_enxertado(campo):
    texto = COMPROVANTE_DE_TITULO + "\n" + campo
    d = boleto.extrair(texto)
    assert d and d["status_sugerido"] == "concluido", (
        f"'{campo}' fez o comprovante virar cobranca: o bot vai cobrar o "
        f"que a pessoa acabou de pagar")


def test_cauda_explicita_continua_decidindo():
    assert boleto.extrair(
        COMPROVANTE_DE_TITULO_COM_CAUDA)["tipo"] == "comprovante"


# --- O invariante: vencimento no futuro nao e comprovante ----------------

def test_vencimento_no_futuro_nunca_e_comprovante():
    """A regra que nao depende de vocabulario. Recibo nao tem data a vencer."""
    texto = (f"Comprovante de pagamento. Beneficiario: Condominio Parque. "
             f"Vencimento {_futuro(30)}. Valor do Documento R$ 190,00")
    d = boleto.extrair(texto)
    assert d["status_sugerido"] == "pendente", (
        f"documento com vencimento no futuro entrou como pago: {d}")


def test_comprovante_com_data_passada_continua_pago():
    passado = (tempo.hoje() - _dt.timedelta(days=5)).strftime("%d/%m/%Y")
    texto = (f"Comprovante de pagamento efetuado em {passado}. "
             f"Valor pago R$ 187,45. Beneficiario: Enel")
    d = boleto.extrair(texto)
    assert d["status_sugerido"] == "concluido", d


def test_no_fluxo_conta_futura_nao_entra_como_paga(usuario, monkeypatch):
    import canal
    texto = (f"Boleto. Recibo do Pagador. Beneficiario: Condominio Parque. "
             f"Recibo de pagamento. Vencimento {_futuro(35)}. "
             f"Valor do Documento R$ 190,00")
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: texto)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": "FUT"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}
    reply = (wa_bot.handle_incoming(payload) or {}).get("text", "")
    itens = db.list_items(usuario["id"])
    assert itens and itens[0]["status"] == "pendente", (
        f"a conta do mes que vem entrou como paga hoje: {itens} / {reply!r}")
    assert "pago em" not in reply


# --- P0-12: nome de empresa que PARECE rotulo ---------------------------

@pytest.mark.parametrize("empresa", [
    "Total Energia S.A.", "Recibo Verde Ltda", "Data Center Brasil",
    "Valor Seguros S.A.", "Central de Pagamento Digital",
    "Enel Distribuicao Sao Paulo",
])
def test_nome_de_empresa_nao_e_cortado(empresa):
    texto = (f"Boleto Ficha de Compensacao. Beneficiario: {empresa}. "
             f"Vencimento 20/09/2026. Valor do Documento R$ 187,45")
    d = boleto.extrair(texto)
    benef = (d.get("beneficiario") or "")
    assert benef, f"'{empresa}' virou beneficiario vazio -> 'conta a pagar'"
    assert empresa.split()[0].lower() in benef.lower(), (
        f"'{empresa}' virou {benef!r}")


def test_duas_contas_diferentes_tem_frases_diferentes(usuario, monkeypatch):
    """Descricao generica fazia duas contas ganharem a MESMA frase sugerida,
    e a frase criava item fantasma."""
    import canal
    import motor_v8
    monkeypatch.setattr(motor_v8, "route", lambda *a, **kw: None)
    frases = []
    for nome, valor in (("Total Energia S.A.", "187,45"),
                        ("Valor Seguros S.A.", "92,10")):
        texto = (f"Boleto Ficha de Compensacao. Beneficiario: {nome}. "
                 f"Vencimento 20/09/2026. Valor do Documento R$ {valor}")
        monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
        monkeypatch.setattr(wa_bot, "_read_image", lambda b64, t=texto: t)
        payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                    "fromMe": False, "id": f"D{valor}"},
                            "pushName": "Kevin",
                            "message": {"imageMessage": {"caption": ""}}}}
        reply = (wa_bot.handle_incoming(payload) or {}).get("text", "")
        import re
        frases.append(re.search(r'_"([^"]+)"_', reply).group(1))
    assert frases[0] != frases[1], f"as duas contas sugerem {frases[0]!r}"


# --- P1-14: o residuo das legendas --------------------------------------

@pytest.mark.parametrize("legenda", [
    "paguei? não, ainda não",
    "paguei a luz, essa aqui é a água",
    "essa conta é paga todo mês no débito automático",
    "não sei se paguei essa",
    "acho que não paguei",
])
def test_legenda_ambigua_nao_marca_como_paga(legenda):
    assert wa_bot._legenda_diz_que_pagou(legenda) is False, (
        f"'{legenda}' marcou a conta como paga")


@pytest.mark.parametrize("legenda", [
    "essa eu já paguei ontem", "paguei", "já paguei essa",
    "essa está paga", "quitei hoje de manhã",
    # o contraste depois da virgula SEM objeto e sobre a mesma conta
    "paguei, essa era a última", "quitei, essa fechou o mês",
])
def test_legenda_afirmativa_continua_valendo(legenda):
    assert wa_bot._legenda_diz_que_pagou(legenda) is True, legenda


# --- o custo do invariante: agendamento ---------------------------------

def test_pagamento_agendado_nao_vira_cobranca():
    """Agendamento tem data futura e JA foi resolvido pela pessoa. Cobrar
    quem agendou e cobrar duas vezes."""
    texto = (f"Comprovante de agendamento de pagamento. Pagamento agendado "
             f"para {_futuro(30)}. Valor pago R$ 187,45. Beneficiario: Enel")
    d = boleto.extrair(texto)
    assert d["status_sugerido"] == "concluido", (
        f"o bot vai cobrar um pagamento ja agendado: {d}")


def test_propaganda_de_agende_nao_vira_excecao():
    """Boleto imprime "agende seu pagamento" como propaganda. O imperativo
    nao pode virar salvo-conduto pra conta futura entrar como paga."""
    texto = (f"Comprovante de pagamento. Agende seu pagamento no app. "
             f"Vencimento {_futuro(20)}. Valor do Documento R$ 90,00")
    d = boleto.extrair(texto)
    assert d["status_sugerido"] == "pendente", d


# --- P1-15: o rotulo generico "valor" nao pode furar o veto fraco --------

@pytest.mark.parametrize("texto", [
    "Etiqueta de prateleira. Arroz 5kg. Valor R$ 24,90 a unidade. "
    "Vence 10/09/2026",
    "Promocao valida ate 30/08/2026. Valor da promocao R$ 45,00",
    "Recibo de estacionamento Shopping. Valor R$ 12,00. Data 16/08/2026",
])
def test_valor_generico_nao_torna_qualquer_coisa_conta(texto):
    assert boleto.extrair(texto) is None, f"virou conta: {texto[:40]!r}"


def test_boleto_de_uma_linha_sem_rotulo_forte_ainda_passa():
    """O conserto acima nao pode recusar o OCR mais comum da producao."""
    d = boleto.extrair("Boleto da Enel, R$ 187,45, vencimento 20/08/2026")
    assert d and d["valor_reais"] == 187.45, d


# --- P1-16: contagem de datas sobre o texto ja limpo ---------------------

def test_uma_data_depois_de_limpar_nao_e_ambigua():
    d = boleto.extrair("Boleto da Enel, R$ 187,45, para pagar dia "
                       "20/08/2026, emitido em 05/08/2026")
    assert d and d["data_vencimento"] == "2026-08-20", (
        f"recusou uma data que ficou sem ambiguidade: {d}")
