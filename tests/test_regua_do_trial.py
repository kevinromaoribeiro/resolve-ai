# -*- coding: utf-8 -*-
"""A régua de engajamento do trial (M7.8).

O Kevin: "todos os dias durante o trial como o cliente está sendo impactado?
eu não recebo, e não sei se os clientes estão".

Medido: ninguém. Nunca. Dois defeitos independentes, cada um bastava sozinho
— e os dois eram invisíveis, porque o sintoma é ausência de mensagem.
"""
import datetime as _dt

import pytest

import db
import scheduler
import tempo
import trial_guiado
import wa_bot
from conftest import TELEFONE


def _no_dia(usuario, dia, calado_ha=20, passo=None):
    """Cliente real: entrou há `dia` dias, calado há `calado_ha` horas."""
    db.update_user_fields(usuario["id"], onboarding_step=passo,
                          status="trial")
    # ZERA A REGUA. `trial_nudges_sent` acumula e a fixture `usuario` do
    # conftest nao limpa — sem isto, um teste consome o "d2" e o proximo
    # mede o consumo do anterior em vez de medir a regua.
    with db.get_conn() as _c:
        _c.execute("UPDATE users SET trial_nudges_sent=NULL WHERE id=?",
                   (usuario["id"],))
    with db.get_conn() as c:
        c.execute("UPDATE users SET trial_base=?, data_criacao=?, "
                  "ultima_interacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(days=dia)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                   (tempo.agora() - _dt.timedelta(days=dia)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                   (tempo.agora() - _dt.timedelta(hours=calado_ha)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                   usuario["id"]))


def _meus(usuario):
    return [d for d in trial_guiado.run_trial_nudges()
            if d["user_id"] == usuario["id"]]


# ---------------------------------------------------------------------------
# 1. o defeito que esvaziava a fila
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("passo", [None, "done"])
def test_quem_terminou_o_onboarding_entra_na_fila(usuario, passo):
    """Quem termina fica com `None`, não com "done" (ver jornada.py). A
    comparação estrita excluía TODO cliente real — e os outros quatro
    lugares do código sempre usaram `(x or "done")`."""
    _no_dia(usuario, dia=2, passo=passo)
    assert usuario["id"] in [u["id"] for u in db.active_trial_users()], passo


def test_quem_ainda_esta_no_onboarding_fica_de_fora(usuario):
    """Nudge no meio do cadastro é atropelar quem ainda está entrando."""
    _no_dia(usuario, dia=2, passo="lgpd_landing")
    assert usuario["id"] not in [u["id"] for u in db.active_trial_users()]


# ---------------------------------------------------------------------------
# 2. o defeito que tornava a régua impossível
# ---------------------------------------------------------------------------

def test_o_nudge_cabe_dentro_da_janela_de_24h():
    """A INVARIANTE QUE ESTAVA QUEBRADA.

    O nudge sai como texto livre (nenhum `trial_d*` tem template, de
    propósito). Texto livre só passa pra quem falou nas últimas 24h. Se o
    gatilho exigir 24h+ de silêncio, as duas condições se excluem e a
    mensagem é gerada e descartada — sem erro e sem log.
    """
    assert trial_guiado.INACTIVE_HOURS < 24, (
        "nudge exige %sh de silencio, mas texto livre so passa dentro de "
        "24h — a regua fica impossivel" % trial_guiado.INACTIVE_HOURS)


def test_o_nudge_SAI_de_verdade_pra_quem_esfriou(usuario, monkeypatch):
    """Ponta a ponta: gerado E entregue. Antes ele morria na poda."""
    _no_dia(usuario, dia=3, calado_ha=20)
    db.log_message(usuario["id"], TELEFONE, "in", "texto", "oi")
    with db.get_conn() as c:      # a entrada foi ha 20h, nao agora
        c.execute("UPDATE msg_log SET ts=? WHERE user_id=?",
                  ((tempo.agora() - _dt.timedelta(hours=20)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))
    ds = _meus(usuario)
    assert ds, "nao gerou nudge nenhum"

    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(txt)
        or {"enviado": True, "via": "t", "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    monkeypatch.setattr(scheduler, "run_proactive_engine",
                        lambda **k: {"executed_at": "x", "total": len(ds),
                                     "guided_dispatches": ds})
    wa_bot.dispatch_proactive()
    assert enviados, "gerou o nudge e ele morreu na poda — de novo"


def test_quem_acabou_de_falar_nao_e_incomodado(usuario):
    """"Nudge é pra quem esfriou. Quem já está engajado precisa de espaço."
    — a filosofia escrita no topo do próprio arquivo."""
    _no_dia(usuario, dia=2, calado_ha=1)
    assert _meus(usuario) == []


# ---------------------------------------------------------------------------
# 3. a régua cobre os dias que promete
# ---------------------------------------------------------------------------

# OS DIAS VEM DA TABELA, e nao de uma lista copiada aqui.
#
# A regua mudou de nove toques diarios pra seis dia-sim-dia-nao, e a lista
# copiada no teste ficou pedindo os dias 2, 4 e 12 — que deixaram de
# existir. Copia de configuracao no teste envelhece em silencio e depois
# reprova por estar desatualizada, nao por ter achado defeito.
DIAS_DA_REGUA = [e["dia"] for e in trial_guiado._ETAPAS]


@pytest.mark.parametrize("dia", DIAS_DA_REGUA)
def test_cada_dia_da_regua_produz_seu_toque(usuario, dia):
    _no_dia(usuario, dia=dia, calado_ha=20)
    ds = _meus(usuario)
    assert ds, "dia %d nao gerou nada" % dia
    assert ds[0]["message"].strip(), ds


def test_o_fechamento_sai_no_fim_do_trial(usuario):
    """O D13 é o que fecha o funil: prova de valor + link de pagamento."""
    _no_dia(usuario, dia=trial_guiado.DIA_FECHAMENTO, calado_ha=20)
    ds = _meus(usuario)
    assert ds, "o fechamento do trial nao saiu"
    assert "19,90" in ds[0]["message"], ds[0]["message"]


def test_ninguem_recebe_o_mesmo_toque_duas_vezes(usuario):
    _no_dia(usuario, dia=3, calado_ha=20)
    primeiro = _meus(usuario)
    assert primeiro
    db.mark_nudge_sent(usuario["id"], primeiro[0]["nudge"])
    assert _meus(usuario) == []


def test_um_toque_por_dia_no_maximo(usuario):
    _no_dia(usuario, dia=3, calado_ha=20)
    assert len(_meus(usuario)) <= 1


# ---------------------------------------------------------------------------
# 4. quem JÁ ESTÁ no meio do trial quando a régua liga
# ---------------------------------------------------------------------------
# A régua nunca disparou até 31/08. Ligá-la com gente no dia 5, 7, 11 não
# pode virar uma rajada dos dias que essa pessoa "perdeu".

@pytest.mark.parametrize("dia", DIAS_DA_REGUA)
def test_quem_ja_esta_no_meio_recebe_UM_toque_so(usuario, dia):
    """O risco real do dia em que a régua passou a funcionar."""
    _no_dia(usuario, dia=dia, calado_ha=20)
    ds = _meus(usuario)
    assert len(ds) <= 1, "rajada de %d mensagens no dia %d" % (len(ds), dia)


def test_nao_manda_os_dias_atrasados_de_uma_vez(usuario):
    """Quem esta no dia 11 recebe a licao do 11 — nao a do 1 ao 11.

    DECISAO DO DONO (05/09): a regua oficial e dia -> licao, e quem entra
    no meio segue de onde esta, como cliente real. As licoes dos dias que
    ja passaram nao voltam.
    """
    _no_dia(usuario, dia=11, calado_ha=20)
    ds = _meus(usuario)
    assert ds and ds[0]["kind"] == "trial_d11", [d["kind"] for d in ds]


def test_o_dia_seguinte_traz_o_toque_seguinte_e_so(usuario):
    _no_dia(usuario, dia=5, calado_ha=20)
    primeiro = _meus(usuario)
    assert len(primeiro) == 1
    db.mark_nudge_sent(usuario["id"], primeiro[0]["nudge"])
    assert _meus(usuario) == [], "repetiu o mesmo dia"


@pytest.mark.parametrize("dia", DIAS_DA_REGUA)
def test_nenhum_toque_promete_um_prazo_errado(usuario, dia):
    """"faltam 2 dias" era verdade num trial de 7. Com 14 virou mentira.

    O toque que dizia o prazo saiu da regua, mas a garantia nao pode sair
    com ele: se algum dia alguem voltar a falar de prazo numa licao, o
    numero tem que ser o que o produto cumpre. A regra vale pra jornada
    inteira, e nao so pro dia que tinha o defeito.
    """
    import re
    _no_dia(usuario, dia=dia, calado_ha=20)
    ds = _meus(usuario)
    assert ds, "o dia %d nao saiu" % dia
    restam = db.trial_days_left(db.get_user(usuario["id"]),
                                trial_guiado.TRIAL_DAYS)
    # SO O PRAZO DO TESTE. "eu te aviso 3 dias antes" fala do vencimento de
    # uma conta, nao do trial — proibir todo numero seguido de "dias"
    # reprovaria a frase mais util da regua.
    prazo = re.compile(
        r"(?:faltam|falta|restam|resta|termina em|acaba em|vence em|"
        r"tem mais)\s*\*?(\d+)\*?\s*dias?", re.I)
    for texto in (ds[0]["message"], ds[0].get("o_que_ela_faz") or ""):
        for achado in prazo.findall(texto):
            assert int(achado) == restam, (
                "o dia %d promete %s dia(s) de teste e faltam %d: %s"
                % (dia, achado, restam, texto))


def test_o_fechamento_leva_link_de_verdade(usuario):
    """Placeholder no ÚNICO momento em que o produto pede dinheiro."""
    _no_dia(usuario, dia=trial_guiado.DIA_FECHAMENTO, calado_ha=20)
    msg = _meus(usuario)[0]["message"]
    assert "SEU-LINK" not in msg, msg
    assert "mpago.la" in msg or "http" in msg, msg


# ---------------------------------------------------------------------------
# 6. o SILENCIO dos dias sem toque
# ---------------------------------------------------------------------------
# A regua avanca pela proxima licao nao enviada. Sem uma trava de calendario
# ela falaria TODOS os dias ate esgotar a fila — seis mensagens em seis dias
# pra quem esta calado, que e o padrao que a Meta pune e que a pessoa
# bloqueia. O teste reverso mostrou que nada garantia esse silencio.

@pytest.mark.parametrize("dia", [2, 4, 6, 8, 10])
def test_dia_sem_toque_e_dia_de_silencio(usuario, dia):
    _no_dia(usuario, dia=dia, calado_ha=20)
    assert _meus(usuario) == [], (
        "a regua falou no dia %d, que nao e dia de toque" % dia)


def test_a_regua_entrega_uma_licao_por_dia_de_toque(usuario):
    """Onze dias de trial dao SEIS licoes, e nunca duas no mesmo dia."""
    saiu = 0
    for dia in range(1, 12):
        _no_dia(usuario, dia=dia, calado_ha=20)
        ds = _meus(usuario)
        assert len(ds) <= 1, "rajada no dia %d: %s" % (dia, ds)
        if ds:
            saiu += 1
            db.mark_nudge_sent(usuario["id"], ds[0]["nudge"])
    assert saiu == len(trial_guiado._DIAS_DE_TOQUE), saiu
