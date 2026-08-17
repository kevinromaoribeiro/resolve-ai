"""Os 3 P0 da rodada 2 do M2.1 — dois deles criados pelos consertos da 1.

Assinatura comum: regra nova escrita como regex de palavra-chave e validada
só contra os textos que a motivaram.
"""
import pytest

import boleto
import db
import wa_bot
from conftest import TELEFONE
from fixtures_boleto import ENEL
from fixtures_boleto2 import (BOLETOS_QUE_A_LISTA_NEGATIVA_COMEU,
                              COMPROVANTE_DE_TITULO,
                              COMPROVANTE_DE_TITULO_COM_CAUDA,
                              CONVERSAS_COM_DINHEIRO, LEGENDAS_AFIRMATIVAS,
                              LEGENDAS_NEGADAS, UMA_LINHA,
                              UMA_LINHA_SEM_ROTULO_DE_VENC)


def _foto(monkeypatch, texto, msg_id="IMG", legenda=""):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: texto)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": msg_id},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": legenda}}}}
    return (wa_bot.handle_incoming(payload) or {}).get("text", "")


# --- P0-8: comprovante de titulo tem os MESMOS campos do boleto ----------

def test_comprovante_de_titulo_nao_vira_cobranca():
    """Cedente, nosso numero e linha digitavel aparecem nos DOIS
    documentos. Classificar pelo campo faz o bot cobrar o que a pessoa
    acabou de pagar."""
    d = boleto.extrair(COMPROVANTE_DE_TITULO)
    assert d["tipo"] == "comprovante", d
    assert d["status_sugerido"] == "concluido", d


def test_cauda_explicita_ganha_da_heuristica():
    """`tipo=comprovante` na cauda e sinal explicito do modelo. Perder pra
    uma palavra solta no corpo e a regra 2 ao contrario."""
    d = boleto.extrair(COMPROVANTE_DE_TITULO_COM_CAUDA)
    assert d["tipo"] == "comprovante", d


def test_comprovante_de_titulo_no_fluxo(usuario, monkeypatch):
    reply = _foto(monkeypatch, COMPROVANTE_DE_TITULO, "R1C")
    itens = db.list_items(usuario["id"])
    assert itens and itens[0]["status"] == "concluido", (
        f"vai cobrar o que ja foi pago: {itens} / {reply!r}")


def test_boleto_de_verdade_continua_pendente():
    """A precedencia nova nao pode inverter o P0-3 da rodada 1."""
    assert boleto.extrair(ENEL)["status_sugerido"] == "pendente"


# --- P0-9: legenda NEGADA nao marca como paga ---------------------------

@pytest.mark.parametrize("legenda", LEGENDAS_NEGADAS)
def test_legenda_negada_mantem_pendente(usuario, monkeypatch, legenda):
    """Quem fotografa boleto costuma comentar que FALTA pagar. Marcar como
    paga tira da lista e nenhum lembrete dispara."""
    _foto(monkeypatch, ENEL, f"NEG{abs(hash(legenda))}", legenda=legenda)
    itens = db.list_items(usuario["id"])
    assert itens, "nao gravou nada"
    assert itens[0]["status"] == "pendente", (
        f"'{legenda}' marcou a conta como paga")


@pytest.mark.parametrize("legenda", LEGENDAS_AFIRMATIVAS)
def test_legenda_afirmativa_marca_pago(usuario, monkeypatch, legenda):
    _foto(monkeypatch, ENEL, f"AFI{abs(hash(legenda))}", legenda=legenda)
    itens = db.list_items(usuario["id"])
    assert itens and itens[0]["status"] == "concluido", (
        f"'{legenda}' devia marcar como paga: {itens}")


def test_comprovante_por_legenda_nao_mente_na_data(usuario, monkeypatch):
    """`pago em <vencimento>` e mentira: o vencimento nao e a data em que a
    pessoa pagou."""
    reply = _foto(monkeypatch, ENEL, "LEGDATA", legenda="já paguei essa")
    assert "pago em 20/08" not in reply, (
        f"usou o vencimento como data de pagamento: {reply!r}")


