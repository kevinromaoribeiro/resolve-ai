"""Gera o templates/SUBMISSAO.md a partir do catálogo.

Escrever o markdown à mão faria o documento divergir do código no primeiro
ajuste de texto — e aí o que está no Business Manager deixa de ser o que o
bot manda. Fonte única: `templates/__init__.py`.

Rodar:  .venv\\Scripts\\python.exe templates/gerar_submissao.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import templates  # noqa: E402

CABECALHO = """# Templates para submeter no Business Manager

GERADO POR `templates/gerar_submissao.py` — não edite à mão. Mude o catálogo
em `templates/__init__.py` e rode o script de novo.

Para cada template abaixo, no Business Manager:
WhatsApp Manager > Modelos de mensagem > Criar modelo

- **Categoria:** Utilidade (UTILITY)
- **Idioma:** Português (BR) — `pt_BR`
- **Cabeçalho:** nenhum
- **Rodapé:** nenhum
- **Botões:** nenhum nesta primeira leva (ver DECISOES.md)

ARMADILHA DA INTERFACE, e ela custou meia hora: no passo "Configurar seu
modelo", clicar na aba **Utilidade** NÃO fixa a categoria. É preciso clicar
na aba **e depois marcar o rádio "Padrão"** logo abaixo dela. Se pular o
rádio, o passo seguinte aparece com "Marketing · Padrão" no cabeçalho e o
template inteiro vai para a categoria errada — e categoria errada na Meta é
preço errado e regra de opt-out errada.

**Sempre confira o cabeçalho da tela de edição antes de preencher.** Ele diz
a categoria em letra pequena embaixo do nome.

Depois que a Meta aprovar, configure no EasyPanel:

```
TEMPLATES_APROVADOS=<nomes aprovados, separados por vírgula>
```

Enquanto um template não estiver nessa lista, o bot NÃO envia nada por ele
fora da janela de 24h — a mensagem fica registrada como não entregue, com o
motivo, em vez de sumir.

---
"""


def gerar_conteudo() -> str:
    """O markdown como string. Separado do `main` porque o teste que confere
    divergência não pode ESCREVER no repo pra comparar (auditoria M2.0,
    P2-12): ele falhava na primeira rodada e passava na segunda, com o
    arquivo já alterado no working tree."""
    partes = [CABECALHO]
    for nome, t in templates.CATALOGO.items():
        exemplo = "\n".join(f"  - `{{{{{i}}}}}` -> `{v}`"
                            for i, v in enumerate(t.exemplo, 1))
        partes.append(f"""## `{t.nome}`

- **Nome:** `{t.nome}`
- **Categoria:** {t.categoria}
- **Idioma:** {t.idioma}

**Corpo:**

```
{t.corpo}
```

**Variáveis (exemplo para a submissão):**

{exemplo or '  - (nenhuma)'}

**Justificativa (cole no campo de descrição, se pedido):**

> {t.justificativa}

---
""")
    return "\n".join(partes)


def caminho_md() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "SUBMISSAO.md")


def main() -> None:
    destino = caminho_md()
    with open(destino, "w", encoding="utf-8") as f:
        f.write(gerar_conteudo())
    print(f"gerado: {destino} ({len(templates.CATALOGO)} templates)")


if __name__ == "__main__":
    main()
