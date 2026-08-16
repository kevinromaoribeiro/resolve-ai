"""M2.0 — templates e a porta unica de saida.

Descoberta que motiva o bloco: no canal oficial, mensagem proativa pra quem
esta FORA da janela de 24h ja falha hoje (a Meta devolve 131047). Ou seja,
lembrete de gente inativa simplesmente nao chega. Template nao e otimizacao,
e o que faz esse caminho existir.

Invariante que estes testes travam: fora da janela, texto livre NAO sai —
nem por decisao do LLM, nem por caminho novo. E template nao aprovado nunca
vira envio silencioso.
"""
import datetime as _dt
import re

import pytest

import canal
import db
import tempo
import templates
import wa_bot
from conftest import TELEFONE


# --- catalogo -------------------------------------------------------------

def test_catalogo_tem_os_cinco():
    esperados = {"resolveai_lembrete_hora", "resolveai_item_vencido",
                 "resolveai_resumo_do_dia",
                 "resolveai_reengajamento_pendentes",
                 "resolveai_fim_de_trial_aviso"}
    assert esperados <= set(templates.CATALOGO)


@pytest.mark.parametrize("nome", [
    "resolveai_lembrete_hora", "resolveai_item_vencido",
    "resolveai_resumo_do_dia", "resolveai_reengajamento_pendentes",
    "resolveai_fim_de_trial_aviso"])
def test_template_e_utility_valido(nome):
    t = templates.CATALOGO[nome]
    assert t.categoria == "UTILITY", f"{nome} nao e utility"
    assert t.idioma == "pt_BR"
    # nome que a Meta aceita: minusculo, digito e underscore
    assert re.fullmatch(r"[a-z0-9_]{1,512}", t.nome), t.nome
    assert len(t.corpo) <= 1024, f"{nome} passa do limite de corpo da Meta"
    assert t.justificativa.strip(), f"{nome} sem justificativa pra submissao"


@pytest.mark.parametrize("nome", list(templates.CATALOGO))
def test_variaveis_sao_sequenciais(nome):
    """{{1}}..{{n}} sem buraco e na mesma quantidade que o catalogo declara.

    Buraco na numeracao e reprovacao certa na submissao, e variavel a mais
    no envio estoura a chamada da Meta em producao."""
    t = templates.CATALOGO[nome]
    achadas = [int(x) for x in re.findall(r"\{\{(\d+)\}\}", t.corpo)]
    assert sorted(set(achadas)) == list(range(1, len(t.variaveis) + 1)), (
        f"{nome}: corpo usa {sorted(set(achadas))}, catalogo declara "
        f"{len(t.variaveis)} variavel(is)")


@pytest.mark.parametrize("nome", list(templates.CATALOGO))
def test_nada_de_linguagem_promocional(nome):
    """Conteudo promocional dentro de utility e rejeitado — e o Kevin foi
    explicito: nao inventar linguagem de venda pra tentar passar."""
    proibidas = ["assine", "assinatura por", "promo", "desconto", "oferta",
                 "aproveite", "sentimos sua falta", "volte", "gratis",
                 "r$", "upgrade", "plano"]
    corpo = templates.CATALOGO[nome].corpo.lower()
    achadas = [p for p in proibidas if p in corpo]
    assert not achadas, f"{nome} tem linguagem promocional: {achadas}"


# --- janela de 24h --------------------------------------------------------

def test_janela_aberta_com_mensagem_recente(usuario):
    db.log_message(usuario["id"], TELEFONE, "in", "texto", "oi")
    assert db.dentro_da_janela(usuario["id"]) is True


def test_janela_fechada_sem_mensagem(usuario):
    assert db.dentro_da_janela(usuario["id"]) is False


def test_janela_fechada_com_mensagem_velha(usuario):
    db.log_message(usuario["id"], TELEFONE, "in", "texto", "oi")
    velho = (tempo.agora() - _dt.timedelta(hours=25)).strftime(
        "%Y-%m-%d %H:%M:%S")
    with db.get_conn() as conn:
        conn.execute("UPDATE msg_log SET ts=? WHERE user_id=?",
                     (velho, usuario["id"]))
    assert db.dentro_da_janela(usuario["id"]) is False


