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


# ---------------------------------------------------------------------------
# As guardas valem pra prosa também (auditoria M16, P1)
# ---------------------------------------------------------------------------
# A intenção neutra que eu dei pra prosa desligava as duas verificações que o
# motor faz por `intent`. Antes do M14 esses casos eram silêncio; viraram
# MENTIRA, que é pior.

def test_prosa_que_promete_guardar_e_recusada():
    """"Anotado! Vou te lembrar da luz dia 12" voltava como conversa, nada
    era gravado e ninguém reconsultava. O docstring da própria função do
    motor chama isso de "a única falha que destrói a confiança de vez"."""
    assert motor_v8._promete_guardar("Anotado! Vou te lembrar da luz dia 12.")


def test_a_recusa_esta_no_caminho_da_prosa():
    import inspect
    fonte = inspect.getsource(motor_v8._llm)
    assert "_promete_guardar(_texto)" in fonte, (
        "a prosa voltou a prometer sem gravar")
    assert "_consulta_confere(_texto, itens)" in fonte, (
        "valor em reais na prosa não é mais batido contra o banco")


@pytest.mark.parametrize("bruto", [
    "Vou conferir o valor da sua conta de luz e te",
    "Deixa eu ver aqui a sua lista de",
])
def test_prosa_de_uma_linha_cortada_no_meio_nao_sai(bruto):
    """A guarda estava dentro de `len(linhas) > 1` — então a forma MAIS
    provável do defeito (resposta curta truncada) passava inteira."""
    assert motor_v8._prosa_aproveitavel(bruto) == ""


def test_fragmento_de_json_no_meio_nao_sai():
    """A guarda só olhava o começo do texto."""
    assert motor_v8._prosa_aproveitavel(
        'Aqui esta o resumo: {"itens": [{"descricao": "luz"') == ""


def test_o_teto_corta_na_palavra_inteira():
    """Cortar em 900 no meio da palavra reintroduz o mesmo "bot travado"
    que esta função existe pra impedir."""
    saida = motor_v8._prosa_aproveitavel("palavra completa. " * 100)
    assert saida.endswith("...")
    assert not saida.replace("...", "").endswith("palavr")
    assert len(saida) <= 905


# ---------------------------------------------------------------------------
# As guardas EXERCITADAS, não conferidas por string no fonte
# ---------------------------------------------------------------------------
# O auditor: "o conserto do P1, que é o mais importante do lote, não tem
# nenhum teste que o exercite — reordenar as guardas para depois do `return`
# passaria verde". Estes três rodam o `_llm` de verdade, com um `completion`
# dublê devolvendo prosa.

class _Resp:
    def __init__(self, txt):
        self.choices = [type("C", (), {"message": type("M", (), {
            "content": txt})()})()]


def _llm_com_prosa(monkeypatch, prosa, itens=None):
    import litellm
    monkeypatch.setattr(litellm, "completion",
                        lambda *a, **k: _Resp(prosa))
    return motor_v8._llm("qualquer coisa", "Ana", itens or [], [], [],
                         __import__("ai_engine"))


def test_prosa_que_promete_guardar_nao_chega_no_cliente(monkeypatch):
    """"Anotado! Vou te lembrar da luz dia 12" com zero item gravado é
    mentira — e antes do M14 era silêncio, que é menos pior."""
    r = _llm_com_prosa(monkeypatch,
                       "Anotado! Vou te lembrar de pagar a luz no dia 12.")
    assert r is None or "lembrar" not in (r.get("reply") or "").lower(), r


def test_prosa_com_valor_fantasma_nao_chega_no_cliente(monkeypatch):
    """Banco vazio e o bot afirmando R$ 1.240,00 é a classe que a
    conferência existe pra barrar."""
    r = _llm_com_prosa(
        monkeypatch, "Você tem três contas somando R$ 1.240,00 este mês.")
    assert r is None or "1.240" not in (r.get("reply") or ""), r


def test_prosa_boa_continua_chegando(monkeypatch):
    """A outra metade: estrangular a prosa mataria o motivo do M14 existir."""
    r = _llm_com_prosa(
        monkeypatch,
        "Seu próximo compromisso é na quinta de manhã. Quer que eu te avise?")
    assert r and "quinta" in (r.get("reply") or "").lower(), r
    assert r.get("intent") == "conversa", r
