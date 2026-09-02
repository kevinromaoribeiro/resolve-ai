FROM python:3.12-slim

# ffmpeg cola os trechos de fala num OGG/Opus so — o unico formato em que o
# WhatsApp mostra os botoes de 1x / 1,5x / 2x, que foi o pedido do cliente.
# Sao ~100 MB de imagem em troca disso. Se este RUN sair, o `voz.py` volta
# pro MP3 sozinho: o episodio nunca deixa de sair por causa do formato.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "wa_bot:app", "--host", "0.0.0.0", "--port", "8000"]
