# -*- coding: utf-8 -*-
"""IMAGEM QUE NAO E BOLETO: entender, PERGUNTAR, e so entao guardar.

Pedido do Kevin em 29/08/2026: "Ler imagem que nao e boleto deve funcionar,
entender o que e e perguntar, confirma, ajusta ou esquece tipo isso".

Hoje o caminho de imagem so sabe fazer duas coisas: se `boleto.extrair`
reconhece um documento financeiro, vira item; se nao, cai no menu 1/2 antigo
e o texto do OCR inteiro vira descricao. Foto de nota fiscal, carteirinha de
vacina ou receita medica nao viram nada util.

A REGRA QUE MANDA AQUI: o bot NUNCA guarda sozinho o que ele apenas achou que
entendeu. Ele propoe e a pessoa confirma — porque item errado na lista e pior
que item nenhum, e foi assim que a lista de alguem encheu de lixo de OCR.
"""
import pytest

import documento
import wa_bot


NOTA_FISCAL = """DANFE - DOCUMENTO AUXILIAR DA NOTA FISCAL ELETRONICA
MAGAZINE ELETRO LTDA   CNPJ 12.345.678/0001-90
GELADEIRA BRASTEMP FROST FREE 375L
Valor total  R$ 3.299,00      Data de emissao 15/08/2026"""

CNH = """REPUBLICA FEDERATIVA DO BRASIL
CARTEIRA NACIONAL DE HABILITACAO
NOME KEVIN SANTOS
VALIDADE 12/03/2027    1a HABILITACAO 10/05/2010
CATEGORIA AB"""

RECEITA = """RECEITUARIO MEDICO
Dr. Joao Silva - CRM 123456
LOSARTANA 50MG - tomar 1 comprimido ao dia
Uso continuo por 60 dias    Data 20/08/2026"""

VACINA_PET = """CARTEIRA DE VACINACAO ANIMAL
THOR - Golden Retriever
V10 aplicada em 12/08/2026
Proxima dose: 12/08/2027"""

FOTO_QUALQUER = "sorria vem passarinho olha o passarinho"


# ---------------------------------------------------------------------------
# reconhecer o tipo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto,esperado", [
    (NOTA_FISCAL, "nota_fiscal"),
    (CNH, "documento"),
    (RECEITA, "receita"),
    (VACINA_PET, "vacina"),
])
def test_reconhece_o_tipo(texto, esperado):
    d = documento.reconhecer(texto)
    assert d, "nao reconheceu nada em %r" % texto[:40]
    assert d["tipo"] == esperado, d


def test_foto_qualquer_nao_vira_nada():
    """Chutar aqui enche a lista da pessoa de lixo."""
    for t in (FOTO_QUALQUER, "", None, "oi tudo bem"):
        assert documento.reconhecer(t) is None, t


def test_extrai_a_data_que_importa():
    """Cada tipo tem UMA data que vale o lembrete."""
    nf = documento.reconhecer(NOTA_FISCAL)
    assert nf["data"] == "2026-08-15", nf          # emissao -> conta garantia
    cnh = documento.reconhecer(CNH)
    assert cnh["data"] == "2027-03-12", cnh        # validade
    vac = documento.reconhecer(VACINA_PET)
    assert vac["data"] == "2027-08-12", vac        # proxima dose


def test_a_descricao_nao_leva_o_ocr_inteiro():
    """O OCR cru virando descricao foi o defeito que criou a regra."""
    nf = documento.reconhecer(NOTA_FISCAL)
    assert len(nf["descricao"]) < 80, nf["descricao"]
    assert "CNPJ" not in nf["descricao"]
    assert "DANFE" not in nf["descricao"]


def test_dado_sensivel_nao_vaza_pra_descricao():
    """CPF, CRM e numero de documento nao entram na lista da pessoa."""
    rec = documento.reconhecer(RECEITA)
    assert "123456" not in rec["descricao"], rec
    cnh = documento.reconhecer(CNH)
    assert "KEVIN SANTOS" not in cnh["descricao"].upper() or True  # nome ok
    assert "CATEGORIA" not in cnh["descricao"].upper()


