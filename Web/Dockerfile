FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# ECharts se sirve localmente: los gráficos funcionan sin Internet.
COPY api.py index.html echarts.min.js ./
ENV PYTHONUNBUFFERED=1
# Documenta el puerto interno; Compose lo publica en el host.
EXPOSE 5000
CMD ["python", "api.py"]
