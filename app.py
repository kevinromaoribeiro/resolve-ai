"""
app.py — RESOLVE AI · O Concierge Operacional de Vida (MVP)

Aplicação Streamlit com 4 módulos navegáveis pela sidebar:
1. Landing Page de Vendas (Aquisição)
2. Onboarding Intelligent Form (Perfil 360)
3. Simulador de WhatsApp & Motor de Ingestão AI
4. One-Page Dashboard Financeiro & Operacional

+ Botão de testes do Cronjob Proativo & Anti-Churn (scheduler.py).

Execução:
    pip install -r requirements.txt
    streamlit run app.py

Sem OPENAI_API_KEY/ANTHROPIC_API_KEY no .env, o sistema roda em
Modo Simulação Inteligente (Mock AI) — 100% funcional offline.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

import db
import ai_engine
import scheduler

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="RESOLVE AI · Life O.S.",
    page_icon="✅",
    layout="wide",
    initial_sidebar_state="expanded",
)

db.init_db()

EMERALD = "#00A86B"
SLATE = "#0F172A"

# ---------------------------------------------------------------------------
# CSS global + Logo
# ---------------------------------------------------------------------------

st.markdown(
    f"""
    <style>
    .stApp {{ background-color: {SLATE}; }}
    section[data-testid="stSidebar"] {{ background-color: #0B1120; }}

    .ra-card {{
        background: #1E293B;
        border: 1px solid #334155;
        border-radius: 16px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 1rem;
    }}
    .ra-kpi-label {{ color: #94A3B8; font-size: 0.8rem; text-transform: uppercase;
                     letter-spacing: 0.08em; }}
    .ra-kpi-value {{ color: {EMERALD}; font-size: 1.9rem; font-weight: 700; }}

    .wa-bubble-user {{
        background: #005C4B; color: #E9EDEF; border-radius: 12px 12px 2px 12px;
        padding: 0.55rem 0.8rem; margin: 0.25rem 0 0.25rem auto;
        max-width: 85%; width: fit-content; font-size: 0.9rem;
    }}
    .wa-bubble-bot {{
        background: #202C33; color: #E9EDEF; border-radius: 12px 12px 12px 2px;
        padding: 0.55rem 0.8rem; margin: 0.25rem auto 0.25rem 0;
        max-width: 85%; width: fit-content; font-size: 0.9rem;
    }}
    .wa-meta {{ color: #8696A0; font-size: 0.7rem; text-align: right; }}

    div.stButton > button[kind="primary"] {{
        background-color: {EMERALD}; color: white; border: none;
        border-radius: 10px; font-weight: 600;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


def render_logo(size: int = 46, with_name: bool = True) -> None:
    """Logo tipográfico: check fundido a um hexágono, Verde Esmeralda."""
    name_html = (
        f"<span style='font-family:Inter,-apple-system,sans-serif;"
        f"font-weight:800;font-size:{size * 0.55}px;color:#F8FAFC;"
        f"letter-spacing:-0.02em;'>RESOLVE"
        f"<span style='color:{EMERALD};'> AI</span></span>"
        if with_name else ""
    )
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:12px;margin:4px 0 14px 0;">
          <svg width="{size}" height="{size}" viewBox="0 0 100 100"
               xmlns="http://www.w3.org/2000/svg">
            <polygon points="50,4 90,27 90,73 50,96 10,73 10,27"
                     fill="none" stroke="{EMERALD}" stroke-width="7"
                     stroke-linejoin="round"/>
            <path d="M30 52 L45 67 L72 36" fill="none" stroke="{EMERALD}"
                  stroke-width="9" stroke-linecap="round"
                  stroke-linejoin="round"/>
          </svg>
          {name_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def wa_chat(messages: list[tuple[str, str]]) -> None:
    """Renderiza uma conversa estilo WhatsApp. messages = [(role, texto)]."""
    html = ["<div style='background:#0B141A;border-radius:14px;padding:14px;'>"]
    for role, text in messages:
        cls = "wa-bubble-user" if role == "user" else "wa-bubble-bot"
        html.append(f"<div class='{cls}'>{text}</div>")
    html.append("<div class='wa-meta'>WhatsApp · simulação</div></div>")
    st.markdown("".join(html), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar: navegação + motor proativo
# ---------------------------------------------------------------------------

with st.sidebar:
    render_logo(size=36)
    page = st.radio(
        "Navegação",
        ["🏠 Landing Page", "📝 Onboarding", "💬 Simulador WhatsApp",
         "📊 Dashboard"],
        label_visibility="collapsed",
    )
    st.divider()

    mode_label = ("🧠 IA real conectada" if ai_engine.LLM_AVAILABLE
                  else "🧪 Modo Simulação Inteligente (Mock AI)")
    st.caption(f"Motor de ingestão: **{mode_label}**")
    st.divider()

    st.markdown("**Testes de Background**")
    if st.button("⚡ Executar Motor de Disparo Proativo (Simular Dia Seguinte)",
                 use_container_width=True):
        st.session_state["scheduler_result"] = scheduler.simulate_next_day()

    if "scheduler_result" in st.session_state:
        result = st.session_state["scheduler_result"]
        st.caption(f"Executado em: {result['executed_at']} · "
                   f"{result['total']} disparo(s)")
        for d in result["due_dispatches"] + result["churn_dispatches"] + result.get("trial_dispatches", []) + result.get("guided_dispatches", []):
            icon = {"1-click-buy": "🛒", "vencimento": "⏰",
                    "anti-churn": "🔄", "trial-ending": "⏳",
                    "trial-guiado": "🌱"}.get(d["kind"], "📩")
            with st.expander(f"{icon} {d['kind']} → {d['user_nome']}"):
                st.markdown(d["message"])
        if result["total"] == 0:
            st.info("Nenhum disparo elegível. Cadastre itens com vencimento "
                    "próximo ou simule inatividade no Onboarding.")

# ---------------------------------------------------------------------------
# MÓDULO 1 — LANDING PAGE
# ---------------------------------------------------------------------------

if page == "🏠 Landing Page":
    render_logo(size=54)
    st.markdown(
        f"""
        <h1 style="color:#F8FAFC;font-size:2.4rem;line-height:1.15;
                   margin-bottom:0.4rem;">
          Pare de gerenciar sua vida na memória.<br>
          <span style="color:{EMERALD};">Mande no Zap, o Resolve AI executa.</span>
        </h1>
        <p style="color:#94A3B8;font-size:1.1rem;max-width:720px;">
          O primeiro mordomo artificial que lê seus áudios, audita suas contas,
          antecipa manutenções e repõe seus mantimentos com 1 clique.
        </p>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### Veja o mordomo em ação")
    c1, c2, c3 = st.columns(3)

    with c1:
        with st.expander("🚗 Demo Carro — foto da etiqueta de óleo", expanded=False):
            wa_chat([
                ("user", "📷 <i>[Foto da etiqueta de troca de óleo:<br>"
                         "Próxima troca 78.500 km ou 10/11]</i>"),
                ("bot", "Etiqueta lida, Kevin. Seu Onix está com ~74.200 km. "
                        "Pela sua média de 900 km/mês, a troca cai em "
                        "<b>meados de novembro</b>. Lembrete agendado para "
                        "03/11 com folga de segurança. ✅"),
            ])

    with c2:
        with st.expander("🐱 Demo Pet — áudio 'comprei areia'", expanded=False):
            wa_chat([
                ("user", "🎤 <i>\"Ééé... comprei areia pro gato hoje, "
                         "aquela de 4kg mesmo\"</i>"),
                ("bot", "Anotado! Areia 4kg dura ~18 dias no seu consumo. "
                        "Te aviso dia 22 com o link da reposição no melhor "
                        "preço — é só clicar. 🛒"),
                ("bot", "<i>(3 dias antes de acabar)</i><br>⏰ Areia do gato "
                        "acabando! Resolver em 1 clique:<br>"
                        "<u>petz.com.br/areia-4kg?ref=resolveai</u>"),
            ])

    with c3:
        with st.expander("💡 Demo Luz — print do boleto", expanded=False):
            wa_chat([
                ("user", "📷 <i>[Print de boleto Enel — R$ 187,40 — "
                         "vence 20/07]</i>"),
                ("bot", "Identifiquei a conta de luz: <b>R$ 187,40</b>, "
                        "vencimento <b>20/07</b>. Como procedo?<br><br>"
                        "1️⃣ Salvar como <b>Despesa Paga</b><br>"
                        "2️⃣ Agendar <b>Lembrete</b> para 19/07"),
            ])

    st.markdown("")
    cta_l, cta_r = st.columns([1, 2])
    with cta_l:
        if st.button("Começar agora por R$ 19,90/mês", type="primary",
                     use_container_width=True):
            st.session_state["goto_onboarding"] = True
            st.rerun()
    if st.session_state.pop("goto_onboarding", False):
        st.success("👉 Acesse **📝 Onboarding** na barra lateral para ativar "
                   "seu mordomo em 2 minutos.")

    st.divider()
    f1, f2, f3 = st.columns(3)
    f1.markdown("**🧠 Gestão proativa**\n\nVencimentos, manutenções e "
                "burocracias antecipadas antes de virarem problema.")
    f2.markdown("**🎯 Zero fricção**\n\nÁudio, foto ou texto. Sem app novo, "
                "sem formulário. É o seu WhatsApp de sempre.")
    f3.markdown("**🛒 Resolução em 1 clique**\n\nReposição de ração, filtro e "
                "óleo com link direto no melhor preço.")

# ---------------------------------------------------------------------------
# MÓDULO 2 — ONBOARDING
# ---------------------------------------------------------------------------

elif page == "📝 Onboarding":
    render_logo(size=40)
    st.markdown("## Comece em 30 segundos")
    st.caption("🎁 **7 dias grátis, sem cartão.** Só o essencial agora — "
               "o resto o Resolve AI aprende conversando com você.")

    nome = st.text_input("Como você quer ser chamado? *",
                         placeholder="Kevin")
    telefone = st.text_input("Seu WhatsApp *",
                             placeholder="+55 11 99999-0000")
    c1, c2 = st.columns(2)
    idade = c1.number_input("Idade", min_value=0, max_value=120, value=0,
                            help="Opcional")
    profissao = c2.text_input("Profissão", placeholder="Ex.: coordenador de e-commerce",
                              help="Opcional")

    st.markdown("**Para que você quer me usar?** *(toque em quantos quiser)*")
    USE_CASES = {
        "💡 Contas de casa": "contas",
        "🛒 Compras de mercado": "mercado",
        "🚗 Manutenções do carro": "carro",
        "🩺 Consultas e exames": "saude",
        "🎂 Aniversários e datas": "datas",
        "📦 Encomendas e prazos": "encomendas",
        "🐾 Cuidados com pet": "pet",
        "📄 Documentos e burocracias": "burocracia",
    }
    selected = st.pills("Casos de uso", list(USE_CASES.keys()),
                        selection_mode="multi",
                        label_visibility="collapsed") \
        if hasattr(st, "pills") else st.multiselect(
            "Casos de uso", list(USE_CASES.keys()),
            label_visibility="collapsed")

    with st.expander("➕ Quer me contar mais? Deixo tudo ainda mais "
                     "personalizado *(ou pule — sem problema)*"):
        carro_modelo = st.text_input("Se tiver carro: modelo e ano",
                                     placeholder="Onix 1.0 2022")
        carro_km = st.number_input("Km aproximada", min_value=0,
                                   max_value=500_000, step=500, value=0)
        pet_info = st.text_input("Se tiver pet: espécie, nome e ração",
                                 placeholder="Gato, Thor, ração Premier")
        dia_resumo = st.selectbox(
            "Melhor dia para o seu resumo semanal",
            ["Segunda-feira", "Terça-feira", "Quarta-feira",
             "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"],
        )

    if st.button("Começar meus 7 dias grátis 🚀", type="primary",
                 use_container_width=True):
        if not nome.strip() or not telefone.strip():
            st.error("Só preciso do seu nome e WhatsApp para começar.")
        else:
            interesses = ",".join(USE_CASES[s] for s in (selected or []))
            user_id = db.create_user(
                nome=nome.strip(),
                telefone=telefone.strip(),
                idade=int(idade) or None,
                profissao=profissao.strip() or None,
                interesses=interesses or None,
                carro_modelo=carro_modelo.strip() or None,
                carro_km=int(carro_km) or None,
                pet_info=pet_info.strip() or None,
                dia_resumo=dia_resumo,
            )
            st.session_state["active_user_id"] = user_id
            st.balloons()
            st.success(f"Pronto, **{nome.split()[0]}**! Seus 7 dias grátis "
                       f"começaram agora.")
            st.markdown("**Experimente mandar no WhatsApp (ou no Simulador):**")
            EXAMPLES = {
                "contas": "📷 *print do boleto de luz* → eu leio o valor e o "
                          "vencimento e te lembro um dia antes",
                "mercado": "🎤 *\"comprei arroz, óleo e café hoje\"* → eu "
                           "registro e te aviso quando estiver na hora de repor",
                "carro": "💬 *\"troquei o óleo hoje, 74.200 km\"* → eu calculo "
                         "e te aviso da próxima troca",
                "saude": "💬 *\"consulta com o cardiologista dia 15/08 às "
                         "14h\"* → lembrete um dia antes e no dia",
                "datas": "💬 *\"aniversário da minha mãe é 03/09\"* → nunca "
                         "mais passa em branco",
                "encomendas": "💬 *\"encomenda chega até sexta\"* → eu cobro "
                              "o prazo por você",
                "pet": "🎤 *\"comprei ração premier hoje\"* → aviso quando "
                       "estiver acabando, com link de reposição em 1 clique",
                "burocracia": "💬 *\"IPVA vence dia 15/01\"* → lembrete com "
                              "antecedência, sem multa",
            }
            chosen = [USE_CASES[s] for s in (selected or [])] or ["contas",
                                                                  "mercado"]
            for key in chosen[:4]:
                st.markdown(f"- {EXAMPLES[key]}")

    st.divider()
    with st.expander("🔧 Ferramentas de teste (dev only)"):
        users = db.list_users()
        if users:
            options = {f"#{u['id']} · {u['nome']}": u["id"] for u in users}
            sel = st.selectbox("Usuário", list(options.keys()))
            uid = options[sel]
            cA, cB = st.columns(2)
            if cA.button("Simular 11 dias de inatividade (gatilho anti-churn)"):
                db.set_last_interaction_days_ago(uid, 11)
                st.success("ultima_interacao retrocedida em 11 dias. "
                           "Rode o Motor Proativo na sidebar.")
            if cB.button("Popular dados de exemplo (5 itens)"):
                today = date.today()
                db.add_item(uid, "despesa", "Contas", "Conta de luz Enel",
                            187.40, status="concluido")
                db.add_item(uid, "despesa", "Alimentação",
                            "Mercado da semana", 342.80, status="concluido")
                db.add_item(uid, "despesa", "Lazer", "Assinatura streaming",
                            55.90, status="concluido")
                db.add_item(uid, "lembrete", "Pet", "Reposição ração Premier",
                            None, (today + timedelta(days=2)).isoformat(),
                            link_afiliado=ai_engine.affiliate_link_for("ração"))
                db.add_item(uid, "lembrete", "Veículo",
                            "Troca de óleo do carro", 280.00,
                            (today + timedelta(days=3)).isoformat(),
                            link_afiliado=ai_engine.affiliate_link_for("óleo"))
                st.success("5 itens de exemplo criados. Veja o 📊 Dashboard.")
        else:
            st.info("Nenhum usuário cadastrado ainda.")

# ---------------------------------------------------------------------------
# MÓDULO 3 — SIMULADOR WHATSAPP
# ---------------------------------------------------------------------------

elif page == "💬 Simulador WhatsApp":
    render_logo(size=40)
    st.markdown("## Simulador de WhatsApp · Motor de Ingestão AI")

    users = db.list_users()
    if not users:
        st.warning("Nenhum usuário cadastrado. Complete o **📝 Onboarding** "
                   "primeiro.")
        st.stop()

    options = {f"#{u['id']} · {u['nome']} ({u['telefone']})": u
               for u in users}
    sel = st.selectbox("Conversando como:", list(options.keys()))
    user = options[sel]
    first_name = user["nome"].split()[0]

    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("pending_decision", None)

    input_kind = st.radio(
        "Tipo de entrada (simula o formato recebido no WhatsApp):",
        ["💬 Texto", "🎤 Áudio (transcrição)", "📷 Imagem + instrução",
         "📷 Imagem silenciosa", "🎬 Vídeo"],
        horizontal=True,
    )

    KIND_MAP = {
        "💬 Texto": "texto",
        "🎤 Áudio (transcrição)": "audio",
        "📷 Imagem + instrução": "imagem_com_texto",
        "📷 Imagem silenciosa": "imagem_silenciosa",
        "🎬 Vídeo": "video",
    }
    kind = KIND_MAP[input_kind]

    instruction = ""
    if kind == "imagem_com_texto":
        instruction = st.text_input(
            "Instrução que acompanha a imagem:",
            placeholder="Ex.: 'já paguei, arquiva' ou 'agenda lembrete'",
        )
    if kind in ("imagem_com_texto", "imagem_silenciosa"):
        st.caption("💡 No MVP, digite abaixo o conteúdo que o OCR leria na "
                   "imagem. Ex.: *Boleto Enel R$ 187,40 vencimento 20/07*")
    if kind == "audio":
        st.caption("💡 Digite a transcrição informal, com cacoetes mesmo. "
                   "Ex.: *ééé então, comprei ração hoje, 89 reais*")

    # Histórico
    for msg in st.session_state["chat_history"]:
        with st.chat_message(msg["role"],
                             avatar="🧑" if msg["role"] == "user" else "✅"):
            st.markdown(msg["content"])

    placeholder = ("Responda 1 ou 2..."
                   if st.session_state["pending_decision"]
                   else "Digite a mensagem...")
    user_input = st.chat_input(placeholder)

    if user_input:
        st.session_state["chat_history"].append(
            {"role": "user", "content": f"*[{input_kind}]* {user_input}"}
        )

        if st.session_state["pending_decision"]:
            result = ai_engine.converse(
                user["id"], first_name, "decisao", user_input,
                pending=st.session_state["pending_decision"],
            )
        else:
            result = ai_engine.converse(
                user["id"], first_name, kind, user_input,
                instruction=instruction,
            )

        st.session_state["pending_decision"] = (
            result["pending_payload"] if result["needs_decision"] else None
        )

        badge = " `🧠 LLM`" if result["mode"] == "llm" else " `🧪 Mock`"
        st.session_state["chat_history"].append(
            {"role": "assistant", "content": result["reply"] + badge}
        )
        st.rerun()

    if st.button("🗑️ Limpar conversa"):
        st.session_state["chat_history"] = []
        st.session_state["pending_decision"] = None
        st.rerun()

# ---------------------------------------------------------------------------
# MÓDULO 4 — DASHBOARD
# ---------------------------------------------------------------------------

elif page == "📊 Dashboard":
    render_logo(size=40)
    st.markdown("## One-Page Dashboard Financeiro & Operacional")

    users = db.list_users()
    if not users:
        st.warning("Nenhum usuário cadastrado. Complete o **📝 Onboarding** "
                   "primeiro.")
        st.stop()

    options = {f"#{u['id']} · {u['nome']}": u for u in users}
    sel = st.selectbox("Usuário:", list(options.keys()))
    user = options[sel]
    uid = user["id"]

    # --- KPIs -------------------------------------------------------------
    total_mes = db.month_spend(uid)
    lembretes = db.active_reminders_count(uid)
    # Economia estimada: multa média de 2% + juros ~1% a.m. sobre contas
    # com lembrete ativo/concluído no prazo (proxy simplificado do MVP).
    contas_protegidas = [
        i for i in db.list_items(uid)
        if i["categoria"] == "Contas" and i["valor_reais"]
    ]
    economia = round(sum(i["valor_reais"] for i in contas_protegidas) * 0.03, 2)

    k1, k2, k3 = st.columns(3)
    for col, label, value in [
        (k1, "Total gasto no mês",
         f"R$ {total_mes:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")),
        (k2, "Lembretes ativos", str(lembretes)),
        (k3, "Economia estimada (multas evitadas)",
         f"R$ {economia:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")),
    ]:
        col.markdown(
            f"<div class='ra-card'><div class='ra-kpi-label'>{label}</div>"
            f"<div class='ra-kpi-value'>{value}</div></div>",
            unsafe_allow_html=True,
        )

    # --- Gráfico de rosca ---------------------------------------------------
    spend = db.spend_by_category(uid)
    col_chart, col_table = st.columns([1, 1.4])

    with col_chart:
        st.markdown("#### Despesas por categoria")
        if spend:
            fig = px.pie(
                names=list(spend.keys()),
                values=list(spend.values()),
                hole=0.55,
                color_discrete_sequence=["#00A86B", "#38BDF8", "#FBBF24",
                                         "#F472B6", "#A78BFA", "#94A3B8"],
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#E2E8F0",
                margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(orientation="h", y=-0.15),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem despesas registradas ainda. Use o Simulador de "
                    "WhatsApp para cadastrar.")

    # --- Tabela de auditoria -------------------------------------------------
    with col_table:
        st.markdown("#### Auditoria de itens")
        status_filter = st.selectbox(
            "Filtrar por status:",
            ["Todos", "pendente", "concluido", "aglutinado"],
        )
        items = db.list_items(
            uid, status=None if status_filter == "Todos" else status_filter
        )
        if items:
            df = pd.DataFrame(items)[
                ["id", "tipo", "categoria", "descricao", "valor_reais",
                 "data_vencimento", "status"]
            ].rename(columns={
                "valor_reais": "valor (R$)",
                "data_vencimento": "vencimento",
            })
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ Exportar .csv",
                df.to_csv(index=False).encode("utf-8"),
                file_name=f"resolve_ai_itens_user{uid}.csv",
                mime="text/csv",
            )
        else:
            st.info("Nenhum item para este filtro.")
