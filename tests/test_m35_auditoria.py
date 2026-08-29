# -*- coding: utf-8 -*-
"""AUDITORIA DO M3.5 — um teste por defeito encontrado.

Contexto: o M3.5 entregou tres coisas novas (codigo de pagamento colavel,
foto de documento que nao e boleto, e a oferta de remarcar servico que
repete). O auditor achou oito defeitos, e SETE deles estavam no codigo
escrito NAQUELA rodada, nao no codigo antigo. E o padrao desta base inteira:
o defeito grave mora na correcao mais nova.

Cada teste aqui prova UM achado. Se algum voltar, o vermelho aponta pro
mesmo lugar — e a mensagem de assert diz o que a pessoa do outro lado
sentiria, nao o que a funcao devolveu.
"""
import datetime as _dt

import pytest

import boleto
import botoes
import db
import documento
import scheduler
import tempo
import wa_bot
from conftest import TELEFONE, responder


def _foto(monkeypatch, texto, msg_id="IMGDOC", legenda=""):
    """Manda uma foto pelo caminho de producao, com o OCR ja decidido."""
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: texto)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": msg_id},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": legenda}}}}
    return wa_bot.handle_incoming(payload) or {}


NOTA = """NOTA FISCAL ELETRONICA - DANFE
Geladeira Brastemp Frost Free 375L
CNPJ 12.345.678/0001-90
Data de emissao 12/03/2026
Valor total R$ 3.499,00"""

CNH = """REPUBLICA FEDERATIVA DO BRASIL
CARTEIRA NACIONAL DE HABILITACAO
JOAO DA SILVA
Registro 01234567890
Validade 12/03/2027
Primeira Habilitacao 05/01/2010"""


# ---------------------------------------------------------------------------
# P1-3 — o item nascia VENCIDO
# ---------------------------------------------------------------------------

def test_nota_fiscal_nao_nasce_vencida():
    """A ancora da nota e a EMISSAO, que e sempre passado.

    Gravando ela em `data_vencimento`, a pessoa mandava a foto da nota de
    ontem, confirmava, e no ciclo seguinte levava "isso venceu e eu nao vi a
    baixa". Cobrar alguem por algo que acabou de nascer e o defeito que mais
    rapido ensina a desinstalar.
    """
    doc = documento.reconhecer(NOTA)
    assert doc["data"] == "2026-03-12", doc      # emissao, lida certo
    venc = documento.vencimento(doc)
    assert venc == "2027-03-12", (
        "a garantia de 1 ano tem que contar A PARTIR da emissao; %r" % venc)


def test_cnh_mantem_a_validade_que_esta_no_papel():
    """Nem todo tipo converte: na CNH a ancora JA e a data que importa."""
    doc = documento.reconhecer(CNH)
    assert documento.vencimento(doc) == "2027-03-12"


def test_promessa_nunca_promete_mais_do_que_o_motor_faz():
    """A outra metade do P1-3: o texto dizia "60 e 30 dias antes" e o motor
    so sabia avisar na vespera. Agora a frase e GERADA da mesma tabela que
    vai pro banco — nao tem como as duas divergirem de novo."""
    for tipo in ("nota_fiscal", "documento", "receita", "vacina"):
        frase = documento.promessa(tipo)
        for dia in documento.avisos(tipo):
            assert str(dia) in frase, (
                "%s avisa em D-%d e a promessa nao diz: %r"
                % (tipo, dia, frase))


def test_documento_guarda_a_antecedencia_junto_com_o_item(usuario, monkeypatch):
    """De ponta a ponta: foto -> proposta -> Confirmar -> item no banco."""
    r = _foto(monkeypatch, CNH)
    assert "CNH" in r.get("text", ""), r
    assert r.get("botoes") == ["Confirmar", "Ajustar", "Esquece"], r

    responder("Confirmar")
    itens = db.list_items(usuario["id"], status="pendente")
    assert len(itens) == 1, itens
    assert itens[0]["data_vencimento"] == "2027-03-12", itens[0]
    assert itens[0]["avisar_dias"] == "60,30", (
        "a promessa era avisar 60 e 30 dias antes; o item nao carrega isso")


def test_scheduler_honra_a_antecedencia_do_item(usuario, horario_util):
    """D-60 nao existe na politica global (`DUE_ALERT_DAYS = {1}`).

    Este teste e o que prova que a promessa VIRA MENSAGEM: sem ele, gravar
    "60,30" no banco seria enfeite. E a armadilha da janela de SQL mora aqui
    — item a 60 dias nem era LIDO, e o sintoma seria silencio, sem erro
    nenhum no log.
    """
    hoje = tempo.hoje()
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Outros",
                descricao="CNH", data_vencimento=(hoje + _dt.timedelta(days=60)
                                                  ).isoformat(),
                status="pendente", avisar_dias="60,30")
    # controle: mesmo prazo, SEM antecedencia propria -> tem que ficar quieto
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Outros",
                descricao="curso de ingles",
                data_vencimento=(hoje + _dt.timedelta(days=60)).isoformat(),
                status="pendente")

    saidas = scheduler.check_due_items(ref=hoje)
    descricoes = " ".join(d.get("message", "") for d in saidas)
    assert "CNH" in descricoes, (
        "prometeu avisar 60 dias antes e nao avisou: %r" % saidas)
    assert "ingles" not in descricoes, (
        "a antecedencia do item vazou pro resto da lista: %r" % saidas)


