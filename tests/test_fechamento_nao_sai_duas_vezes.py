# -*- coding: utf-8 -*-
"""O fechamento do trial sai UMA vez. Nao duas.

05/09, achado por auditoria e reproduzido rodando o pipeline de verdade:
no dia 13 a pessoa recebia DUAS mensagens identicas pedindo assinatura,
com segundos de diferenca, no numero que ja levou duas restricoes da Meta.

Por que a suite ficou verde com isso dentro: os dois geradores do
fechamento nunca eram testados JUNTOS. `check_trial_ending` tem uma guarda
contra duplicar com o D13 — mas ela le `trial_nudges_sent`, e quem grava
isso e quem ENVIA, depois do envio dar certo. Os dois lados sao gerados no
mesmo ciclo, antes de qualquer envio: quando a guarda pergunta, o carimbo
daquele ciclo ainda nao existe.

Enquanto o `trial_d6` nao tinha template o defeito era invisivel — ele
morria na poda fora da janela. Ao ganhar o MESMO template do
`trial-ending`, os dois passaram a sobreviver.

As duas condicoes batem no mesmo dia SEMPRE (`dia >= DIA_FECHAMENTO` e
`trial_days_left == 1` leem a mesma base). Nao e caso raro: e todo mundo
que chega ao dia 13.
"""
import datetime as _dt

import db
import scheduler
import templates
import tempo
import trial_guiado


def _no_dia_13(usuario):
    """Poe a pessoa no dia do fechamento e calada, que e o caso real."""
    base = tempo.agora() - _dt.timedelta(days=trial_guiado.DIA_FECHAMENTO)
    calado = (tempo.agora() - _dt.timedelta(hours=30)
              ).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_conn() as c:
        c.execute("UPDATE users SET trial_base=?, ultima_interacao=? "
                  "WHERE id=?",
                  (base.strftime("%Y-%m-%d %H:%M:%S"), calado, usuario["id"]))
    return db.get_user(usuario["id"])


def _fechamentos(guided, trial):
    """Todo disparo que pede assinatura, dos dois geradores."""
    return [d for d in list(guided) + list(trial)
            if d.get("kind") in ("trial_d6", "trial-ending")]


def test_no_dia_do_fechamento_sai_uma_mensagem_so(usuario):
    _no_dia_13(usuario)
    guided = trial_guiado.run_trial_nudges()
    _fechando = {d["user_id"] for d in guided if d.get("kind") == "trial_d6"}
    trial = scheduler.check_trial_ending(pular_ids=_fechando)
    meus = [d for d in _fechamentos(guided, trial)
            if d["user_id"] == usuario["id"]]
    assert len(meus) == 1, [d["kind"] for d in meus]


def test_o_ciclo_inteiro_nao_duplica_o_pedido_de_assinatura(usuario):
    """Pelo caminho de verdade: `run_proactive_engine` monta os dois lados."""
    _no_dia_13(usuario)
    pacote = scheduler.run_proactive_engine()
    todos = []
    for chave, valor in pacote.items():
        if chave.endswith("_dispatches") and isinstance(valor, list):
            todos.extend(valor)
    meus = [d for d in todos
            if d.get("user_id") == usuario["id"]
            and d.get("kind") in ("trial_d6", "trial-ending")]
    assert len(meus) <= 1, [d["kind"] for d in meus]


def test_os_dois_geradores_usam_o_mesmo_template(usuario):
    """E por isso que a duplicata doi: nao sao duas mensagens diferentes.

    Se um dia eles divergirem de template, este teste cai e obriga a
    revisar a guarda — porque a razao dela deixa de ser obvia.
    """
    assert (templates.KIND_TEMPLATE["trial_d6"]
            == templates.KIND_TEMPLATE["trial-ending"])


def test_quem_o_guiado_nao_fecha_ainda_recebe_o_fallback(usuario):
    """A guarda nao pode calar o fallback de quem o guiado NAO vai fechar.

    O `trial-ending` existe justamente pra quem escapou do D13 — matar ele
    junto trocaria uma mensagem duplicada por nenhuma, no unico momento em
    que o produto pede dinheiro.
    """
    _no_dia_13(usuario)
    trial = scheduler.check_trial_ending(pular_ids=frozenset())
    assert any(d["user_id"] == usuario["id"] for d in trial), trial


def test_a_guarda_pula_so_quem_esta_na_lista(usuario):
    _no_dia_13(usuario)
    vazio = scheduler.check_trial_ending(pular_ids={usuario["id"]})
    assert not [d for d in vazio if d["user_id"] == usuario["id"]]
