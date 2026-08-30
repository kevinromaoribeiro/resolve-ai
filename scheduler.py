"""
scheduler.py — Motor de Disparo Proativo & Anti-Churn do RESOLVE AI.

Executado sob demanda pelo botão de testes na sidebar do app
("⚡ Executar Motor de Disparo Proativo"), simulando o cronjob diário.

Duas checagens:
1. Vencimentos em D+3 com 1-Click Buy (link de afiliado) para itens físicos.
2. Anti-Churn: usuários inativos há mais de 10 dias recebem mensagem
   de reativação de utilidade imediata.

Retorna uma lista de "dispatches" (dicts) que o app renderiza como se
fossem mensagens proativas de WhatsApp.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import os
import unicodedata
import tempo
from typing import Optional

import db

CHURN_THRESHOLD_DAYS = 10
CHURN_COOLDOWN_DAYS = 7          # anti-churn no máx. 1x por semana
# SÓ D-1. Avisar em D-3, D-1 e no dia é três mensagens pelo mesmo boleto —
# não é ajudar, é encher o saco, e é assim que o usuário silencia o bot.
#
# ESTE É O DEFAULT DO MÓDULO, e não um override do `wa_bot`, desde a rodada 2
# da auditoria M2.5: o `app.py` importa `scheduler` SEM importar `wa_bot`,
# então o painel simulava D-3/D-1/D-0 enquanto a produção mandava só D-1.
# Painel divergindo do WhatsApp é como o dono para de confiar no painel.
DUE_ALERT_DAYS = {1}

# ANTECEDÊNCIA MAIOR PARA OBRIGAÇÃO ANUAL DE VEÍCULO (M2.5).
#
# D-3 é a antecedência certa pra conta de luz e errada pra licenciamento: o
# prazo é um MÊS inteiro, o valor é alto, e se os três dias caírem numa emenda
# de feriado a pessoa simplesmente não resolve. Avisar em D-30 é o que
# transforma "você perdeu" em "dá tempo".
#
# O gatilho é a CATEGORIA, não a descrição. Categoria é campo estrutural, com
# lista fechada em db.VALID_CATEGORIES; farejar a palavra "licenciamento" no
# texto seria a mesma regra-por-palavra-chave que já custou quatro rodadas de
# auditoria no M2.1. E fica restrito: se a antecedência de 30 dias vazar pro
# resto, o bot vira o que avisa da conta de luz um mês antes — e aí a pessoa
# silencia o bot inteiro.
DUE_ALERT_DAYS_POR_CATEGORIA = {"Veículo": {30, 7, 1}}

# A JANELA E OS DIAS DE AVISO SÃO UM SÓ AJUSTE, e por isso mudam juntos.
#
# `DUE_WINDOW_DAYS` é o filtro de SQL: item mais distante que isso nem chega a
# ser lido. Quem muda o conjunto de dias e esquece a janela DESLIGA o aviso em
# silêncio — nenhum erro, nenhum log, a lista só vem vazia. Aconteceu comigo
# nesta própria mudança: o D-30 estava certo no filtro e não disparava, porque
# o `wa_bot` sobrescreve a janela para 1 no import e eu não tinha visto.
DUE_WINDOW_DAYS = 3


def definir_politica_de_aviso(padrao=None, por_categoria=None) -> None:
    """Troca a política de aviso de vencimento sem deixar a janela pra trás."""
    global DUE_ALERT_DAYS, DUE_ALERT_DAYS_POR_CATEGORIA, DUE_WINDOW_DAYS
    if padrao is not None:
        DUE_ALERT_DAYS = set(padrao)
    if por_categoria is not None:
        DUE_ALERT_DAYS_POR_CATEGORIA = {k: set(v)
                                        for k, v in por_categoria.items()}
    dias = set(DUE_ALERT_DAYS)
    for v in DUE_ALERT_DAYS_POR_CATEGORIA.values():
        dias |= set(v)
    DUE_WINDOW_DAYS = max(dias or {0})


definir_politica_de_aviso()
QUIET_START, QUIET_END = 21, 8   # silêncio 21h–8h (exceto alarme com hora)

# O mini-podcast so dispara quando o dono liga. Ver `check_podcast`.
# LIGADO POR PADRAO desde 30/08/2026: o Kevin ouviu as cinco amostras e
# aprovou ("ouvi todos e gostei, estao muito bons; pode por no ar, padrao pra
# todos novos clientes"). A variavel continua existindo pra DESLIGAR sem
# deploy, que e o que importa numa emergencia.
# FAIL-CLOSED NA DIGITACAO (auditoria M5.4, P1-4): so LIGA com valor que a
# gente reconhece como "ligado", ou com a variavel ausente (o default novo).
# Qualquer outra coisa DESLIGA — "desligado", "OFF!", "n" digitados as
# pressas no meio de uma emergencia nao podem manter a feature no ar.
PODCAST_ATIVO = (os.environ.get("PODCAST_ATIVO", "") or "").strip().lower() \
    in ("", "1", "true", "sim", "on", "yes")

# TODO KIND QUE O MOTOR PODE EMITIR, declarado num lugar só.
#
# Serve pra uma pergunta que antes não tinha resposta: existe momento proativo
# que, FORA da janela de 24h, some sem template? Kind novo entra aqui e o
# teste exige que ele tenha template em `templates.KIND_TEMPLATE` ou esteja em
# `templates.KINDS_SEM_TEMPLATE` — decidido, não esquecido.
#
# `test_o_inventario_de_kinds_esta_completo` varre o código-fonte e cobra a
# volta: kind emitido e não declarado aqui reprova. Sem isso, esta lista
# envelheceria em silêncio, que é o defeito que ela existe pra evitar.
KINDS_PROATIVOS = {
    "vencimento", "1-click-buy", "anti-churn", "trial-ending", "arquivado",
    "vencido", "hora", "resumo", "winback", "gastos", "retorno", "podcast", "podcast-convite", "podcast-dia",
} | {f"trial_d{n}" for n in range(1, 13)}


def _in_quiet_hours(now: Optional[datetime] = None) -> bool:
    h = (now or tempo.agora()).hour
    return h >= QUIET_START or h < QUIET_END


def _fmt_br(iso: Optional[str]) -> str:
    if not iso:
        return "sem data"
    y, m, d = iso.split("-")
    return f"{d}/{m}"


def check_due_items(ref: Optional[date] = None) -> list[dict]:
    """Checagem 1: vencimentos — avisa em D-3, D-1 e no dia, 1x por dia
    por item (dedup via log de disparos)."""
    ref = ref or tempo.hoje()
    dispatches: list[dict] = []
    for user in db.list_users():
        if not db.user_can_receive(user):
            continue  # trial expirado sem pagamento: silêncio (exceto winback)
        # A JANELA DE LEITURA COBRE A MAIOR ANTECEDÊNCIA POSSÍVEL.
        #
        # `DUE_WINDOW_DAYS` é o filtro de SQL e vale 1 em produção. Item com
        # antecedência própria (CNH: 60 e 30 dias) simplesmente NÃO SERIA LIDO
        # — nenhum erro, nenhum log, o aviso só não sairia. É a armadilha que
        # o comentário do `definir_politica_de_aviso` descreve, e ela pega de
        # novo aqui porque a antecedência agora também vem do item.
        #
        # Ler mais linhas não solta mais mensagem: quem não bate com `alerta`
        # cai fora logo abaixo.
        due = db.items_due_within(
            user["id"], days=max(DUE_WINDOW_DAYS, db.AVISO_MAX_DIAS), ref=ref)
        for item in due:
            if "(lembrete de demonstração)" in (item.get("descricao") or ""):
                continue  # o trial guiado entrega esse momento (d4)
            rec = item.get("recorrencia") or ""
            if rec == "diaria" or rec.startswith("horas"):
                continue  # o alarme de hora já cobre; evita aviso duplo
            if item.get("data_vencimento"):
                # UMA LINHA RUIM NAO PODE MATAR O CICLO INTEIRO.
                #
                # O `check_overdue` ganhou esta blindagem na v23.4 (P1-5) e o
                # `check_due_items` ficou de fora — a janela era de 1 dia, e
                # uma data podre praticamente nunca chegava ate aqui. A
                # janela agora vai a 90 dias, e a auditoria M3.6 (P0-2)
                # mostrou o resultado: um "31/09" lido de uma foto derrubava
                # o motor proativo de TODOS os usuarios, todo ciclo, ate
                # alguem apagar a linha na mao. O item ruim e pulado e o log
                # diz qual e; o resto do ciclo segue.
                try:
                    y, m, d = map(int, str(item["data_vencimento"]).split("-"))
                    days_left = (date(y, m, d) - ref).days
                except (ValueError, TypeError, AttributeError):
                    import logging
                    logging.getLogger("resolveai").error(
                        "[vencimento] item %s com data invalida (%r) - pulado",
                        item.get("id"), item.get("data_vencimento"))
                    continue
                # ANTECEDENCIA DO ITEM SOMA COM A VESPERA, nao substitui
                # (auditoria M3.6, P1-1). Com `or`, "60,30" APAGAVA o D-1: a
                # CNH era avisada 60 e 30 dias antes e ficava muda na
                # vespera, e a nota fiscal (so D-30) perdia a vespera de vez.
                # A vespera e a rede de baixo de todo item com data — nada
                # que a gente adicione pode tirar ela.
                _do_item = db.dias_de_aviso(item)
                alerta = DUE_ALERT_DAYS_POR_CATEGORIA.get(
                    item.get("categoria") or "", DUE_ALERT_DAYS)
                if _do_item:
                    alerta = set(alerta) | set(_do_item)
                if days_left not in alerta:
                    # REDE DE SEGURANCA DO DIA DO VENCIMENTO.
                    #
                    # A politica e avisar na vespera. Se o bot estava fora do
                    # ar naquele dia (apagao da VPS em 25-28/08/2026), ou o
                    # teto diario cortou, ou a pessoa estava fora da janela, o
                    # aviso simplesmente nunca saiu — e no dia do vencimento
                    # ela nao ouve nada. So no dia seguinte recebe "venceu e
                    # nao vi a baixa", quando ja e tarde.
                    #
                    # `dispatched_ever_item` (nao `dispatched_today`, que e
                    # por dia) garante que isto NAO duplica: quem foi avisado
                    # na vespera ja tem o carimbo e cai fora aqui.
                    #
                    # Vale so pra "vencimento". Link de afiliado nao ganha
                    # caminho de envio novo.
                    # `criado_hoje`: a rede e pra aviso PERDIDO, nao pra eco.
                    # Quem acabou de mandar "pagar o condominio hoje" sabe que
                    # e hoje; devolver "passando pra lembrar" segundos depois
                    # e uma vibracao que nao informa nada.
                    _cri = str(item.get("data_criacao") or "")[:10]
                    criado_hoje = _cri == ref.isoformat()
                    if (days_left != 0
                            or criado_hoje
                            or item.get("link_afiliado")
                            or db.dispatched_ever_item("vencimento",
                                                       item["id"])):
                        continue
                if days_left == 0 and item.get("hora_alvo"):
                    continue  # D-0 com hora marcada: o alarme ⏰ é o aviso
            kind = "1-click-buy" if item.get("link_afiliado") else "vencimento"
            if db.dispatched_today(kind, user["id"], item["id"]):
                continue
            first_name = user["nome"].split()[0]
            venc = _fmt_br(item["data_vencimento"])
            # Mensagem extra deste disparo (hoje: so o codigo de pagamento,
            # que vai sozinho pra que o toque-e-copia entregue so ele).
            # Zerado por ITEM: sem isto o codigo de um boleto vazaria pro
            # lembrete do proximo item do mesmo ciclo.
            tem_codigo = False
            if item.get("link_afiliado"):
                msg = (
                    f"⏰ {first_name}, seu item *{item['descricao']}* está "
                    f"programado para {venc}.\n\n"
                    f"🛒 *Resolver em 1 clique* (reposição com o melhor preço):\n"
                    f"{item['link_afiliado']}\n\n"
                    f"Responda *feito* quando resolver que eu baixo da sua lista."
                )
            else:
                is_conta = (item.get("tipo") == "despesa"
                            or item.get("valor_reais"))
                valor = (f" de *R$ {item['valor_reais']:.2f}*".replace(".", ",")
                         if item.get("valor_reais") else "")
                if is_conta:
                    # O CÓDIGO DE PAGAMENTO SAI AQUI (M3.5) — e só aqui.
                    #
                    # É o único momento em que ele serve: a pessoa está
                    # abrindo o app do banco. Guardado desde a foto do boleto,
                    # em coluna própria, sem nunca passar pela descrição.
                    import boleto as _bol
                    _cod = ({"tipo": item.get("codigo_tipo"),
                             "colavel": item.get("codigo_pagamento")}
                            if item.get("codigo_pagamento") else None)
                    msg = (
                        f"💡 {first_name}, passando pra lembrar: "
                        f"*{item['descricao']}*{valor} vence em *{venc}*.\n\n"
                        f"Quando pagar, é só me dizer *paguei* que eu dou "
                        f"baixa. Se quiser adiar o aviso, responda *adiar*."
                        + _bol.aviso_de_codigo(_cod)
                    )
                    # O CÓDIGO NÃO SAI SOZINHO — SAI QUANDO PEDIDO.
                    #
                    # O WhatsApp não tem botão que copia fora de template de
                    # autenticação (categoria de OTP, que não pode ser usada
                    # pra cobrança). A alternativa era mandar o código numa
                    # segunda mensagem, e ela custa caro: DOBRA o volume
                    # proativo por boleto, que é exatamente a métrica do
                    # termômetro anti-bloqueio (`db.pulso_envio`) — e este
                    # número já foi restringido duas vezes.
                    #
                    # Com o botão é uma mensagem só. E a resposta ao toque é
                    # REATIVA: não conta como proativa, não gasta o teto
                    # diário da pessoa e não entra na razão de ritmo.
                    #
                    # Aqui viaja só o SINAL de que existe código. O código em
                    # si fica no banco, fora do dict de disparo — e portanto
                    # fora de qualquer log que serialize o disparo.
                    #
                    # `bool(_cod)`, nao `True`: a maioria das contas nao
                    # tem codigo nenhum, e prometer "toque em Copiar
                    # codigo" pra quem nao tem e o bot oferecendo o que
                    # nao pode entregar.
                    tem_codigo = bool(_cod)
                else:
                    msg = (
                        f"📌 {first_name}, lembrete: *{item['descricao']}* "
                        f"— marcado para *{venc}*.\n\n"
                        f"Já resolveu? Responda *feito* que eu tiro da lista. "
                        f"Quer adiar? É só dizer *adiar*."
                    )
            dispatches.append({
                "user_id": user["id"],
                "user_nome": user["nome"],
                "telefone": user["telefone"],
                "item_id": item["id"],
                "kind": kind,
                "message": msg,
                # Existe codigo de pagamento guardado pra este item? So o
                # SINAL: quem decide os botoes precisa saber, e o codigo em si
                # nao tem por que viajar no dict de disparo.
                "tem_codigo": tem_codigo,
                # `quando` E O QUE O TEMPLATE MOSTRA fora da janela de 24h.
                # Sem ele o corpo saia "vence em *em breve*" em 100% dos
                # casos — e essa e a UNICA mensagem que chega em quem passou
                # 24h sem falar com o bot. Texto que promete data e entrega
                # "em breve" e o mesmo defeito de data errada, com outro
                # nome. Nao tem default esperto aqui de proposito: quem
                # produz o disparo e quem sabe a data.
                "quando": venc,
            })
    return dispatches


def check_podcast_dia(ref: Optional[datetime] = None) -> list[dict]:
    """A pergunta de qual dia, 10 min depois do primeiro episodio.

    MESMA CHAVE DO `check_podcast` (auditoria M5.4, P1-3). Sem isso,
    `PODCAST_ATIVO=0` calava o convite e deixava a pergunta do dia saindo —
    a pessoa recebia "que dia da semana voce prefere?" de uma feature que o
    dono acabou de desligar.

    Ela volta no M4.7 porque agora a resposta VALE: com o template, o
    lembrete sai no dia escolhido mesmo que a pessoa nao fale com o bot ha
    uma semana. Antes disso, perguntar era prometer o que a gente nao
    honrava — e por isso a pergunta tinha saido.
    """
    if not PODCAST_ATIVO:
        return []
    import podcast as _pod

    agora = ref or tempo.agora()
    saida: list[dict] = []
    for u in db.podcast_a_perguntar_o_dia(
            ref=agora, minutos=_pod.MINUTOS_ATE_PERGUNTAR_O_DIA):
        if not db.user_can_receive(u):
            continue
        if db.dispatched_today("podcast-dia", u["id"]):
            continue
        p = _pod.pergunta_do_dia(nome=u.get("nome") or "")
        saida.append({
            "user_id": u["id"], "user_nome": u["nome"],
            "telefone": u["telefone"], "item_id": None,
            "kind": "podcast-dia", "message": p["texto"],
            "botoes": p["botoes"],
            "quando": _fmt_br(agora.date().isoformat()),
        })
    return saida


def check_churn(ref: Optional[datetime] = None) -> list[dict]:
    """Checagem 2: gatilho D+10 de inatividade — no máx. 1x por semana."""
    dispatches: list[dict] = []
    for user in db.inactive_users(days=CHURN_THRESHOLD_DAYS, ref=ref):
        if not db.user_can_receive(user):
            continue
        if db.dispatched_within("anti-churn", user["id"],
                                days=CHURN_COOLDOWN_DAYS):
            continue
        if db.dispatch_count("anti-churn", user["id"]) >= CHURN_MAX_ATTEMPTS:
            continue  # desiste com elegância após 3 tentativas
        first_name = user["nome"].split()[0]
        msg = (
            f"Opa, {first_name}! Vi que sua semana foi corrida. Que tal "
            f"esvaziar a cabeça agora? Manda por áudio em 10 segundos ou "
            f"tira print de alguma encomenda para rastrear ou da "
            f"quilometragem do carro. Eu organizo tudo."
        )
        dispatches.append({
            "user_id": user["id"],
            "user_nome": user["nome"],
            "telefone": user["telefone"],
            "item_id": None,
            "kind": "anti-churn",
            "message": msg,
        })
    return dispatches


def check_trial_ending() -> list[dict]:
    """Checagem 3: trial termina amanhã -> aviso com link de pagamento."""
    import os
    payment = os.environ.get("PAYMENT_LINK", "https://SEU-LINK-DE-PAGAMENTO")
    dispatches: list[dict] = []
    for user in db.trial_ending_users(days_left=1):
        if db.dispatched_ever("trial-ending", user["id"]):
            continue
        if db.nudge_already_sent(user, "d6_fim"):
            continue  # o trial guiado já fez o push do D6 (evita msg dupla)
        first_name = user["nome"].split()[0]
        msg = (
            f"⏳ {first_name}, seu teste grátis termina *amanhã*. "
            f"Curtiu ter a cabeça mais leve?\n\n"
            f"💳 Continue por R$ 19,90/mês: {payment}\n\n"
            f"Se não assinar, tudo bem — seus dados ficam guardados 30 dias "
            f"caso mude de ideia."
        )
        dispatches.append({
            "user_id": user["id"],
            "user_nome": user["nome"],
            "telefone": user["telefone"],
            "item_id": None,
            "kind": "trial-ending",
            "message": msg,
        })
    return dispatches


OVERDUE_NUDGE_DAYS = 1           # cobrança única D+1 pós-vencimento
ARCHIVE_AFTER_DAYS = 15          # cadáver arquivado (e avisado) em D+15
CHURN_MAX_ATTEMPTS = 3           # anti-churn desiste após 3 tentativas


def roll_recurring(ref: Optional[date] = None) -> int:
    """Rola itens recorrentes vencidos/concluídos para a próxima ocorrência."""
    ref = ref or tempo.hoje()
    rolls: list[tuple] = []
    for item in db.recurring_to_roll(ref):
        rec = item["recorrencia"]
        venc = item.get("data_vencimento") or ref.isoformat()
        y, m, d = map(int, venc.split("-"))
        base = date(y, m, d)
        hora = item.get("hora_alvo")
        if rec == "diaria":
            nxt = max(base + timedelta(days=1), ref)
        elif rec.startswith("semanal:"):
            alvo = int(rec.split(":")[1])
            nxt = base + timedelta(days=1)
            while nxt.weekday() != alvo or nxt < ref:
                nxt += timedelta(days=1)
        elif rec.startswith("mensal:"):
            dd = int(rec.split(":")[1])
            y2, m2 = base.year + (base.month == 12), base.month % 12 + 1
            try:
                nxt = date(y2, m2, dd)
            except ValueError:
                nxt = date(y2, m2, 28)
            while nxt < ref:
                y2, m2 = nxt.year + (nxt.month == 12), nxt.month % 12 + 1
                try:
                    nxt = date(y2, m2, dd)
                except ValueError:
                    nxt = date(y2, m2, 28)
        elif rec.startswith("horas:"):
            step = int(rec.split(":")[1])
            if hora:
                h, mi = map(int, hora.split(":"))
                prox = datetime(base.year, base.month, base.day, h, mi) \
                    + timedelta(hours=step)
                while prox < tempo.agora():
                    prox += timedelta(hours=step)
                rolls.append((item["id"], prox.date().isoformat(),
                              prox.strftime("%H:%M")))
            continue
        else:
            continue
        rolls.append((item["id"], nxt.isoformat(), hora))
    db.roll_items_batch(rolls)
    return len(rolls)


def check_overdue(ref: Optional[date] = None) -> list[dict]:
    """Cobrança única D+1 e arquivamento com aviso em D+15 (não-recorrentes)."""
    ref = ref or tempo.hoje()
    dispatches: list[dict] = []
    pendentes: dict = {}          # user_id -> [(item, atraso, primeiro_nome)]
    for item in db.overdue_items(days_ago=OVERDUE_NUDGE_DAYS, ref=ref):
        if "(lembrete de demonstração)" in (item.get("descricao") or ""):
            continue
        u = db.get_user(item["user_id"])
        if not u or not db.user_can_receive(u):
            continue
        # UMA LINHA RUIM NAO PODE MATAR O CICLO INTEIRO (auditoria v23.4,
        # P1-5). `map(int, venc.split("-"))` estourava ValueError com uma
        # data fora do ISO e derrubava check_overdue no meio do laco: NINGUEM
        # — nem os outros usuarios — recebia aviso de vencimento naquele
        # ciclo. O item problematico e pulado e o dono e avisado; o resto do
        # ciclo segue. Nao e `except: pass`: o erro sobe pro log com o id.
        venc = item["data_vencimento"]
        try:
            y, m, d = map(int, str(venc).split("-"))
            atraso = (ref - date(y, m, d)).days
        except (ValueError, TypeError, AttributeError):
            import logging
            logging.getLogger("resolveai").error(
                "[vencidos] item %s com data invalida (%r) - pulado",
                item.get("id"), venc)
            continue
        first = (item.get("user_nome") or "").split()[0] or "Oi"
        if db.item_silenciado(item["id"]):
            continue          # M1.5 — silenciado nao cobra tambem
        if atraso >= ARCHIVE_AFTER_DAYS:
            # ARQUIVA SO DEPOIS QUE O AVISO COMPROVADAMENTE SAIU.
            #
            # Antes, `archive_item` rodava aqui na GERACAO do disparo. Só que
            # "arquivado" esta em KINDS_SEM_TEMPLATE: fora da janela de 24h a
            # mensagem nao sai. E quem tem item parado ha 15 dias e justamente
            # quem nao conversa ha semanas — a condicao que dispara o
            # arquivamento e quase a mesma que impede o aviso de sair. O item
            # sumia da lista da pessoa, ela nunca era avisada, e como
            # `overdue_items` so olha status='pendente' o disparo nunca mais
            # era regerado. Perda silenciosa de dado do usuario.
            #
            # O carimbo de `dispatched_ever_item` so existe se o `falar`
            # retornou enviado (wa_bot so chama `log_dispatch` no sucesso).
            # Entao: um ciclo avisa, o seguinte arquiva. Se a pessoa responder
            # no meio, o item sai de 'pendente' e nunca chega a ser arquivado
            # — que e exatamente o certo.
            if db.dispatched_ever_item("arquivado", item["id"]):
                db.archive_item(item["id"])
            else:
                dispatches.append({
                    "user_id": item["user_id"], "telefone": item["telefone"],
                    "item_id": item["id"], "kind": "arquivado",
                    "message": (f"Vou arquivar *{item['descricao']}* — venceu há "
                                f"{atraso} dias sem baixa. Se ainda estiver em "
                                f"aberto, me manda de novo que eu reagendo. 🗂️")})
        elif atraso >= OVERDUE_NUDGE_DAYS:
            if not db.dispatched_ever_item("vencido", item["id"]):
                pendentes.setdefault(item["user_id"], []).append((item, atraso,
                                                                  first))

    # UMA MENSAGEM POR PESSOA, NÃO UMA POR ITEM.
    #
    # Em 04/08 às 07:59–08:00 o Kevin recebeu QUATRO mensagens em um minuto,
    # uma por item vencido. No WhatsApp isso não é diligência, é rajada: o
    # celular vibra quatro vezes e a pessoa arquiva a conversa. O bot que
    # existe pra tirar peso da cabeça vira mais uma fonte de barulho.
    #
    # Agora os vencidos de cada pessoa saem juntos, numa lista, com UMA
    # vibração. E o dedup continua por item — nenhum some.
    for uid, grupo in pendentes.items():
        item0, _, first = grupo[0]
        if len(grupo) == 1:
            it, atraso, _ = grupo[0]
            quando = "ontem" if atraso == 1 else f"há {atraso} dias"
            msg = (f"{first}, *{it['descricao']}*{_valor_txt(it)} venceu "
                   f"{quando} e não vi a baixa.\n\n"
                   f"{_pergunta_baixa(it)} — ou *adiar* se precisar de fôlego.")
        else:
            linhas = []
            for it, atraso, _ in grupo[:8]:
                quando = "ontem" if atraso == 1 else f"há {atraso} dias"
                linhas.append(f"• *{it['descricao']}*{_valor_txt(it)} "
                              f"— venceu {quando}")
            extra = (f"\n• _+{len(grupo) - 8} outro(s)_"
                     if len(grupo) > 8 else "")
            msg = (f"{first}, {len(grupo)} coisas venceram e eu não vi a "
                   f"baixa:\n\n" + "\n".join(linhas) + extra +
                   f"\n\nResponde *feito* + o nome do que já resolveu, ou "
                   f"*adiar* + o nome pra eu empurrar.")
        # um disparo por item no log (dedup preservado), uma mensagem só
        for i, (it, _, _) in enumerate(grupo):
            dispatches.append({
                "user_id": uid, "telefone": it["telefone"],
                "item_id": it["id"], "kind": "vencido",
                # só o primeiro carrega texto; os outros são registro de dedup
                "message": msg if i == 0 else "",
            })
    return dispatches


def _valor_txt(it: dict) -> str:
    if not it.get("valor_reais"):
        return ""
    return f" ({_brl(it['valor_reais'])})"


def _pergunta_baixa(it: dict) -> str:
    """"Já pagou?" só faz sentido quando existe dinheiro no item.

    Em 04/08 o bot perguntou "Já pagou?" sobre *definir próxima pós
    graduação*. Não há o que pagar — a pergunta expõe que ele não entende o
    que guardou.
    """
    tem_dinheiro = (it.get("valor_reais")
                    or it.get("tipo") in ("despesa", "documento"))
    return ("Já pagou? Responda *pago*" if tem_dinheiro
            else "Já resolveu? Responda *feito*")


def check_time_alarms(ref: Optional[datetime] = None) -> list[dict]:
    """Checagem 0 (v6.3): alarmes com hora — itens de HOJE cuja hora_alvo
    chegou. Dispara no minuto (rodando o cron a cada 5-15 min), 1x por item.
    Ignora horário de silêncio: hora explícita é pedido explícito."""
    now = ref or tempo.agora()
    dispatches: list[dict] = []
    for item in db.items_due_at_time(now):
        u = db.get_user(item["user_id"])
        if not u or not db.user_can_receive(u):
            continue
        if db.dispatched_today("hora", item["user_id"], item["id"]):
            continue
        # M1.5 — item silenciado continua na lista, mas para de tocar.
        # Alerta que toca depois de tres "agora nao" e alerta que a pessoa
        # silencia — e quando ela silencia, silencia o bot inteiro.
        if db.item_silenciado(item["id"]):
            continue
        first_name = (item.get("user_nome") or "").split()[0] or "Oi"
        valor = (f" ({'R$ %.2f' % item['valor_reais']})".replace(".", ",")
                 if item.get("valor_reais") else "")
        # QUANTO ATRASOU? O texto muda, o aviso nao deixa de sair.
        #
        # Caso da Carol (11/08): as 21:43 ela cadastrou "dentista 11/08 as
        # 16:00" — cinco horas no passado. O cron viu hora_alvo <= agora e
        # mandou "⏰ chegou a hora" de um compromisso que tinha sido de
        # tarde. Dizer "chegou a hora" cinco horas depois e mentira, e
        # ensina a pessoa a ignorar o ⏰ — que e justamente o que ela paga
        # pra receber.
        #
        # A saida NAO e filtrar: sumir com o lembrete e pior que avisar
        # atrasado. E dizer a verdade.
        _atraso = 0
        try:
            _h, _m = str(item["hora_alvo"])[:5].split(":")
            _marcado = now.replace(hour=int(_h), minute=int(_m),
                                   second=0, microsecond=0)
            _atraso = int((now - _marcado).total_seconds() // 60)
        except Exception:
            _atraso = 0
        # M2.0: qual das tres variantes de texto e esta? Sem isso o template
        # "Chegou a hora" seria usado tambem pro alarme atrasado e pro
        # escalonamento — dizendo "chegou a hora" cinco horas depois (caso
        # da Carol) e voltando a cobrar quem o M1.5 mandou parar de cobrar.
        variante = "na_hora"
        if _atraso > db.ALARME_JANELA_MIN:
            _quanto = (f"{_atraso // 60}h" if _atraso >= 60
                       else f"{_atraso} min")
            variante = "atrasado"
            msg = (f"{first_name}, passou da hora de *{item['descricao']}*"
                   f"{valor} — era às {item['hora_alvo']} "
                   f"(há {_quanto}).\n"
                   f"Ainda vale? Responda *feito* se já resolveu, ou me "
                   f"manda a data certa que eu reagendo.")
        else:
            msg = (f"⏰ {first_name}, chegou a hora: *{item['descricao']}*"
                   f"{valor} — você me pediu pra avisar às {item['hora_alvo']}.\n"
                   f"Responda *feito* que eu dou baixa, ou *adiar 1h*.")

        # M1.5 — ESCALONAMENTO DE TOM. Nao escala pra cobranca: escala pra
        # HONESTIDADE. Quem promete tirar peso da cabeca nao pode virar mais
        # uma voz cobrando. Na 3a vez o bot assume que o problema pode ser
        # dele (hora errada) e devolve a escolha pra pessoa.
        _snoozes = 0
        try:
            _snoozes = db.dispatch_count_item("adiado", item["id"])
        except Exception:
            _snoozes = 0
        if _snoozes >= db.SNOOZE_LIMITE:
            variante = "escalonado"
            msg = (f"{first_name}, já te chamei "
                   f"{_snoozes}x pro *{item['descricao']}* e não rolou.\n\n"
                   f"Ou o horário tá errado, ou isso não é prioridade "
                   f"agora — e as duas respostas são válidas.\n\n"
                   f"Me diz: *remarcar* ou *tirar da lista*?")
        dispatches.append({
            "user_id": item["user_id"],
            "user_nome": item.get("user_nome", ""),
            "telefone": item["telefone"],
            "item_id": item["id"],
            "kind": "hora",
            "variante": variante,
            "message": msg,
        })
    return dispatches


# ---------------------------------------------------------------------------
# RESUMO SEMANAL (v16)
# ---------------------------------------------------------------------------
# A coluna `dia_resumo` existia desde o v1 e nunca foi lida por ninguém.
# Regra da casa: gatilho, corte e formatação ficam AQUI, em Python. Nada de
# pedir "faça um resumo bonito" pro LLM — resumo é fato de banco, e fato de
# banco não pode oscilar entre uma segunda e outra.

RESUMO_HORA_INICIO = 8    # 8h. Antes disso o _in_quiet_hours já barra.
RESUMO_HORA_LIMITE = 12   # se o app ficou fora a manhã toda, desiste do dia:
                          # resumo de segunda chegando 21h é lixo, não serviço.
RESUMO_JANELA_DIAS = 7    # horizonte da lista "esta semana"
RESUMO_MAX_ITENS = 8      # teto de linhas; o resto vira "+N outros"
RESUMO_MAX_ATRASADOS = 3

# O QUE NÃO ENTRA NO RESUMO.
# "Me lembra de almoçar meio-dia" é rotina, não compromisso: entraria 7 vezes
# na lista da semana e afogaria o boleto que realmente importa. Rotina já tem
# canal próprio — o alarme ⏰ toca na hora certa, todo dia. O resumo semanal é
# sobre o que TEM DATA e CONSEQUÊNCIA: conta, vencimento, documento, revisão.
RESUMO_IGNORA_RECORRENCIA = ("diaria", "horas:")

# LÉXICO DE DINHEIRO.
# Existe porque o categorizador está gravando TUDO como "Outros" — em produção,
# "Cartão de débito" e "comprar frutas" chegam ao banco com exatamente os
# mesmos campos (lembrete / Outros / sem valor / com hora). Sem um sinal
# textual, ou o resumo derruba a conta junto com o recado, ou carrega os dois.
# Isto é remendo consciente: quando `categoria` voltar a ser confiável, o
# léxico vira rede de segurança em vez de critério principal.
_PALAVRAS_DINHEIRO = (
    "conta", "boleto", "fatura", "cartao", "aluguel", "parcela", "prestacao",
    "financiamento", "seguro", "mensalidade", "ipva", "iptu", "imposto",
    "darf", "inss", "luz", "energia", "agua", "gas", "internet", "telefone",
    "celular", "condominio", "escola", "faculdade", "plano de saude",
    "assinatura", "netflix", "spotify", "pagar", "pagamento", "vencimento",
    "multa", "taxa", "emprestimo", "consorcio", "previdencia", "salario",
)
_CATEGORIAS_SERIAS = ("Contas", "Veículo", "Saúde", "Documento")

_DIAS_SEMANA = {"segunda": 0, "terca": 1, "quarta": 2, "quinta": 3,
                "sexta": 4, "sabado": 5, "domingo": 6}
_DIA_CURTO = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn")


def dia_resumo_weekday(valor: Optional[str]) -> Optional[int]:
    """'Segunda-feira' | 'segunda' | 'SEGUNDA FEIRA' | 'Sábado' -> int (0=seg).

    Valor desconhecido devolve None e o usuário simplesmente não recebe —
    melhor não receber do que receber na quinta achando que é segunda.
    """
    chave = _sem_acento(str(valor or "")).lower().strip()
    chave = chave.replace("-", " ").split()
    if not chave:
        return None
    return _DIAS_SEMANA.get(chave[0])


def _brl(v: float) -> str:
    """1234.5 -> 'R$ 1.234,50'."""
    return "R$ " + (f"{v:,.2f}".replace(",", "X")
                    .replace(".", ",").replace("X", "."))


def _e_demo(item: dict) -> bool:
    return "(lembrete de demonstração)" in (item.get("descricao") or "")


def _e_rotina(item: dict) -> bool:
    """Alarme de rotina (almoçar, remédio de 8h em 8h, alongar).

    Critério em Python e não no prompt: `recorrencia` diária ou de N em N
    horas. Não é julgamento sobre a descrição — é o que o próprio usuário
    declarou quando pediu "todo dia às 12h".
    """
    rec = item.get("recorrencia") or ""
    return any(rec == p or rec.startswith(p)
               for p in RESUMO_IGNORA_RECORRENCIA)


def _cheira_a_dinheiro(descricao: Optional[str]) -> bool:
    """A descrição indica conta/compromisso financeiro?"""
    d = _sem_acento(descricao or "").lower()
    return any(p in d for p in _PALAVRAS_DINHEIRO)


def _entra_no_resumo(item: dict) -> bool:
    """Este item merece uma linha no resumo semanal?

    Ordem importa. Em produção o banco chega assim (user 23, 03/08/2026):

        "Esquentar o almoço"  lembrete/Outros  sem valor  12:39  —      -> não
        "fruta"               lembrete/Outros  sem valor  10:00  diaria -> não
        "comprar frutas"      lembrete/Outros  sem valor  12:30  —      -> não
        "Cartão de débito"    lembrete/Outros  sem valor  20:00  —      -> SIM

    Os quatro têm campos estruturados idênticos. Só o texto separa a conta do
    recado, e o critério tem que ser explícito — não palpite de LLM.
    """
    if _e_demo(item) or _e_rotina(item):
        return False
    if item.get("tipo") in ("despesa", "documento"):
        return True                      # dinheiro e documento sempre entram
    if item.get("valor_reais"):
        return True
    if item.get("categoria") in _CATEGORIAS_SERIAS:
        return True
    if _cheira_a_dinheiro(item.get("descricao")):
        return True
    # Sobrou lembrete comum. Se tem hora marcada, é alarme de relógio: o ⏰
    # toca na hora exata e listar de novo aqui é dizer a mesma coisa duas
    # vezes. Sem hora, é planejamento da semana — esse sim entra.
    return not item.get("hora_alvo")


def _linha_item(item: dict, ref: date) -> str:
    """Uma linha de item: quando — o quê — hora — valor."""
    quando = ""
    iso = item.get("data_vencimento")
    if iso:
        y, m, d = map(int, iso.split("-"))
        dt = date(y, m, d)
        dias = (dt - ref).days
        if dias == 0:
            rotulo = "hoje"
        elif dias == 1:
            rotulo = "amanhã"
        else:
            rotulo = f"{_DIA_CURTO[dt.weekday()]} {dt.day:02d}/{dt.month:02d}"
        quando = f"{rotulo} — "
    hora = f" às {item['hora_alvo']}" if item.get("hora_alvo") else ""
    valor = f" ({_brl(item['valor_reais'])})" if item.get("valor_reais") else ""
    return f"{quando}{item['descricao']}{hora}{valor}"


def montar_resumo_semanal(user: dict, ref: Optional[date] = None) -> Optional[str]:
    """Monta o texto do resumo a partir do banco. Sem LLM, sem invenção.

    Devolve None quando não há NADA a dizer (semana sem item, sem atraso e
    sem gasto). Mandar "você não tem nada" toda segunda é exatamente como se
    ensina o usuário a ignorar o bot.
    """
    ref = ref or tempo.hoje()
    uid = user["id"]

    semana = [i for i in db.items_due_within(uid, days=RESUMO_JANELA_DIAS,
                                             ref=ref) if _entra_no_resumo(i)]
    atrasados = [i for i in db.items_overdue_for_user(uid, ref=ref)
                 if _entra_no_resumo(i)]
    ini = (ref - timedelta(days=6)).isoformat()
    gastos = db.spend_by_category_period(uid, ini, ref.isoformat())
    total_gasto = sum(gastos.values())

    if not semana and not atrasados and total_gasto <= 0:
        return None

    first = (user.get("nome") or "").split()
    first = first[0] if first else "Oi"
    linhas = [f"☀️ Bom dia, {first}. Sua semana no Resolve AI:"]

    if semana:
        linhas.append(f"\n📌 *Esta semana* ({len(semana)})")
        for it in semana[:RESUMO_MAX_ITENS]:
            linhas.append("• " + _linha_item(it, ref))
        sobra = len(semana) - RESUMO_MAX_ITENS
        if sobra > 0:
            linhas.append(f"• _+{sobra} outro(s)_")

    if atrasados:
        linhas.append(f"\n⚠️ *Em atraso* ({len(atrasados)})")
        for it in atrasados[:RESUMO_MAX_ATRASADOS]:
            linhas.append(f"• {it['descricao']} — venceu "
                          f"{_fmt_br(it.get('data_vencimento'))}")
        sobra = len(atrasados) - RESUMO_MAX_ATRASADOS
        if sobra > 0:
            linhas.append(f"• _+{sobra} outro(s)_")

    if total_gasto > 0:
        linhas.append(f"\n💸 *Últimos 7 dias*: {_brl(total_gasto)}")
        linhas.append(" · ".join(f"{c} {_brl(v)}"
                                 for c, v in list(gastos.items())[:3]))

    linhas.append("\nResponda *feito* + o nome do item que eu dou baixa.")
    return "\n".join(linhas)


def check_weekly_summary(ref: Optional[datetime] = None) -> list[dict]:
    """Checagem 4: resumo no dia escolhido pelo usuário (default segunda).

    1x por dia (dedup pelo log de disparos), só na janela da manhã.
    """
    now = ref or tempo.agora()
    if not (RESUMO_HORA_INICIO <= now.hour < RESUMO_HORA_LIMITE):
        return []
    hoje = now.date()
    dispatches: list[dict] = []
    for user in db.list_users():
        alvo = dia_resumo_weekday(user.get("dia_resumo"))
        if alvo is None or alvo != hoje.weekday():
            continue
        if (user.get("onboarding_step") or "done") != "done":
            continue  # quem ainda está se cadastrando não leva resumo
        if not db.user_can_receive(user):
            continue
        if db.dispatched_today("resumo", user["id"]):
            continue
        msg = montar_resumo_semanal(user, ref=hoje)
        if not msg:
            continue  # semana limpa: silêncio é a resposta certa
        dispatches.append({
            "user_id": user["id"],
            "user_nome": user["nome"],
            "telefone": user["telefone"],
            "item_id": None,
            "kind": "resumo",
            "message": msg,
        })
    return dispatches


# ---------------------------------------------------------------------------
# RESUMO DE GASTOS — segunda de manhã (M2.5)
# ---------------------------------------------------------------------------
# O resumo semanal que já existia lista COMPROMISSO. Este lista DINHEIRO, que
# é a pergunta que a pessoa não consegue responder sozinha: "pra onde foi?".
#
# Duas regras de produto moram aqui:
#
# 1. QUEM NÃO TEM DADO NÃO RECEBE. Resumo vazio ("você registrou R$ 0,00 em
#    nada") é o jeito mais rápido de ensinar alguém a ignorar o bot — e depois
#    disso os LEMBRETES também passam batido, que é o produto inteiro.
# 2. O CONVITE VARIA. A mesma frase toda segunda vira ruído em três semanas.
#    O rodízio é determinístico (número da semana + id), então não repete na
#    semana seguinte e não depende de guardar estado.

# NUNCA NO MESMO DIA DO OUTRO RESUMO — este e o P1-C da rodada 2.
#
# Os dois sao digests semanais de manha, e o `dia_resumo` default e segunda.
# Ligados no mesmo dia, a pessoa recebia duas proativas em segundos, com
# conteudo sobreposto ("voce tem 3 compromissos, o mais proximo e a internet"
# seguido de "voce registrou R$ 526,90, onde mais pesou: Contas"). Num numero
# que ja levou DUAS restricoes da Meta, empilhar digest e comprar a terceira
# — e e o mesmo motivo pelo qual o aviso de vencimento foi cortado pra D-1.
#
# A escolha e por `dia_resumo`, e nao por "ja disparou hoje": o dedup so e
# marcado no ENVIO, entao no momento em que este check roda o resumo do dia
# ainda nao saiu. Regra estrutural, nao corrida entre dois checks.
GASTOS_DIAS = (0, 1, 2)          # segunda, terca ou quarta de manha
GASTOS_HORA_INICIO = 8
GASTOS_HORA_LIMITE = 12
GASTOS_COOLDOWN_DIAS = 6         # um por semana, no maximo
GASTOS_JANELA_DIAS = 7
# Um lançamento não é resumo, é eco: a pessoa acabou de mandar aquilo.
GASTOS_MIN_LANCAMENTOS = 2

# CONVITE = pedido de USO, nunca de dinheiro. O guardrail de produto vale
# aqui igual: o bot lembra, organiza e registra; nunca paga nem compra. Um
# convite do tipo "me manda que eu pago" seria promessa que o produto não
# cumpre — e a que mais gera pedido de reembolso.
CONVITES_DE_USO = (
    "Chegou boleto essa semana? Me manda a foto que eu guardo a data.",
    "Tem algo que você não pode esquecer? Me diz _\"me lembra de X\"_ que "
    "eu cuido.",
    "Se tiver conta com vencimento essa semana, me manda que eu te aviso "
    "antes.",
    "Consulta, exame, prazo de escola: me manda que eu guardo com a data.",
    "Tem PDF de conta no e-mail? Me encaminha aqui que eu leio e guardo.",
    "Se você tem carro, me diz a placa que eu te aviso do IPVA e do licenciamento.",
)


def convite_de_uso(user_id: int, semana: int) -> str:
    """Rodízio determinístico: não repete na semana seguinte, sem guardar
    estado. Somar o `user_id` evita que a base inteira receba a mesma frase
    na mesma segunda — o que reduz a chance de virar print de grupo."""
    return CONVITES_DE_USO[(int(semana) + int(user_id)) % len(CONVITES_DE_USO)]


def montar_resumo_de_gastos(user: dict,
                            ref: Optional[date] = None) -> Optional[str]:
    """A mensagem, ou None quando não há o que resumir."""
    ref = ref or tempo.hoje()
    try:
        g = db.gastos_da_semana(user["id"], ref=ref, dias=GASTOS_JANELA_DIAS)
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[gastos] falha ao somar a semana do user %s", user.get("id"),
            exc_info=True)
        return None
    if g["n"] < GASTOS_MIN_LANCAMENTOS or g["total"] <= 0:
        return None

    primeiro = (user.get("nome") or "").split()
    primeiro = primeiro[0] if primeiro else "Oi"
    linhas = [f"📊 {primeiro}, o resumo da sua semana: "
              f"*{_brl(g['total'])}* em contas registradas.", ""]
    for categoria, valor in list(g["por_categoria"].items())[:4]:
        linhas.append(f"• {categoria} — {_brl(valor)}")

    # COMPARAÇÃO, não número solto. Foi exatamente o defeito do painel do
    # dono: "R$ 340" não diz se melhorou ou piorou, e sem isso a pessoa não
    # tem o que fazer com o número.
    anterior = g["total_anterior"]
    if anterior > 0:
        delta = g["total"] - anterior
        if abs(delta) < 0.01:
            comp = f"Igualzinho à semana passada ({_brl(anterior)})."
        elif delta > 0:
            comp = (f"Na semana passada foram {_brl(anterior)} — "
                    f"{_brl(abs(delta))} a mais agora.")
        else:
            comp = (f"Na semana passada foram {_brl(anterior)} — "
                    f"{_brl(abs(delta))} a menos agora.")
    else:
        comp = "É a primeira semana que eu tenho pra comparar."
    linhas += ["", comp, "",
               convite_de_uso(user["id"], ref.isocalendar()[1])]
    return "\n".join(linhas)


def dia_de_gastos(user: dict) -> int:
    """Em que dia da semana ESTA pessoa recebe o resumo de gastos.

    Segunda de manhã por padrão, como o dono pediu — mas nunca no mesmo dia
    do resumo de compromissos dela. Quem tem o resumo na segunda (o default)
    recebe os gastos na terça.
    """
    do_resumo = dia_resumo_weekday(user.get("dia_resumo"))
    for d in GASTOS_DIAS:
        if d != do_resumo:
            return d
    return GASTOS_DIAS[0]


def check_gastos_semanais(ref: Optional[datetime] = None) -> list[dict]:
    """Checagem 5: resumo de gastos no início da semana, 1x por semana."""
    now = ref or tempo.agora()
    if now.weekday() not in GASTOS_DIAS:
        return []
    if not (GASTOS_HORA_INICIO <= now.hour < GASTOS_HORA_LIMITE):
        return []
    hoje = now.date()
    dispatches: list[dict] = []
    for user in db.list_users():
        if (user.get("onboarding_step") or "done") != "done":
            continue
        if not db.user_can_receive(user):
            continue
        if now.weekday() != dia_de_gastos(user):
            continue
        # Uma por semana. `dispatched_today` sozinho deixava passar dois
        # envios em dias diferentes se o dia calculado mudasse no meio da
        # semana (a pessoa troca o `dia_resumo` e ganha um resumo extra).
        if db.dispatched_within("gastos", user["id"], GASTOS_COOLDOWN_DIAS):
            continue
        msg = montar_resumo_de_gastos(user, ref=hoje)
        if not msg:
            continue
        dispatches.append({
            "user_id": user["id"],
            "user_nome": user["nome"],
            "telefone": user["telefone"],
            "item_id": None,
            "kind": "gastos",
            "message": msg,
            "semana": hoje.isocalendar()[1],
        })
    return dispatches


def check_winback() -> list[dict]:
    """1 única mensagem 3 dias após o trial expirar sem conversão."""
    dispatches: list[dict] = []
    for user in db.winback_candidates():
        if db.dispatched_ever("winback", user["id"]):
            continue
        first_name = user["nome"].split()[0]
        pend = db.list_items(user["id"], status="pendente")
        gancho = (f"Seu *{pend[0]['descricao']}* continua aqui me esperando. "
                  if pend else "")
        dispatches.append({
            "user_id": user["id"], "user_nome": user["nome"],
            "telefone": user["telefone"], "item_id": None, "kind": "winback",
            "message": (f"Oi {first_name}! Seu teste do Resolve AI acabou "
                        f"há alguns dias. {gancho}Se fez falta, é só assinar "
                        f"que tudo volta na hora — e se não fez, sem "
                        f"problema: essa é a última mensagem que te mando. 🤝"),
        })
    return dispatches


PURGA_DIA_DO_MES = 1     # roda uma vez por mes, dia 1
PURGA_HORA = 4           # de madrugada, longe do horario de gente


def rodar_purga_se_for_o_dia(now: Optional[datetime] = None) -> Optional[dict]:
    """M1.6 — purga de concluidos velhos, 1x por mes.

    SECO por padrao: conta e registra o lacre, mas nao apaga. Delete de dado
    de usuario nao estreia direto em producao — primeiro a gente olha o
    numero por algumas semanas. Ligar de verdade e trocar uma env var.
    """
    import os
    now = now or tempo.agora()
    if now.day != PURGA_DIA_DO_MES or now.hour != PURGA_HORA:
        return None
    if db.dispatched_today("purga-mensal", 0):
        return None
    seco = os.environ.get("PURGA_VALENDO", "0") != "1"
    try:
        r = db.purgar_concluidos(seco=seco)
        db.log_dispatch(0, "purga-mensal")
        return r
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[purga] falhou", exc_info=True)
        return None


def check_retorno(ref: Optional[datetime] = None) -> list[dict]:
    """Depois da baixa de um serviço que repete, oferece marcar o próximo.

    Quem decide o que repete é `recorrencia.sugestao` — unha, sobrancelha,
    dentista sim; conta de luz não. E a espera de ~10h existe pra pergunta
    não chegar com a pessoa ainda saindo do salão.

    SEM TEMPLATE de propósito (`KINDS_SEM_TEMPLATE`): é uma pergunta de
    conveniência, não um compromisso com data. Quem esfriou não precisa
    receber isso fora da janela — e a Meta classificaria como marketing.
    """
    import recorrencia

    agora = ref or tempo.agora()
    dispatches: list[dict] = []
    for linha in recorrencia.pendentes_de_pergunta(ref=agora):
        u = db.get_user(linha["user_id"])
        if not u or not db.user_can_receive(u):
            continue
        p = recorrencia.pergunta(linha["sugestao"], linha["descricao"])
        if not p:
            continue
        dispatches.append({
            "user_id": linha["user_id"],
            "user_nome": linha.get("user_nome"),
            "telefone": linha["telefone"],
            "item_id": linha["id"],
            "kind": "retorno",
            "message": p["texto"],
            "botoes": p["botoes"],
            "sugestao": linha["sugestao"],
            "descricao": linha["descricao"],
        })
    return dispatches


def run_proactive_engine(
    ref_date: Optional[date] = None,
    ref_datetime: Optional[datetime] = None,
) -> dict:
    """
    Executa o ciclo completo. Pode rodar a cada 5-15 minutos com segurança:
    o log de disparos garante que ninguém recebe mensagem repetida.
    Alarmes com hora furam o silêncio; o resto respeita 8h-21h.
    """
    now = ref_datetime or tempo.agora()
    roll_recurring(ref=ref_date)          # recorrentes rolam ANTES de tudo
    rodar_purga_se_for_o_dia(now)         # M1.6 (seco por padrao)
    alarms = check_time_alarms(ref=now)
    if _in_quiet_hours(now):
        # TODA CHECAGEM PRECISA DE VALOR NOS DOIS RAMOS (auditoria M4.2).
        #
        # `podcast_conv` nasceu so no `else`, e das 21h
        # as 8h o `return` la embaixo estourava UnboundLocalError —
        # derrubando o CICLO INTEIRO, inclusive o alarme de hora
        # marcada, que e justamente o unico que fura o silencio. Onze
        # horas por dia, todo dia, e o `_loop_proativo` so logava
        # "ciclo falhou" e dormia 60s: quem marcou "remedio 22h" nunca
        # ouviria o bot.
        #
        # Checagem nova entra NESTA tupla no mesmo commit em que nasce.
        due, churn, trial, guided, overdue, resumo, gastos, retorno = (
            [], [], [], [], [], [], [], [])
        podcast_conv, podcast_dia = [], []
    else:
        overdue = check_overdue(ref=ref_date) + check_winback()
        due = check_due_items(ref=ref_date)
        churn = check_churn(ref=ref_datetime)
        resumo = check_weekly_summary(ref=now)
        gastos = check_gastos_semanais(ref=now)
        retorno = check_retorno(ref=now)
        # MINI-PODCAST (M4.2). So o CONVITE sai daqui; o audio nunca e
        # proativo — ele vai como resposta ao toque no botao, dentro da
        # conversa que a pessoa acabou de abrir.
        podcast_conv = check_podcast(ref=now)
        podcast_dia = check_podcast_dia(ref=now)
        try:
            import trial_guiado
            guided = trial_guiado.run_trial_nudges()
        except Exception:
            guided = []
        trial = check_trial_ending()  # fallback: só quem NÃO recebeu o d6
    return {
        "executed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "alarm_dispatches": alarms,
        "resumo_dispatches": resumo,
        "gastos_dispatches": gastos,
        "overdue_dispatches": overdue,
        "due_dispatches": due,
        "churn_dispatches": churn,
        "trial_dispatches": trial,
        "guided_dispatches": guided,
        "retorno_dispatches": retorno,
        "podcast_dispatches": podcast_conv,
        "podcast_dia_dispatches": podcast_dia,
        "total": (len(alarms) + len(resumo) + len(overdue) + len(due)
                  + len(churn) + len(trial) + len(guided) + len(gastos)
                  + len(retorno) + len(podcast_conv) + len(podcast_dia)),
    }


def simulate_next_day() -> dict:
    """Simula a execução do cronjob no dia seguinte (D+1)."""
    tomorrow = tempo.hoje() + timedelta(days=1)
    tomorrow_dt = tempo.agora() + timedelta(days=1)
    return run_proactive_engine(ref_date=tomorrow, ref_datetime=tomorrow_dt)


_DIAS_PT = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta",
            "Sábado", "Domingo")


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t or "")
                   if unicodedata.category(c) != "Mn").lower()


def check_podcast(ref: Optional[datetime] = None) -> list[dict]:
    """Checagem: quem recebe o CONVITE do mini-podcast hoje.

    So o convite sai daqui — o audio nunca e proativo. A pessoa toca em
    "Quero ouvir" e o audio vai como resposta, dentro da conversa que ela
    acabou de abrir. Audio de 3 min chegando sozinho e a mensagem mais
    intrusiva que existe no WhatsApp, e este numero ja foi restringido duas
    vezes.

    Dois grupos entram:
      1. quem escolheu nicho na landing e passou das 6h do cadastro (uma vez);
      2. quem ja ouviu e faz 7 dias do ultimo convite ou episodio.

    O `kind` e "podcast" e ele esta em `KINDS_SEM_TEMPLATE`: fora da janela
    de 24h o convite nao sai, e esta certo. Nao ha template de podcast e nao
    vai haver — seria marketing, e marketing neste numero e o que a regua da
    Meta pune.
    """
    import podcast as _pod
    import voz as _voz

    agora = ref or tempo.agora()
    # CHAVE DE EMERGENCIA (M4.7, redefinida no M5.4).
    #
    # Ate 30/08/2026 isto era uma trava de aprovacao, DESLIGADA por default:
    # o Kevin ainda ia ouvir as amostras, e deploy nao e lancamento. Ele
    # ouviu, aprovou ("pode por no ar, padrao pra todos novos clientes"), e o
    # default virou LIGADO.
    #
    # O que a variavel e HOJE: o jeito de calar a feature inteira sem
    # deploy. Por isso ela e consultada nos TRES caminhos que geram audio ou
    # convite — aqui, em `check_podcast_dia`, e no `_mandar_podcast` do
    # `wa_bot`. Chave que desliga so um deles nao e chave de emergencia.
    if not PODCAST_ATIVO:
        return []

    # SEM VOZ CONFIGURADA, NAO CONVIDA. Perguntar "quer ouvir?" e nao ter
    # como gerar o audio e prometer o que nao da pra entregar — e a pessoa
    # toca no botao e nao recebe nada.
    if not _voz.disponivel():
        return []

    dispatches: list[dict] = []
    vistos: set = set()

    def _junta(u, primeiro: bool):
        if u["id"] in vistos:
            return
        if not db.user_can_receive(u):
            return
        # UMA VEZ POR DIA, o mesmo dedup que todo disparo desta base usa
        # (auditoria M4.3).
        #
        # `podcast_convite_em` cobria so a PRIMEIRA vez: no caminho semanal
        # nada segurava, e `podcast_ultimo` so muda quando a pessoa TOCA no
        # botao. Enquanto ela nao tocasse, o convite era regerado a cada
        # ciclo do cron — cinco, seis notas identicas em cinco minutos, ate
        # estourar o teto diario dela. E o teto e compartilhado com o aviso
        # de vencimento.
        #
        # `dispatched_today` e carimbado por quem ENVIA (`log_dispatch` no
        # laco do cron), entao convite que nao saiu volta amanha.
        # O DEDUP OLHA O KIND QUE VAI SER EMITIDO. Os dois momentos usam
        # kinds diferentes (`podcast-convite` na 1a vez, `podcast` no
        # semanal) desde o M4.7, e checar so um deles deixava o outro
        # repetir a cada ciclo — que e o P0 que ja custou duas rodadas.
        if db.dispatched_today("podcast-convite" if primeiro else "podcast",
                               u["id"]):
            return
        if not _pod.pode_enviar(u.get("podcast_ultimo"), agora=agora):
            return
        # O TETO OLHA O CONVITE TAMBEM (auditoria M4.5, P0). Quem recebeu e
        # nao tocou ficava com `podcast_ultimo` parado, e o convite renascia
        # todo dia — 14 numa quinzena. E cada um comia uma vaga do teto
        # diario, que e compartilhado com o alarme de hora marcada.
        if db.podcast_convite_recente(u["id"],
                                      dias=_pod.DIAS_ENTRE_EPISODIOS,
                                      ref=agora):
            return
        convite = _pod.convite(u.get("podcast_nicho"), nome=u.get("nome") or "")
        if not convite:
            return
        vistos.add(u["id"])
        dispatches.append({
            "user_id": u["id"],
            "user_nome": u["nome"],
            "telefone": u["telefone"],
            "item_id": None,
            # DOIS KINDS, porque sao dois momentos com regras diferentes.
            #
            # `podcast-convite` (1a vez) vive dentro da janela: a pessoa
            # acabou de se cadastrar e esta conversando. `podcast` (semanal)
            # tem template, porque o dia que ELA escolheu tem que valer
            # mesmo que ela nao fale com o bot ha uma semana.
            "kind": "podcast-convite" if primeiro else "podcast",
            "message": convite["texto"],
            "botoes": convite["botoes"],
            "nicho": convite["nicho"],
            "primeiro": primeiro,
            "quando": _fmt_br(agora.date().isoformat()),
        })

    for u in db.podcast_a_convidar(ref=agora,
                                   horas=_pod.HORAS_ATE_O_CONVITE):
        _junta(u, True)

    # O DIA QUE A PESSOA ESCOLHEU VALE (M4.7).
    #
    # Ele tinha sido removido porque, sem template, o lembrete so alcancava
    # quem por acaso tivesse falado com o bot nas ultimas 24h — entao "toda
    # segunda" era uma promessa que a gente quebrava na maioria das semanas.
    # Com `resolveai_podcast_pronto` aprovado, o lembrete sai no dia certo
    # independente de conversa recente, e a escolha dela passa a valer de
    # verdade. O Kevin: "tem que respeitar o que o cliente quiser, no dia
    # certo que ele selecionar".
    #
    # Quem ainda NAO escolheu dia entra em qualquer dia — ela ouviu uma vez
    # e nao disse quando quer; segurar por isso seria puni-la por nao ter
    # respondido.
    hoje_pt = _DIAS_PT[agora.weekday()]
    for u in db.podcast_assinantes():
        escolhido = (u.get("podcast_dia") or "").strip()
        if escolhido and _sem_acento(escolhido) != _sem_acento(hoje_pt):
            continue
        _junta(u, False)
    return dispatches