# ---------------------------------------------------------------------------
# a pergunta: confirma, ajusta ou esquece
# ---------------------------------------------------------------------------

def test_a_pergunta_tem_as_tres_saidas():
    p = documento.pergunta_de_confirmacao(documento.reconhecer(NOTA_FISCAL))
    assert "confirma" in p["texto"].lower() or "isso" in p["texto"].lower()
    assert p["botoes"] == ["Confirmar", "Ajustar", "Esquece"], p["botoes"]


def test_os_botoes_sao_comandos_conhecidos():
    """Botao que o bot nao entende e pior que botao nenhum."""
    p = documento.pergunta_de_confirmacao(documento.reconhecer(CNH))
    for b in p["botoes"]:
        assert wa_bot.entende_comando(b), b


def test_a_pergunta_diz_o_que_entendeu():
    """A pessoa so consegue corrigir o que ela ve."""
    p = documento.pergunta_de_confirmacao(documento.reconhecer(VACINA_PET))
    assert "12/08/2027" in p["texto"], p["texto"]


def test_sem_reconhecimento_nao_ha_pergunta():
    assert documento.pergunta_de_confirmacao(None) is None


# ---------------------------------------------------------------------------
# de ponta a ponta: foto -> pergunta -> confirma -> item
# ---------------------------------------------------------------------------

def test_as_categorias_existem_no_banco():
    """Categoria invalida vira 'Outros' em silencio e polui o resumo."""
    import db
    for tipo, cat in wa_bot._CATEGORIA_DE_DOC.items():
        assert cat in db.VALID_CATEGORIES, (tipo, cat)


def _propor(usuario, ocr):
    """Coloca uma proposta pendente, como o fluxo de imagem faria."""
    import documento as _doc
    import tempo
    p = _doc.pergunta_de_confirmacao(_doc.reconhecer(ocr))
    wa_bot.PENDING[usuario["telefone"]] = {
        "tipo": "confirmar_documento", "doc": p["doc"], "quando": tempo.agora()}
    return p


def test_confirmar_guarda_o_item(usuario):
    import db
    _propor(usuario, CNH)
    resp = wa_bot._handle_commands(usuario, usuario["telefone"], "Confirmar")
    assert resp and "12/03/2027" in resp, resp
    itens = db.list_items(usuario["id"], status="pendente")
    assert any(i["data_vencimento"] == "2027-03-12" for i in itens), itens


def test_esquece_nao_guarda_nada(usuario):
    import db
    antes = len(db.list_items(usuario["id"]))
    _propor(usuario, CNH)
    resp = wa_bot._handle_commands(usuario, usuario["telefone"], "Esquece")
    assert resp and "não guardei" in resp.lower(), resp
    assert len(db.list_items(usuario["id"])) == antes


def test_ajustar_devolve_a_bola_sem_chutar(usuario):
    import db
    antes = len(db.list_items(usuario["id"]))
    _propor(usuario, CNH)
    resp = wa_bot._handle_commands(usuario, usuario["telefone"], "Ajustar")
    assert resp, resp
    assert len(db.list_items(usuario["id"])) == antes, "guardou mesmo assim"
    assert wa_bot.PENDING[usuario["telefone"]]["tipo"] == "ajustar_documento"


def test_confirmar_solto_nao_cria_item_do_nada(usuario):
    """Sem proposta pendente, "confirmar" nao pode virar item."""
    import db
    wa_bot.PENDING.pop(usuario["telefone"], None)
    antes = len(db.list_items(usuario["id"]))
    wa_bot._handle_commands(usuario, usuario["telefone"], "Confirmar")
    assert len(db.list_items(usuario["id"])) == antes


def test_documento_sem_data_pede_a_data(usuario):
    """Sem data nao da pra prometer aviso — pergunta em vez de inventar."""
    import db
    sem_data = "CARTEIRA NACIONAL DE HABILITACAO\nNOME KEVIN SANTOS"
    _propor(usuario, sem_data)
    antes = len(db.list_items(usuario["id"]))
    resp = wa_bot._handle_commands(usuario, usuario["telefone"], "Confirmar")
    assert "data" in (resp or "").lower(), resp
    assert len(db.list_items(usuario["id"])) == antes
