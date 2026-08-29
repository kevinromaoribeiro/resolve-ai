# -*- coding: utf-8 -*-
"""A VOZ E A LOCUCAO: o unico ponto onde o LLM chega perto do audio.

A divisao e a de sempre (regra 2): o LLM faz LINGUA, o Python faz FATO. Ele
recebe so o que veio dos feeds e e proibido de acrescentar; o que ele devolve
passa por uma conferencia EM PYTHON antes de virar audio.

"Nao invente" e instrucao, e instrucao o modelo as vezes ignora. Conferencia
nao depende de boa vontade — e e isso que estes testes protegem.

Nenhum teste aqui chama modelo pago nem toca a rede.
"""
import pytest

import podcast
import voz

ITENS = [
    {"titulo": "Palmeiras vence o Flamengo por 2 a 1", "resumo": "No Allianz.",
     "fonte": "ge.globo"},
    {"titulo": "Corinthians anuncia meia argentino", "resumo": "Emprestimo.",
     "fonte": "ESPN Brasil"},
    {"titulo": "Gazeta aponta lesao de titular", "resumo": "Tres semanas.",
     "fonte": "Gazeta Esportiva"},
]

BOM = ("Oi, Kevin! Seu resumo de futebol da semana. " + "palavra " * 200 +
       " Isso foi o que saiu em ge.globo, ESPN Brasil e Gazeta Esportiva. "
       "Ate a proxima!")


# ---------------------------------------------------------------------------
# a conferencia do roteiro do LLM
# ---------------------------------------------------------------------------

def test_roteiro_bom_passa():
    assert podcast.conferir_locucao(BOM, "futebol") is None


def test_roteiro_que_cita_fonte_de_fora_e_recusado():
    """Fonte inventada e o sintoma mais facil de detectar de roteiro
    alucinado, e o mais caro: a pessoa vai conferir onde nao existe."""
    ruim = BOM + " Segundo a Folha de S.Paulo, o tecnico foi demitido."
    motivo = podcast.conferir_locucao(ruim, "futebol")
    assert motivo and "fora da lista" in motivo, motivo


@pytest.mark.parametrize("veiculo", [
    "Folha", "Estadão", "G1", "Reuters", "Bloomberg", "UOL", "CNN", "Veja",
])
def test_veiculos_famosos_sao_pegos(veiculo):
    ruim = BOM + " Como mostrou a %s, o clube negou." % veiculo
    assert podcast.conferir_locucao(ruim, "futebol"), veiculo


def test_a_propria_fonte_do_nicho_nao_e_falso_positivo():
    """"ge.globo" contem "globo", que esta na lista de veiculos. Recusar o
    roteiro por citar a fonte CERTA seria o veto se voltando contra si."""
    assert podcast.conferir_locucao(BOM, "futebol") is None
    vogue = ("Oi! Resumo de moda. " + "palavra " * 150 +
             " Isso foi o que saiu em Vogue Brasil, FFW e Steal the Look.")
    assert podcast.conferir_locucao(vogue, "moda") is None


def test_roteiro_gigante_e_recusado():
    """Passar do teto quebra a promessa dos 3 minutos, e TTS custa por
    minuto."""
    motivo = podcast.conferir_locucao("palavra " * 900, "futebol")
    assert motivo and "passou de" in motivo, motivo


@pytest.mark.parametrize("lixo", ["", "   ", None, "curto demais"])
def test_roteiro_vazio_ou_curto_e_recusado(lixo):
    assert podcast.conferir_locucao(lixo, "futebol")


def test_nicho_desconhecido_recusa():
    assert podcast.conferir_locucao(BOM, "criptomoeda")


# ---------------------------------------------------------------------------
# a locucao: cai no deterministico quando o LLM falha
# ---------------------------------------------------------------------------

def test_llm_bom_e_usado():
    r = podcast.locucao("futebol", ITENS, nome="Kevin", chamar=lambda p: BOM)
    assert r.startswith("Oi, Kevin!"), r[:60]


def test_llm_alucinado_cai_no_deterministico():
    """O roteiro simples e feio, mas e verdadeiro. Audio com voz de locutor
    afirmando o que ninguem verificou e o jeito mais rapido de perder a
    confianca de alguem."""
    ruim = BOM + " Segundo a Reuters, o clube foi vendido."
    r = podcast.locucao("futebol", ITENS, nome="Kevin", chamar=lambda p: ruim)
    assert r and "Reuters" not in r, r
    assert "Palmeiras vence o Flamengo" in r, r