def test_saida_do_bot_nao_abre_janela(usuario):
    """Quem abre a janela e a pessoa falando com o bot. Se a saida do bot
    abrisse, o bot se autoautorizaria a falar pra sempre."""
    db.log_message(usuario["id"], TELEFONE, "out", "texto", "oi")
    assert db.dentro_da_janela(usuario["id"]) is False


# --- a porta unica --------------------------------------------------------

def test_dentro_da_janela_manda_texto_livre(usuario, limpo):
    db.log_message(usuario["id"], TELEFONE, "in", "texto", "oi")
    r = canal.falar(TELEFONE, "chegou a hora: dentista",
                    user_id=usuario["id"],
                    template="resolveai_lembrete_hora", variaveis=["dentista"])
    assert r["enviado"] is True and r["via"] == "texto"
    assert limpo, "nao mandou nada"


def test_fora_da_janela_sem_template_nao_sai(usuario, limpo):
    r = canal.falar(TELEFONE, "chegou a hora: dentista",
                    user_id=usuario["id"])
    assert r["enviado"] is False
    assert r["motivo"] == "fora_da_janela_sem_template", r
    assert not limpo, f"mandou texto livre fora da janela: {limpo}"


def test_template_nao_aprovado_nao_vira_envio_silencioso(usuario, limpo,
                                                         monkeypatch):
    monkeypatch.setattr(canal, "_aprovados", lambda: set())
    r = canal.falar(TELEFONE, "chegou a hora: dentista",
                    user_id=usuario["id"],
                    template="resolveai_lembrete_hora", variaveis=["dentista"])
    assert r["enviado"] is False
    assert r["motivo"] == "template_nao_aprovado", r
    assert not limpo, "mandou mesmo sem aprovacao"


def test_fora_da_janela_com_template_aprovado_usa_template(usuario, monkeypatch):
    monkeypatch.setattr(canal, "_aprovados",
                        lambda: {"resolveai_lembrete_hora"})
    monkeypatch.setattr(canal, "OFICIAL", True)
    chamadas = []
    monkeypatch.setattr(canal, "send_template",
                        lambda *a, **kw: chamadas.append((a, kw)) or True)

    r = canal.falar(TELEFONE, "chegou a hora: dentista",
                    user_id=usuario["id"],
                    template="resolveai_lembrete_hora", variaveis=["dentista"])

    assert r["enviado"] is True and r["via"] == "template", r
    assert chamadas, "nao chamou o send_template"
    assert chamadas[0][0][1] == "resolveai_lembrete_hora"
    assert chamadas[0][0][2] == ["dentista"]


def test_template_desconhecido_no_catalogo_nao_sai(usuario, monkeypatch):
    monkeypatch.setattr(canal, "_aprovados", lambda: {"inventado"})
    r = canal.falar(TELEFONE, "oi", user_id=usuario["id"],
                    template="inventado", variaveis=[])
    assert r["enviado"] is False
    assert r["motivo"] == "template_fora_do_catalogo", r


def test_canal_sem_template_nao_manda_texto_livre(usuario, limpo, monkeypatch):
    """Canal reserva nao tem template. A regra continua valendo: foi texto
    livre fora da janela que rendeu duas restricoes da Meta."""
    monkeypatch.setattr(canal, "_aprovados",
                        lambda: {"resolveai_lembrete_hora"})
    monkeypatch.setattr(canal, "OFICIAL", False)
    r = canal.falar(TELEFONE, "chegou a hora", user_id=usuario["id"],
                    template="resolveai_lembrete_hora", variaveis=["x"])
    assert r["enviado"] is False
    assert r["motivo"] == "canal_sem_template", r
    assert not limpo


# --- o motor proativo passa pela porta ------------------------------------

def _dispatch_de_hora(uid, item_id):
    # `variante` e obrigatoria: quem nao declara nao usa template (o alarme
    # tem tres textos e so um deles e "chegou a hora").
    return {"user_id": uid, "user_nome": "Kevin", "telefone": TELEFONE,
            "item_id": item_id, "kind": "hora", "variante": "na_hora",
            "message": "chegou a hora: dentista"}


