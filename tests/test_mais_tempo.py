# -*- coding: utf-8 -*-
"""A extensão do trial (M8.1).

O Kevin, com clientes reais entrando: "se o cliente responder MAIS TEMPO,
daremos só mais 2 dias no máximo viu, isso não é uma ONG, precisamos
faturar".

Dois dias bastam pra quem só precisava de um empurrão e são curtos demais
pra quem estava adiando a decisão. Sete transformavam o trial de 14 em 21
pra qualquer um que pedisse.
"""
import pytest

import db
import wa_bot
from conftest import TELEFONE, responder


def test_sao_dois_dias_no_maximo():
    assert wa_bot.TRIAL_EXTENSAO_DIAS == 2


def test_quem_pede_recebe_exatamente_dois(usuario):
    antes = db.trial_days_left(db.get_user(usuario["id"]), wa_bot.TRIAL_DAYS)
    r = responder("mais tempo")
    depois = db.trial_days_left(db.get_user(usuario["id"]), wa_bot.TRIAL_DAYS)
    assert depois - antes == 2, (antes, depois)
    assert "+2 dias" in r, r


def test_e_uma_vez_so_por_pessoa(usuario):
    """Sem isto, "mais tempo" toda semana vira trial infinito."""
    responder("mais tempo")
    depois_da_1a = db.trial_days_left(db.get_user(usuario["id"]),
                                      wa_bot.TRIAL_DAYS)
    r = responder("mais tempo")
    assert db.trial_days_left(db.get_user(usuario["id"]),
                              wa_bot.TRIAL_DAYS) == depois_da_1a
    assert "já te dei" in r.lower(), r


def test_assinante_nao_ganha_extensao(usuario):
    db.update_user_fields(usuario["id"], status="ativo")
    antes = db.trial_days_left(db.get_user(usuario["id"]), wa_bot.TRIAL_DAYS)
    responder("mais tempo")
    assert db.trial_days_left(db.get_user(usuario["id"]),
                              wa_bot.TRIAL_DAYS) == antes


def test_a_promessa_da_regua_bate_com_o_que_o_bot_faz(usuario):
    """O D12 promete "responde *mais tempo* que eu resolvo". Promessa que o
    Python não cumpre é a regra que já custou um P0 aqui."""
    assert wa_bot._MAIS_TEMPO_RE.match("mais tempo")
    r = responder("mais tempo")
    assert "liberei" in r.lower(), r


def test_falha_no_banco_nao_mente_pro_cliente(usuario, monkeypatch):
    """Dizer "liberei +2 dias" sem ter liberado, e ainda bloquear a pessoa
    pra sempre, é o defeito mais caro que este projeto já teve."""
    monkeypatch.setattr(db, "admin_extend_trial", lambda *a, **k: False)
    r = responder("mais tempo")
    assert "não consegui" in r.lower(), r
    assert not db.dispatched_ever("extensao-trial", usuario["id"])


def test_o_fechamento_promete_o_numero_que_o_bot_libera(usuario):
    """O texto dizia "falo com o Kevin e libero alguns dias" — ninguém é
    consultado (a extensão é automática) e "alguns" deixava a pessoa
    esperando uma semana pra receber dois dias. Promessa vaga no fechamento
    é a pior hora pra frustrar."""
    import datetime as _dt
    import tempo, trial_guiado
    db.update_user_fields(usuario["id"], onboarding_step=None,
                          status="trial")
    with db.get_conn() as c:
        c.execute("UPDATE users SET trial_base=?, ultima_interacao=?, "
                  "trial_nudges_sent=NULL WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(days=trial_guiado.DIA_FECHAMENTO)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                   (tempo.agora() - _dt.timedelta(hours=20)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))
    ds = [d for d in trial_guiado.run_trial_nudges()
          if d["user_id"] == usuario["id"]]
    assert ds, "o fechamento nao saiu"
    msg = ds[0]["message"]
    assert "*2* dias" in msg, msg
    assert "falo com o Kevin" not in msg, msg


def test_os_dois_modulos_nao_divergem():
    """Número escrito em dois lugares sai de sincronia no primeiro ajuste —
    e aí o texto promete um e o código entrega outro."""
    import trial_guiado
    assert trial_guiado.TRIAL_EXTENSAO_DIAS == wa_bot.TRIAL_EXTENSAO_DIAS
