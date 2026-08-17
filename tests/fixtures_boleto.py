# -*- coding: utf-8 -*-
"""OCR como ele volta de BOLETO DE VERDADE, não como é cômodo escrever.

A auditoria do M2.1 reprovou com 7 P0 e a raiz de quase todos era o mesmo
erro de método: eu validei contra texto de uma linha, minúsculo, com um
valor e uma data. Boleto real tem rótulo capitalizado, QUATRO datas
(documento, processamento, vencimento, parcela anterior), CINCO valores
(desconto, juros, tarifa, documento, cobrado) e "Recibo do Pagador"
impresso no canhoto — a palavra que fazia o boleto virar comprovante pago.

Estes textos imitam a saída do `_read_image` sobre esses documentos.
"""

# --- boletos (têm que virar item PENDENTE) --------------------------------

ENEL = """Boleto bancário Ficha de Compensação. Beneficiário: ENEL DISTRIBUICAO SAO PAULO
Recibo do Pagador. Nosso Número 12345678901
Data do documento 05/08/2026   Data de processamento 06/08/2026
Vencimento 20/08/2026
(-) Desconto / Abatimento 0,50
(+) Juros / Multa 0,00
(=) Valor do Documento R$ 187,45
Linha digitável 03399.63290 64000.000006 00125.201020 4 12345678901234
DADOS: valor=187,45; vencimento=20/08/2026; beneficiario=Enel Distribuicao Sao Paulo; tipo=boleto"""

CONDOMINIO = """BOLETO CONDOMINIO EDIFICIO RESIDENCIAL SAO JOSE
BENEFICIARIO: CONDOMINIO EDIFICIO RESIDENCIAL SAO JOSE
RECIBO DO SACADO
DATA DO DOCUMENTO 01/09/2026  VENCIMENTO 10/09/2026
VALOR DO DOCUMENTO R$ 450,00
PARCELA ANTERIOR PAGO EM 10/08/2026"""

SABESP_SEM_CAUDA = """Conta de água SABESP. Beneficiario: Companhia de Saneamento Basico
Consumo 12 m3  Tarifa 7,44
Data de emissão 02/08/2026
Vence em 25/08/2026
Total a pagar R$ 92,10"""

CARTAO_MAIUSCULO = """FATURA CARTAO DE CREDITO NUBANK
BENEFICIARIO: NU PAGAMENTOS S.A.
VENCIMENTO: 15/09/2026
PAGAMENTO MINIMO R$ 89,00
VALOR COBRADO R$ 1.234,56"""

BOLETO_ESPACO_NO_VALOR = """Boleto Vivo Fibra. Beneficiário: TELEFONICA BRASIL S.A.
Vencimento 28/08/2026
Valor do Documento R$ 119, 90"""

BOLETO_DATA_POR_EXTENSO = """Boleto da academia. Beneficiário: Smart Fit
Vence em 20 de setembro de 2026. Valor R$ 129,90"""

# --- comprovantes (têm que virar despesa CONCLUÍDA) -----------------------

COMPROVANTE_PIX = """Comprovante de Pix enviado. Valor R$ 250,00
Pago em 14/08/2026 para SUPERMERCADO XYZ LTDA
Recibo de pagamento"""

COMPROVANTE_BANCO = """COMPROVANTE DE PAGAMENTO DE TITULO
PAGAMENTO EFETUADO EM 12/08/2026
VALOR PAGO R$ 187,45
BENEFICIARIO: ENEL DISTRIBUICAO SAO PAULO"""

# --- NÃO são conta (não podem virar item nenhum) --------------------------

CARDAPIO = """Cardápio da pizzaria. Pizza grande 45,00, refrigerante 8,00,
sobremesa 12,00. Entrega em 30 minutos."""

PRINT_CONVERSA = """Print de conversa do WhatsApp. Amigo escreveu:
te devo 50,00 do rango de ontem, te pago sexta"""

PIX_RECEBIDO = """Comprovante de Pix RECEBIDO. Você recebeu R$ 300,00
de MARIA SILVA em 15/08/2026. Crédito em conta."""

EXTRATO = """Extrato bancário. Saldo disponível R$ 1.240,33
Últimos lançamentos: mercado 89,90, farmácia 45,00"""

ETIQUETA = """Etiqueta de prateleira de supermercado: Arroz tipo 1, 5kg,
R$ 24,90 a unidade. Promoção válida até domingo."""

IFOOD = """Print do iFood. Pedido entregue. Total R$ 62,40
Restaurante Sabor Caseiro. Pedido nº 8842."""

ESTACIONAMENTO = """Recibo de estacionamento. Valor R$ 12,00.
Entrada 14:20 saída 16:45. Shopping Center Norte."""

FOTO_QUALQUER = "Foto de um cachorro caramelo deitado no sofá da sala"

NAO_SAO_CONTA = {
    "cardapio": CARDAPIO,
    "print_conversa": PRINT_CONVERSA,
    "pix_recebido": PIX_RECEBIDO,
    "extrato": EXTRATO,
    "etiqueta": ETIQUETA,
    "ifood": IFOOD,
    "estacionamento": ESTACIONAMENTO,
    "foto_qualquer": FOTO_QUALQUER,
}
