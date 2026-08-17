"""Rodada 8 do M2.1 — a chave não era tão única quanto o nome dela promete.

Os dois P0 são o mesmo defeito por dois ângulos: `valor + vencimento`
SELECIONA bem, mas não IDENTIFICA — e o campo que sustenta a chave às vezes
carregava a data errada.

A distinção que fecha isso: nome não serve como medida de semelhança (isso
falhou nas rodadas 6 e 7), mas serve como CONTRADIÇÃO. Ele só pode vetar.
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


def _ontem():
    return (tempo.hoje() - _dt.timedelta(days=1)).strftime("%d/%m/%Y")


def _foto(monkeypatch, texto, msg_id):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: texto)
    return (wa_bot.handle_incoming({"data": {"key": {
        "remoteJid": f"{TELEFONE}@s.whatsapp.net", "fromMe": False,
        "id": msg_id}, "pushName": "Kevin",
        "message": {"imageMessage": {"caption": ""}}}}) or {}).get("text", "")


def _conta(nome, valor, venc):
    return (f"Boleto Ficha de Compensacao. Beneficiario: {nome}. "
            f"Vencimento {venc}. Valor do Documento R$ {valor}")


# --- P0-19: o veto por contradição de nome ------------------------------

def test_comprovante_de_outro_credor_nao_quita(usuario, monkeypatch):
    """Colisao de valor E dia nao e exotica: vencimento se concentra em
    10/15/20 e valor redondo se repete (condominio, mensalidade, seguro)."""
    venc = _futuro(35)
    _foto(monkeypatch, _conta("SABESP", "150,00", venc), "V1")

    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em "
                   f"{_ontem()}. Beneficiario: ENEL DISTRIBUICAO. "
                   f"Vencimento do titulo: {venc}. Valor Pago R$ 150,00")
    reply = _foto(monkeypatch, comprovante, "V2")

    pendentes = db.list_items(usuario["id"], status="pendente")
    assert any("SABESP" in i["descricao"] for i in pendentes), (
        f"o comprovante da ENEL quitou a conta da SABESP: "
        f"{db.list_items(usuario['id'])} / {reply!r}")


def test_palavra_generica_nao_autoriza_baixa(usuario, monkeypatch):
    """`conta` esta em toda descricao. Ela nao pode nem causar nem
    PERMITIR — por isso sai dos dois lados antes da comparacao."""
    venc = _futuro(35)
    _foto(monkeypatch, _conta("SABESP", "150,00", venc), "G1")
    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em "
                   f"{_ontem()}. Beneficiario: CONTA DE LUZ ENEL. "
                   f"Vencimento do titulo: {venc}. Valor Pago R$ 150,00")
    _foto(monkeypatch, comprovante, "G2")
    pendentes = db.list_items(usuario["id"], status="pendente")
    assert any("SABESP" in i["descricao"] for i in pendentes), (
        db.list_items(usuario["id"]))


def test_mesmo_credor_continua_fechando(usuario, monkeypatch):
    """O veto nao pode barrar o caminho feliz."""
    venc = _futuro(35)
    _foto(monkeypatch, _conta("SABESP", "150,00", venc), "M1")
    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em "
                   f"{_ontem()}. Beneficiario: SABESP. "
                   f"Vencimento do titulo: {venc}. Valor Pago R$ 150,00")
    _foto(monkeypatch, comprovante, "M2")
    itens = db.list_items(usuario["id"])
    assert len(itens) == 1 and itens[0]["status"] == "concluido", itens


def test_comprovante_sem_beneficiario_ainda_fecha_pela_chave(usuario,
                                                             monkeypatch):
    """Sem nome nao ha contradicao possivel: a chave decide sozinha."""
    venc = _futuro(35)
    _foto(monkeypatch, _conta("SABESP", "150,00", venc), "S1")
    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em "
                   f"{_ontem()}. Vencimento do titulo: {venc}. "
                   f"Valor Pago R$ 150,00")
    _foto(monkeypatch, comprovante, "S2")
    itens = db.list_items(usuario["id"])
    assert len(itens) == 1 and itens[0]["status"] == "concluido", itens


# --- P0-20: o campo tem que carregar o que o nome diz -------------------

def test_data_neutra_nao_vira_vencimento_do_titulo():
    """`Data` e rotulo neutro e comum em recibo. Preenchendo o campo
    "vencimento do titulo" com a data do PAGAMENTO, o nome do campo mente e
    a decisao que depende dele erra."""
    hoje = tempo.hoje().strftime("%d/%m/%Y")
    d = boleto.extrair(f"COMPROVANTE DE PAGAMENTO. Data {hoje}. "
                       f"Beneficiario: MERCADO LIVRE. Valor Pago R$ 250,00")
    assert d["vencimento_titulo"] is None, d


def test_data_neutra_nao_fecha_conta_de_outro(usuario, monkeypatch):
    hoje = tempo.hoje().strftime("%d/%m/%Y")
    _foto(monkeypatch, _conta("VIVO", "250,00", hoje), "N1")
    comprovante = (f"COMPROVANTE DE PAGAMENTO. Data {hoje}. "
                   f"Beneficiario: MERCADO LIVRE. Valor Pago R$ 250,00")
    _foto(monkeypatch, comprovante, "N2")
    pendentes = db.list_items(usuario["id"], status="pendente")
    assert any("VIVO" in i["descricao"] for i in pendentes), (
        db.list_items(usuario["id"]))


def test_rotulo_de_vencimento_de_verdade_preenche():
    venc = _futuro(35)
    d = boleto.extrair(f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em "
                       f"{_ontem()}. Beneficiario: SABESP. "
                       f"Vencimento do titulo: {venc}. Valor Pago R$ 92,10")
    assert d["vencimento_titulo"] == "-".join(reversed(venc.split("/"))), d


@pytest.mark.parametrize("texto", [
    f"Comprovante de pagamento. pago em 10/08/2026. Valor Pago R$ 50,00",
    "Comprovante de Pix. Valor R$ 80,00 para Maria Silva",
])
def test_recibo_sem_vencimento_nao_inventa_chave(texto):
    d = boleto.extrair(texto)
    assert d is None or d["vencimento_titulo"] is None, d
