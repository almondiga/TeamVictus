# Imagen determinista para Render (o cualquier host Docker).
# Alternativa al runtime Python nativo: en Render elige "Docker" como entorno.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
