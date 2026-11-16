# Imagen base con Python.
FROM python:3.12-slim
# Carpeta de trabajo dentro del contenedor.
WORKDIR /app
# Primero las dependencias, después el código.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker.py .
# Muestra los print y logs al instante en docker compose logs.
ENV PYTHONUNBUFFERED=1
# Comando que inicia el contenedor.
CMD ["python", "worker.py"]
