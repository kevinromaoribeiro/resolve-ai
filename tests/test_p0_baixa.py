"""P0-1 e P0-2 — "feito" tem que dar baixa, e o menu 1/2 nao pode corromper item.

Conversa real do Kevin (12, 13 e 14/08), tres vezes seguidas:

    Bot:   chegou a hora: Estudar Product Manager
           Responda feito que eu dou baixa, ou adiar 1h.
    Kevin: feito
    Bot:   Nao entendi. Responda *1* (despesa paga), *2* (agendar lembrete)

E em 14/08 o estrago maior: o item vencido era "falar com o dentista" — um
LEMBRETE. Kevin respondeu "1" ao menu e o bot arquivou como *Despesa Paga*.

Estes testes executam o `handle_incoming` de verdade. Nenhum deles depende do
LLM: a baixa e o adiamento sao invariantes de Python (regra 2 do CLAUDE.md).
"""
import pytest

import db
import wa_bot
from conftest import TELEFONE, responder


def _item_com_alarme(uid, descricao="falar com o dentista", tipo="lembrete"):
    """Item pendente cujo alarme JA TOCOU — o alvo natural de um 'feito'."""
    item_id = db.add_item(user_id=uid, tipo=tipo, categoria="Saude",
                          descricao=descricao, status="pendente")
    db.log_dispatch(uid, "hora", item_id)
    return item_id


def _status(item_id):
    with db.get_conn() as conn:
        r = conn.execute("SELECT status FROM items WHERE id=?",
                         (item_id,)).fetchone()
    return r["status"] if r else None


def _tipo(item_id):
    with db.get_conn() as conn:
        r = conn.execute("SELECT tipo FROM items WHERE id=?",
                         (item_id,)).fetchone()
    return r["tipo"] if r else None


# --- P0-1 -----------------------------------------------------------------

@pytest.mark.parametrize("palavra", [
    "feito", "Feito", "FEITO", "ja fiz", "já fiz", "resolvi", "pago",
    "paguei", "feito!", "  feito  ",
])
def test_baixa_com_pending_travado(usuario, palavra):
    """O bloco de decisao pendente NAO pode sequestrar a palavra de baixa."""
    item_id = _item_com_alarme(usuario["id"])
    # Estado real do Kevin: um PENDING de imagem ficou preso no processo.
    wa_bot.PENDING[TELEFONE] = {"tipo": "despesa", "descricao": "boleto",
                                "valor_reais": 210.5, "categoria": "Contas",
                                "data_vencimento": "2026-08-20",
                                "status": "pendente"}

    reply = responder(palavra)

    assert _status(item_id) == "concluido", (
        f"'{palavra}' nao deu baixa. Resposta do bot: {reply!r}")
    assert "1" not in reply or "despesa paga" not in reply.lower(), (
        f"bot ofereceu o menu de desambiguacao para '{palavra}': {reply!r}")


@pytest.mark.parametrize("palavra", ["feito", "Feito", "resolvi", "paguei"])
def test_baixa_sem_pending(usuario, palavra):
    """Sem PENDING nenhum a baixa tambem tem que ser deterministica."""
    item_id = _item_com_alarme(usuario["id"])
    reply = responder(palavra)
    assert _status(item_id) == "concluido", (
        f"'{palavra}' nao deu baixa. Resposta: {reply!r}")


@pytest.mark.parametrize("palavra", ["adiar", "adiar 1h", "Adiar 1h",
                                     "mais tarde"])
def test_adiar_nao_conclui(usuario, palavra):
    """Adiar e o oposto de concluir. Em 05/08 o bot trocou os dois."""
    item_id = _item_com_alarme(usuario["id"])
    wa_bot.PENDING[TELEFONE] = {"tipo": "despesa", "descricao": "boleto",
                                "valor_reais": 210.5, "categoria": "Contas",
                                "data_vencimento": "2026-08-20",
                                "status": "pendente"}
    responder(palavra)
    assert _status(item_id) == "pendente", (
        f"'{palavra}' concluiu o item — o usuario pediu para ADIAR")


def test_feito_nao_da_baixa_sem_alarme(usuario):
    """Regressao do caso Fabio (05/08).

    Ele listou "arroz, leite, pao" e escreveu "Feito" querendo dizer
    "terminei de listar". Nenhum alarme tinha tocado. Se "feito" virar baixa
    incondicional, ele perde a lista inteira de novo.
    """
    item_id = db.add_item(user_id=usuario["id"], tipo="lembrete",
                          categoria="Outros", descricao="arroz, leite, pao",
                          status="pendente")
    responder("Feito")
    assert _status(item_id) == "pendente", (
        "deu baixa sem alarme nenhum ter tocado — caso Fabio de novo")


def test_frase_longa_nao_e_baixa(usuario):
    """"feito" dentro de frase nao e comando: e conteudo."""
    item_id = _item_com_alarme(usuario["id"])
    responder("o bolo ta feito de chocolate e vence amanha")
    assert _status(item_id) == "pendente"


# --- P0-2 -----------------------------------------------------------------

def test_menu_nao_reclassifica_lembrete(usuario):
    """O "1" do menu nao pode transformar um lembrete em despesa paga."""
    item_id = _item_com_alarme(usuario["id"])
    wa_bot.PENDING[TELEFONE] = {"tipo": "despesa", "descricao": "boleto",
                                "valor_reais": 210.5, "categoria": "Contas",
                                "data_vencimento": "2026-08-20",
                                "status": "pendente"}
    responder("feito")          # baixa deterministica, menu nao aparece
    responder("1")

    assert _tipo(item_id) == "lembrete", (
        "o lembrete do dentista virou despesa no historico")
    despesas = [i for i in db.list_items(usuario["id"], tipo="despesa")]
    assert not despesas, (
        f"o menu criou despesa fantasma a partir de estado velho: {despesas}")


def test_pending_nao_fica_preso_para_sempre(usuario):
    """PENDING sem saida foi o que fez o bug se repetir por 3 dias.

    Duas respostas fora do menu e o bot solta a decisao em vez de repetir
    "Nao entendi" indefinidamente.
    """
    wa_bot.PENDING[TELEFONE] = {"tipo": "despesa", "descricao": "boleto",
                                "valor_reais": 210.5, "categoria": "Contas",
                                "data_vencimento": "2026-08-20",
                                "status": "pendente"}
    responder("nada a ver")
    responder("outra coisa qualquer")
    responder("mais uma")
    assert TELEFONE not in wa_bot.PENDING, (
        "PENDING ficou preso: toda mensagem seguinte cai no menu 1/2")