# ---------------------------------------------------------------------------
# P1-4 — "Ajustar" era beco sem saida
# ---------------------------------------------------------------------------

def test_ajustar_documento_escuta_a_resposta(usuario, monkeypatch):
    """A pessoa toca em Ajustar, o bot pede a data, ela responde — e antes
    NAO ACONTECIA NADA. Pedir e ignorar e pior que nao ter perguntado."""
    _foto(monkeypatch, CNH)
    r = responder("Ajustar")
    assert "vence" in r.lower(), r

    r = responder("vence 15/06/2028")
    assert "15/06/2028" in r, r
    itens = db.list_items(usuario["id"], status="pendente")
    assert len(itens) == 1, itens
    assert itens[0]["data_vencimento"] == "2028-06-15", itens[0]
    assert itens[0]["avisar_dias"] == "60,30", (
        "o ajuste perdeu a antecedencia que a proposta prometia")


def test_ajuste_com_data_relativa(usuario, monkeypatch):
    """"daqui a 3 meses" e resposta de gente. O parser e de proposito curto,
    mas o que ele entende tem que estar certo."""
    _foto(monkeypatch, CNH)
    responder("Ajustar")
    r = responder("daqui a 3 meses")
    esperado = wa_bot._somar_meses(tempo.hoje(), 3).isoformat()
    itens = db.list_items(usuario["id"], status="pendente")
    assert itens and itens[0]["data_vencimento"] == esperado, (r, itens)


def test_ajuste_que_o_python_nao_entende_nao_prende_a_conversa(
        usuario, monkeypatch):
    """Se a frase nao e data, a mensagem SEGUE pro motor normal.

    Insistir prenderia a pessoa num "me diz a data" sem saida — e ela tem o
    direito de mudar de assunto no meio.
    """
    _foto(monkeypatch, CNH)
    responder("Ajustar")
    responder("na verdade deixa pra la, quanto eu gastei esse mes?")
    assert wa_bot.PENDING.get(TELEFONE) is None, (
        "a conversa ficou presa esperando uma data que nao vem")


def test_data_sem_ano_e_sempre_pra_frente():
    """"vence 12/03" dito em agosto e marco do ANO QUE VEM.

    Gravar 12/03 deste ano faria o item nascer vencido — o mesmo P1-3 que
    esta rodada esta consertando, entrando de novo por outra porta.
    """
    base = _dt.date(2026, 8, 29)
    assert wa_bot._data_do_texto("vence 12/03", base=base) == "2027-03-12"
    assert wa_bot._data_do_texto("dia 30/09", base=base) == "2026-09-30"


def test_outra_data_escuta_a_resposta(usuario, horario_util, monkeypatch):
    """O mesmo beco sem saida do lado da oferta de remarcar.

    Vai pelo caminho de producao inteiro — motor gera, envio real de
    disparo, pessoa responde — porque a versao anterior desta feature
    "passava nos testes" justamente por montar o contexto na mao.
    """
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao="sobrancelha",
                      valor_reais=None, status="pendente")
    db.update_item_status(iid, "concluido")
    with db.get_conn() as c:
        c.execute("UPDATE items SET data_conclusao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=12)
                    ).strftime("%Y-%m-%d %H:%M:%S"), iid))
    db.log_message(None, usuario["telefone"], "in", "texto", "oi")
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True, "via": "botoes",
                                         "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    wa_bot.dispatch_proactive()

    r = wa_bot._handle_commands(usuario, usuario["telefone"], "Outra data")
    assert r and "data" in r.lower(), r
    r = wa_bot._handle_commands(usuario, usuario["telefone"], "dia 12/10")
    assert r and "12/10" in r, r
    novos = [i for i in db.list_items(usuario["id"], status="pendente")
             if i["descricao"] == "sobrancelha"]
    assert novos and novos[0]["data_vencimento"].endswith("-10-12"), novos


# ---------------------------------------------------------------------------
# P1-5 — pendencia de CONVERSA virando item fantasma
# ---------------------------------------------------------------------------

def test_oferta_de_remarcar_nao_vira_item_fantasma(usuario):
    """`_resgatar_pendencia` salva como lembrete o que ficou preso no
    PENDING — e esta certo pra item que a PESSOA mandou. A oferta de
    remarcar nao e isso: e o bot esperando resposta a uma pergunta DELE.
    Resgatar viraria um lembrete de unha, sem data, que ninguem pediu."""
    wa_bot.PENDING[TELEFONE] = {"tipo": "confirmar_retorno",
                                "descricao": "fazer as unhas",
                                "sugestao": {"dias": 21,
                                             "proxima": "2026-09-19"},
                                "quando": tempo.agora()}
    antes = len(db.list_items(usuario["id"]))
    resgatado = wa_bot._resgatar_pendencia(usuario, TELEFONE)
    assert resgatado == "", resgatado
    assert len(db.list_items(usuario["id"])) == antes, (
        "o bot inventou um item que a pessoa nunca pediu")
    assert wa_bot.PENDING.get(TELEFONE) is None, "a pendencia continuou presa"