def test_proativa_fora_da_janela_vira_template(usuario, monkeypatch):
    import scheduler
    item_id = db.add_item(user_id=usuario["id"], tipo="lembrete",
                          categoria="Saude", descricao="dentista",
                          status="pendente")
    monkeypatch.setattr(scheduler, "run_proactive_engine", lambda: {
        "alarm_dispatches": [_dispatch_de_hora(usuario["id"], item_id)],
        "due_dispatches": [], "churn_dispatches": []})
    monkeypatch.setattr(canal, "_aprovados",
                        lambda: {"resolveai_lembrete_hora"})
    monkeypatch.setattr(canal, "OFICIAL", True)
    usados = []
    monkeypatch.setattr(canal, "send_template",
                        lambda *a, **kw: usados.append(a) or True)
    monkeypatch.setattr(canal, "send_text",
                        lambda *a, **kw: usados.append(("TEXTO",) + a) or True)

    wa_bot.dispatch_proactive()

    assert usados, "nao enviou nada"
    assert usados[0][1] == "resolveai_lembrete_hora", (
        f"proativa fora da janela nao usou template: {usados}")


def test_proativa_dentro_da_janela_continua_texto_livre(usuario, monkeypatch):
    import scheduler
    db.log_message(usuario["id"], TELEFONE, "in", "texto", "oi")
    item_id = db.add_item(user_id=usuario["id"], tipo="lembrete",
                          categoria="Saude", descricao="dentista",
                          status="pendente")
    monkeypatch.setattr(scheduler, "run_proactive_engine", lambda: {
        "alarm_dispatches": [_dispatch_de_hora(usuario["id"], item_id)],
        "due_dispatches": [], "churn_dispatches": []})
    usados = []
    monkeypatch.setattr(canal, "send_text",
                        lambda *a, **kw: usados.append(a) or True)

    wa_bot.dispatch_proactive()

    assert usados and "chegou a hora" in usados[0][1], (
        f"dentro da janela tem que ser texto livre: {usados}")


def test_proativa_nao_registrada_quando_nao_sai(usuario, monkeypatch):
    """Dedup so pode contar o que a pessoa recebeu. Registrar disparo que
    nao saiu apaga o lembrete pra sempre — o dedup e por item."""
    import scheduler
    item_id = db.add_item(user_id=usuario["id"], tipo="lembrete",
                          categoria="Saude", descricao="dentista",
                          status="pendente")
    monkeypatch.setattr(scheduler, "run_proactive_engine", lambda: {
        "alarm_dispatches": [_dispatch_de_hora(usuario["id"], item_id)],
        "due_dispatches": [], "churn_dispatches": []})
    monkeypatch.setattr(canal, "_aprovados", lambda: set())

    wa_bot.dispatch_proactive()

    assert db.dispatch_count_item("hora", item_id) == 0, (
        "marcou como disparado um lembrete que nunca chegou")


def test_variaveis_saem_do_dado_real(usuario):
    item_id = db.add_item(user_id=usuario["id"], tipo="lembrete",
                          categoria="Saude", descricao="ir ao dentista",
                          status="pendente")
    d = {"user_id": usuario["id"], "user_nome": "Kevin Ribeiro",
         "item_id": item_id, "kind": "hora", "variante": "na_hora"}
    nome, variaveis = templates.para_disparo(d)
    assert nome == "resolveai_lembrete_hora"
    assert variaveis == ["ir ao dentista"], variaveis


def test_kind_sem_template_nao_inventa():
    nome, variaveis = templates.para_disparo({"kind": "arquivado"})
    assert nome is None, "inventou template pra kind que nao tem"


def test_reengajamento_sem_pendente_nao_sai(usuario):
    """"Voce tem 0 itens pendentes" nao presta servico nenhum — o que
    sobra e so o pedido de voltar, que e marketing."""
    d = {"user_id": usuario["id"], "user_nome": "Kevin", "kind": "anti-churn"}
    nome, _ = templates.para_disparo(d)
    assert nome is None


def test_submissao_md_nao_diverge_do_catalogo():
    """Doc gerado que envelhece vira mentira: o que esta no Business Manager
    deixa de ser o que o bot manda."""
    import pathlib

    import templates.gerar_submissao as gs

    # Compara em MEMORIA: teste que escreve no repo pra conferir divergencia
    # falha na primeira rodada e passa na segunda, com o arquivo ja alterado.
    no_disco = pathlib.Path(gs.caminho_md()).read_text(encoding="utf-8")
    assert no_disco == gs.gerar_conteudo(), (
        "templates/SUBMISSAO.md esta desatualizado — rode "
        "`python templates/gerar_submissao.py` e commite")
