# -*- coding: utf-8 -*-
"""Os corpos já aprovados na Meta não mudam sem alguém decidir.

Congelado quer dizer que o texto **está aprovado lá**: o que sai pro cliente é
o que a Meta tem, não o que o repo diz. Se o repo muda e a Meta não, os dois
divergem em silêncio — e o silêncio é o problema, não a divergência.

ISTO EXISTE PORQUE O CONGELAMENTO ERA NOMINAL (auditoria M9, 4ª passada).
`CONGELADOS_NA_META` só fazia a checagem de acento PULAR o template. Efeito
líquido: nenhum teste lia aquele corpo, e trocar "da semana" por "do período"
passava a suíte inteira em verde. Uma anistia, não um cadeado.

Aqui o texto é fixado de verdade. Mudar um corpo congelado passa a exigir
mudar esta linha junto — que é o lembrete de que a Meta precisa aprovar de
novo antes de aquilo sair pra alguém.
"""
import hashlib

import pytest

import templates
from test_portugues import CONGELADOS_NA_META


# O texto exato que a Meta aprovou, por template. Trocar um valor daqui sem
# submeter a nova versão é o erro que este arquivo existe pra tornar barulhento.
CORPOS_APROVADOS = {
    "reativar_boas_vindas": (
        "Oi, {{1}}! Aqui e o Resolve AI. 👋\n\n"
        "Voce se cadastrou pra testar e a gente falhou: nosso sistema ficou "
        "fora do ar e voce nao recebeu resposta. Foi erro nosso, e pedimos "
        "desculpa.\n\n"
        "Ja esta tudo funcionando, num numero novo e oficial. E seus 14 dias "
        "gratis estao intactos, valendo a partir de agora.\n\n"
        "Pra comecar, me manda uma coisa que voce nao pode esquecer:\n\n"
        '"luz 187 vence dia 20"\n'
        '"dentista dia 15 as 14h"\n\n'
        "Eu te aviso antes, sozinho, aqui no Zap. E se nao quiser mais, e so "
        "responder parar que eu nao te incomodo de novo."
    ),
    # O "da semana" aqui é conhecido e está sob decisão do dono: a
    # regularidade virou escolha (5/7/15/30 dias), então a frase mente pra
    # quem pedir quinzenal ou mensal. Trocar exige nova submissão à Meta, e
    # este template é o ÚNICO caminho do podcast que atravessa a janela de
    # 24h — enquanto a nova versão não é aprovada, quem escolheu dia fixo
    # não é avisado. Por isso a decisão é dele, e por isso o texto está
    # fixado aqui: pra ninguém "consertar" no código e o cliente continuar
    # recebendo o que a Meta tem.
    "resolveai_podcast_pronto": (
        "Oi {{1}}, seu resumo de *{{2}}* da semana está pronto.\n\n"
        "Quer ouvir agora? É só tocar no botão."
    ),
}


def test_todo_congelado_tem_o_corpo_fixado():
    """Entrar em `CONGELADOS_NA_META` sem passar por aqui devolveria a
    anistia: o template sairia das checagens e não entraria em nenhuma."""
    faltando = CONGELADOS_NA_META - set(CORPOS_APROVADOS)
    assert not faltando, (
        "congelado sem corpo fixado (a checagem some e nada a substitui): %s"
        % sorted(faltando))


def test_todo_corpo_fixado_esta_congelado():
    """A outra ponta: fixar o texto de um template que NÃO está aprovado
    engessaria uma redação que ainda pode mudar de graça."""
    sobrando = set(CORPOS_APROVADOS) - CONGELADOS_NA_META
    assert not sobrando, sobrando


@pytest.mark.parametrize("nome", sorted(CORPOS_APROVADOS))
def test_o_corpo_aprovado_nao_mudou(nome):
    atual = templates.CATALOGO[nome].corpo
    esperado = CORPOS_APROVADOS[nome]
    assert atual == esperado, (
        "%s mudou no código e continua aprovado na Meta com o texto antigo.\n"
        "O que sai pro cliente é o da Meta.\n\n"
        "  sha256 esperado: %s\n"
        "  sha256 atual:    %s\n\n"
        "Se a mudança é pra valer, submeta a nova versão à Meta, espere "
        "aprovar e só então atualize esta linha." % (
            nome,
            hashlib.sha256(esperado.encode("utf-8")).hexdigest()[:16],
            hashlib.sha256(atual.encode("utf-8")).hexdigest()[:16]))


@pytest.mark.parametrize("nome", sorted(CORPOS_APROVADOS))
def test_o_corpo_fixado_continua_valendo_como_template(nome):
    """O texto fixado não pode ter virado inválido por outro motivo — o
    congelamento não é desculpa pra corpo que a Meta recusaria."""
    corpo = CORPOS_APROVADOS[nome]
    assert len(corpo) <= 1024, len(corpo)
    assert not corpo.startswith("{{"), "corpo não pode começar em parâmetro"
    assert not corpo.rstrip().endswith("}}"), "nem terminar em parâmetro"


def test_o_teste_enxerga_a_mudanca():
    """Sem isto, um erro na comparação deixaria tudo verde e este arquivo
    seria a próxima guarda a não guardar nada."""
    corpo = CORPOS_APROVADOS["resolveai_podcast_pronto"]
    adulterado = corpo.replace("da semana", "do período")
    assert adulterado != corpo
    assert (hashlib.sha256(adulterado.encode("utf-8")).hexdigest()
            != hashlib.sha256(corpo.encode("utf-8")).hexdigest())