# ---------------------------------------------------------------------------
# P1-7 — PIX sem CRC devolvia o OCR inteiro
# ---------------------------------------------------------------------------

def test_pix_truncado_nao_vira_codigo():
    """Sem o CRC final o payload nao e aceito pelo banco de jeito nenhum.

    Devolver "o resto do texto" arrastava nome, CPF e endereco do
    beneficiario pra mensagem da pessoa — e, pelo caminho de falha, pro log.
    """
    ocr = ("PIX COPIA E COLA\n"
           "00020126580014BR.GOV.BCB.PIX0136abc\n"
           "JOAO DA SILVA CPF 123.456.789-00\n"
           "Rua das Flores 100 Telefone 11 98888-7777")
    assert boleto.codigo_de_pagamento(ocr) is None


# ---------------------------------------------------------------------------
# P1-8 — botao explicito era ignorado
# ---------------------------------------------------------------------------

def test_botao_explicito_chega_no_envio(monkeypatch):
    """A proposta de documento JA SABE seus botoes, e `escolher` nao casa
    com essas frases novas. Sem passar a lista, a pergunta saia como texto
    puro e a pessoa tinha que DIGITAR "confirmar" — o oposto do pedido."""
    import canal
    monkeypatch.setattr(canal, "OFICIAL", True, raising=False)
    vistos = {}

    def _fake_enviar(number, texto, bts):
        vistos["botoes"] = bts
        return True

    monkeypatch.setattr(botoes, "enviar", _fake_enviar)
    ok = botoes.enviar_resposta("5511999999999", "Isso parece uma *CNH*.",
                                lambda n, t: True,
                                botoes=["Confirmar", "Ajustar", "Esquece"])
    assert ok
    assert vistos.get("botoes") == ["Confirmar", "Ajustar", "Esquece"], vistos


def test_botao_explicito_cai_pra_texto_quando_o_corpo_e_gigante(monkeypatch):
    """Interativo com corpo acima do limite a Meta engole SEM AVISAR — a
    pessoa fica sem resposta nenhuma. Texto puro chega."""
    import canal
    monkeypatch.setattr(canal, "OFICIAL", True, raising=False)
    monkeypatch.setattr(botoes, "enviar",
                        lambda *a: pytest.fail("mandou interativo gigante"))
    caiu = {}
    ok = botoes.enviar_resposta(
        "5511999999999", "x" * (botoes.MAX_CORPO + 1),
        lambda n, t: caiu.setdefault("texto", True),
        botoes=["Confirmar"])
    assert ok and caiu.get("texto"), "nao caiu pro texto puro"


# ---------------------------------------------------------------------------
# P2 — codigo de pagamento velho sobrevivendo a virada do mes
# ---------------------------------------------------------------------------

def test_codigo_de_pagamento_morre_na_virada(usuario):
    """Cada boleto tem o seu: o de setembro nao paga outubro.

    Mantendo a coluna, o aviso do mes seguinte sairia com o codigo do mes
    passado — e ou o banco recusa (e ela para de confiar na mensagem) ou ela
    paga de novo o que ja pagou.
    """
    hoje = tempo.hoje()
    item_id = db.add_item(
        user_id=usuario["id"], tipo="despesa", categoria="Contas",
        descricao="conta de luz", valor_reais=187.45,
        data_vencimento=(hoje - _dt.timedelta(days=1)).isoformat(),
        status="pendente", recorrencia="mensal:20",
        codigo_pagamento="34191790010104351004791020150008291070026000",
        codigo_tipo="boleto")

    scheduler.roll_recurring(ref=hoje)
    item = db.get_item(item_id)
    assert item["data_vencimento"] > hoje.isoformat(), item
    assert not item["codigo_pagamento"], (
        "o codigo do mes passado ficou no item do mes que vem")
    assert not item["codigo_tipo"], item


def test_valor_com_ponto_nao_esconde_o_codigo():
    """P2 do prefixo: o descarte e por BLOCO, nunca por contagem.

    Cortar "os N ultimos digitos" devolveria um codigo de 48 digitos que
    ninguem conferiu — 44, 47 e 48 sao todos tamanhos validos. Bloco que
    nao fecha vira None, que e a resposta honesta.
    """
    linha = "34191.79001 01043.510047 91020.150008 2 91070026000"
    achado = boleto.codigo_de_pagamento("Valor do documento 187.40 " + linha)
    assert achado and achado["colavel"] == (
        "34191790010104351004791020150008291070026000"), achado