def test_llm_fora_do_ar_cai_no_deterministico():
    def _explode(p):
        raise RuntimeError("429 rate limit")
    r = podcast.locucao("futebol", ITENS, chamar=_explode)
    assert r and "Palmeiras" in r


def test_sem_noticia_nao_ha_locucao():
    assert podcast.locucao("futebol", [], chamar=lambda p: BOM) is None
    assert podcast.locucao("futebol", None, chamar=lambda p: BOM) is None


def test_o_prompt_leva_so_o_que_veio_das_fontes():
    """O que o modelo recebe e o teto do que ele pode dizer."""
    visto = {}

    def _espiao(p):
        visto["prompt"] = p
        return BOM

    podcast.locucao("futebol", ITENS, nome="Kevin", chamar=_espiao)
    p = visto["prompt"]
    assert "Palmeiras vence o Flamengo" in p
    assert "ge.globo" in p and "ESPN Brasil" in p
    assert "NÃO INVENTE" in p, "o prompt perdeu a regra principal"
    assert str(podcast.PALAVRAS_ALVO) in p


def test_item_de_fonte_de_fora_nao_chega_no_prompt():
    """O filtro roda ANTES do modelo: o que nao veio das fontes nao pode nem
    ser oferecido como materia-prima."""
    visto = {}
    sujo = ITENS + [{"titulo": "Boato do Blog do Ze", "resumo": "x",
                     "fonte": "Blog do Ze"}]
    podcast.locucao("futebol", sujo, chamar=lambda p: visto.setdefault("p", p) or BOM)
    assert "Blog do Ze" not in visto["p"]


# ---------------------------------------------------------------------------
# a sintese
# ---------------------------------------------------------------------------

def test_sem_provedor_nao_sintetiza(monkeypatch):
    """Sem chave, `disponivel()` e False e o podcast nem e oferecido — o bot
    nao promete o que nao pode entregar."""
    monkeypatch.setattr(voz, "provedor_configurado", lambda: None)
    assert voz.sintetizar("qualquer coisa") is None
    assert not voz.disponivel()


@pytest.mark.parametrize("lixo", ["", "   ", None])
def test_texto_vazio_nao_vira_chamada_paga(lixo, monkeypatch):
    chamou = []
    monkeypatch.setattr(voz, "provedor_configurado", lambda: "openai")
    monkeypatch.setattr(voz, "_openai", lambda t: chamou.append(t))
    assert voz.sintetizar(lixo) is None
    assert not chamou, "gastou chamada de TTS com texto vazio"


def test_texto_gigante_nao_vira_chamada_paga(monkeypatch):
    """Acima do teto alguma coisa deu errado a montante, e sintetizar seria
    pagar por um erro."""
    chamou = []
    monkeypatch.setattr(voz, "provedor_configurado", lambda: "openai")
    monkeypatch.setattr(voz, "_openai", lambda t: chamou.append(t))
    assert voz.sintetizar("x" * (voz.MAX_CARACTERES + 1)) is None
    assert not chamou


def test_falha_do_provedor_devolve_none_e_nao_estoura(monkeypatch):
    """Audio quebrado e pior que audio nenhum: a pessoa toca, nao sai som, e
    conclui que o produto nao funciona."""
    def _explode(t):
        raise RuntimeError("500 do provedor")
    monkeypatch.setattr(voz, "provedor_configurado", lambda: "openai")
    monkeypatch.setattr(voz, "_openai", _explode)
    assert voz.sintetizar("texto normal") is None


def test_o_formato_e_nota_de_voz_nao_arquivo():
    """Mp3 chega como card de download, e ninguem baixa arquivo de bot."""
    assert voz.FORMATO == "opus"
    assert voz.MIME == "audio/ogg"


def test_a_conta_do_custo_fecha():
    """O custo do podcast entra na mesma conta que decide se R$ 19,90 fecha.
    Deixar isso implicito e como se descobre no fim do mes que a feature
    comeu a margem."""
    um = voz.custo_estimado_usd("x" * 2500)
    assert 0.02 < um < 0.05, um
    assert voz.custo_mensal_estimado_usd(0) == 0
    assert voz.custo_mensal_estimado_usd(11) < 2.0
    # cresce linear: o dobro de gente, o dobro de custo
    assert abs(voz.custo_mensal_estimado_usd(200)
               - 2 * voz.custo_mensal_estimado_usd(100)) < 0.01