# --- P0-10: OCR de UMA LINHA (o que o prompt realmente pede) ------------

@pytest.mark.parametrize("nome", sorted(UMA_LINHA))
def test_uma_linha_pega_vencimento_e_valor(nome):
    texto, valor, data = UMA_LINHA[nome]
    d = boleto.extrair(texto)
    assert d, f"{nome} nao foi reconhecido"
    assert d["valor_reais"] == valor, f"{nome}: valor {d['valor_reais']}"
    assert d["data_vencimento"] == data, (
        f"{nome}: pegou {d['data_vencimento']} em vez de {data} — "
        f"item nasce vencido e cobra na hora")


def test_duas_datas_sem_rotulo_de_vencimento_nao_chuta():
    """Duas datas e NENHUMA com rotulo: a politica do arquivo e clara —
    melhor nao ter data do que ter data errada.

    Refinado na rodada 3 (P1-16): a contagem e feita depois de apagar as
    datas PROIBIDAS. Se sobrou uma so, nao ha ambiguidade e ela vale; o que
    nao vale e escolher entre duas no escuro.
    """
    d = boleto.extrair("Boleto Enel 05/08/2026 e 20/08/2026, valor do "
                       "documento R$ 187,45")
    assert d is None or d["data_vencimento"] is None, (
        f"escolheu uma data entre duas, sem rotulo nenhum: {d}")


def test_emissao_apagada_deixa_a_unica_data_valer():
    d = boleto.extrair(UMA_LINHA_SEM_ROTULO_DE_VENC + " e 20/08/2026")
    assert d and d["data_vencimento"] == "2026-08-20", d


# --- P1-10: a lista negativa nao pode comer boleto legitimo -------------

@pytest.mark.parametrize("nome", sorted(BOLETOS_QUE_A_LISTA_NEGATIVA_COMEU))
def test_boleto_legitimo_passa_pela_lista_negativa(nome):
    d = boleto.extrair(BOLETOS_QUE_A_LISTA_NEGATIVA_COMEU[nome])
    assert d is not None, f"{nome} foi recusado — a feature nao existe pra ele"
    assert d["status_sugerido"] == "pendente", d


# --- P1-11: conversa que cita dinheiro nao e conta ----------------------

@pytest.mark.parametrize("nome", sorted(CONVERSAS_COM_DINHEIRO))
def test_conversa_com_dinheiro_nao_vira_conta(nome):
    d = boleto.extrair(CONVERSAS_COM_DINHEIRO[nome])
    assert d is None, f"{nome} virou conta a pagar: {d}"


# --- P1-12: beneficiario nao engole o rotulo seguinte -------------------

@pytest.mark.parametrize("texto,proibido", [
    ("Boleto. Beneficiario: Enel. Vencimento 20/08/2026. Valor do "
     "Documento R$ 187,45", "vencimento"),
    ("Boleto Enel emitido em 05/08/2026 para pagamento ate 20/08/2026, "
     "valor do documento R$ 187,45", "pagamento"),
])
def test_beneficiario_para_no_proximo_rotulo(texto, proibido):
    d = boleto.extrair(texto)
    benef = (d.get("beneficiario") or "").lower()
    assert proibido not in benef, f"beneficiario ficou {benef!r}"


# --- P2: PDF com legenda tem o mesmo tratamento da foto -----------------

def test_pdf_com_legenda_de_ja_pago(usuario, monkeypatch):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "YmFzZTY0")
    monkeypatch.setattr(boleto, "texto_de_pdf", lambda dados: ENEL)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": "PDF_LEG"},
                        "pushName": "Kevin",
                        "message": {"documentMessage": {
                            "fileName": "boleto.pdf",
                            "mimetype": "application/pdf",
                            "caption": "já paguei essa"}}}}
    wa_bot.handle_incoming(payload)
    itens = db.list_items(usuario["id"])
    assert itens and itens[0]["status"] == "concluido", (
        f"PDF com legenda nao recebeu o mesmo tratamento da foto: {itens}")
