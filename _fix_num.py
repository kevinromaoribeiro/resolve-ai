# -*- coding: utf-8 -*-
"""Reescreve a conferencia de numero. Descartavel."""
import io

NL = chr(10)
p = "podcast.py"
s = io.open(p, encoding="utf-8").read()

# tira os bytes de controle que o heredoc deixou no fonte
for ruim in (chr(8), chr(1)):
    s = s.replace(ruim, "")

ini = s.index("    # NUMERO QUE NAO ESTAVA NA MATERIA-PRIMA")
fim = s.index("def conferir_locucao", ini)
# recorta ate o fim do bloco de helpers que veio depois do return
fim = s.index("BOTOES = [", ini) if "BOTOES = [" in s[ini:] else fim

bloco = '''    # NUMERO QUE NAO ESTAVA NA MATERIA-PRIMA (auditoria M4.2, P1-5;
    # recalibrado na M4.3).
    #
    # O teste de fonte pega o modelo citando a Folha; nao pega ele afirmando
    # "venceu por 7 a 0" sem citar ninguem — que e a alucinacao que importa,
    # porque a pessoa nao tem como desconfiar.
    #
    # A PRIMEIRA VERSAO ERA CEGA E REPROVAVA O LEGITIMO. Ela exigia o numero
    # IGUAL na fonte, e reprovou 5 de 12 reescritas normais: "3 noticias",
    # "R$ 1,2 milhao" (fonte: 1.200.000), "mais de 60%" (fonte: 62%),
    # "temporada 2026". Pior: reprovava o PROPRIO roteiro deterministico
    # desta casa, que numera os blocos "1." "2." "3.". Uma conferencia que
    # reprova tudo nao protege nada — ela so desliga a locucao, e sobra o
    # roteiro cru de quarenta segundos.
    #
    # Agora ela permite o que a fala legitimamente faz e barra o que e
    # invencao:
    #   - o numero que esta na fonte (com escala: "1,2 milhao" == 1.200.000)
    #   - ARREDONDAMENTO de ate 10% — locucao arredonda, e o cabecalho deste
    #     modulo pede isso explicitamente
    #   - 1 ate BLOCOS: e a numeracao dos blocos, que o proprio formato cria
    #   - o ano corrente e o proximo: "temporada 2026" nao e alucinacao
    if materia is not None:
        fonte = _valores(materia)
        ano = (hoje or tempo.hoje()).year
        for valor, cru in _valores(texto, com_texto=True):
            if _autorizado(valor, fonte, ano):
                continue
            return "numero %r nao veio das fontes" % cru
    return None


# Escala falada. "1,2 milhao" e "1.200.000" sao o mesmo numero, e a locucao
# escolhe a primeira forma — recusar por isso seria a conferencia brigando
# com a reescrita que ela existe pra permitir.
_ESCALAS = (
    (r"bilh[õo]es|bilh[ãa]o", 1_000_000_000),
    (r"milh[õo]es|milh[ãa]o", 1_000_000),
    (r"\\bmil\\b", 1_000),
)

_NUM_BRUTO_RE = re.compile(r"\\d[\\d.,]*")


def _um_valor(cru: str) -> Optional[float]:
    """"1.200.000,50" -> 1200000.5 ; "1,2" -> 1.2 ; "05" -> 5."""
    t = cru.rstrip(".,")
    if not t:
        return None
    try:
        if "," in t:                       # virgula e o decimal no Brasil
            return float(t.replace(".", "").replace(",", "."))
        partes = t.split(".")
        if len(partes) > 1 and all(len(x) == 3 for x in partes[1:]):
            return float("".join(partes))  # 1.200.000 = milhar
        return float(t.replace(".", "")) if len(partes) > 1 else float(t)
    except ValueError:
        return None


def _valores(texto: str, com_texto: bool = False):
    """Todos os numeros do texto, ja com a escala falada aplicada."""
    saida = []
    for m in _NUM_BRUTO_RE.finditer(texto or ""):
        valor = _um_valor(m.group(0))
        if valor is None:
            continue
        depois = (texto[m.end():m.end() + 24] or "").lower()
        for padrao, mult in _ESCALAS:
            if re.match(r"\\s*(?:de\\s+)?(?:%s)" % padrao, depois):
                valor *= mult
                break
        saida.append((valor, m.group(0)) if com_texto else valor)
    return saida


def _autorizado(valor: float, fonte: list, ano: int) -> bool:
    if valor <= BLOCOS and float(valor).is_integer():
        return True                       # numeracao de bloco / "primeiro"
    if valor in (ano, ano + 1):
        return True
    for f in fonte:
        if f == valor:
            return True
        # 10% de folga: "mais de 60%" quando a fonte diz 62% e locucao, nao
        # invencao.
        if max(abs(f), abs(valor)) and \\
                abs(f - valor) <= 0.1 * max(abs(f), abs(valor)):
            return True
    return False


'''

s = s[:ini] + bloco + s[fim:]

# a conferencia precisa saber que dia e hoje pro ano corrente
s = s.replace(
    "def conferir_locucao(texto: Optional[str], nicho: Optional[str],"
    + NL + "                     materia: Optional[str] = None) -> Optional[str]:",
    "def conferir_locucao(texto: Optional[str], nicho: Optional[str],"
    + NL + "                     materia: Optional[str] = None,"
    + NL + "                     hoje: Optional[date] = None) -> Optional[str]:", 1)

io.open(p, "w", encoding="utf-8", newline=NL).write(s)
print("conferencia reescrita")
