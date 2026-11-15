# Imagen base con Python; no necesitamos compiladores adicionales.
FROM python:3.12-slim
WORKDIR /app
# Instalar dependencias antes del código permite reutilizar la caché.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker.py .
# Envía los logs inmediatamente a docker compose logs.
ENV PYTHONUNBUFFERED=1
# El worker realiza consultas salientes; no necesita publicar puertos.
CMD ["python", "worker.py"]
