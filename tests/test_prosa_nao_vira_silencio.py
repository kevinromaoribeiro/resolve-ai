# -*- coding: utf-8 -*-
"""Resposta boa do modelo não pode virar silêncio pro cliente.

Alerta real, 02/09/2026, cliente novo:

    Mensagem: "Liste aqui para eu ir às compras"
    Motivo: json invalido (Expecting value: line 1 column 1 (char 0)) ::
            "Anotado. Você já tem uma lista de compras guardada:
             * Lista de compras escrita à mão ... — R$ 0,00 · pendente
             Qual item você "

Olha a resposta: ela estava CERTA. O modelo entendeu o pedido, achou a lista
e ia perguntar qual item. Só veio em prosa, e o motor só aceitava JSON — os
três remendos falharam, o laço deu `continue`, e **a pessoa não recebeu
nada**. Resposta boa jogada fora é a pior troca possível: o cliente fica sem
nada e a gente paga o token do mesmo jeito.
"""
import pytest

import motor_v8


INCIDENTE = ("Anotado. Você já tem uma lista de compras guardada:\n\n"
             "* Lista de compras escrita à mão com itens variados, sem "
             "valor — R$ 0,00 · pendente\n\n"
             "Qual item você ")


# ---------------------------------------------------------------------------
# 1. a cura de raiz: a API devolve JSON
# ---------------------------------------------------------------------------

def test_o_pedido_ja_sai_exigindo_json():
    """Não há o que remendar quando o formato não pode vir errado."""
    assert motor_v8._forcar_json() == {
        "response_format": {"type": "json_object"}}


def test_da_pra_desligar_sem_deploy(monkeypatch):
    """Nem todo provedor aceita o parâmetro. Perder a resposta inteira por
    causa dele seria trocar um defeito por um pior."""
    monkeypatch.setenv("LLM_JSON_ESTRITO", "0")
    assert motor_v8._forcar_json() == {}


# ---------------------------------------------------------------------------
# 2. e se ainda assim vier prosa, a prosa serve
# ---------------------------------------------------------------------------

def test_a_resposta_do_incidente_e_aproveitada():
    saida = motor_v8._prosa_aproveitavel(INCIDENTE)
    assert saida, "a resposta boa foi jogada fora de novo"
    assert "lista de compras guardada" in saida
    assert "Lista de compras escrita à mão" in saida, (
        "cortou a lista — era ela que o cliente pediu")


def test_a_frase_cortada_no_meio_nao_sai():
    """"Qual item você " no WhatsApp de alguém é pior que não mandar:
    parece que o bot travou no meio."""
    assert "Qual item você" not in motor_v8._prosa_aproveitavel(INCIDENTE)


def test_texto_inteiro_e_completo_passa_intacto():
    t = "Sua conta de luz vence amanhã. Quer que eu te lembre de manhã?"
    assert motor_v8._prosa_aproveitavel(t) == t


@pytest.mark.parametrize("bruto", [
    "",
    None,
    "   ",
    "oi",                                   # curto demais pra ser resposta
    '{"reply": "oi", "intent": "conv',      # JSON quebrado, não prosa
    '[{"a": 1}',
    '{"intent": "listar"}',
])
def test_o_que_nao_e_resposta_nao_vira_resposta(bruto):
    """JSON quebrado é fragmento de dado, não texto pra pessoa ler."""
    assert motor_v8._prosa_aproveitavel(bruto) == ""


def test_a_prosa_nao_age_sozinha():
    """Ela responde e não executa. Agir a partir de texto que a gente não
    conseguiu interpretar seria adivinhar em cima da lista de alguém."""
    import inspect
    fonte = inspect.getsource(motor_v8)
    assert '{"intent": "conversa", "reply": _texto}' in fonte, (
        "a prosa passou a carregar intenção — ela vai agir sem ser entendida")


def test_o_texto_longo_e_limitado():
    assert len(motor_v8._prosa_aproveitavel("Frase. " * 500)) <= 900
