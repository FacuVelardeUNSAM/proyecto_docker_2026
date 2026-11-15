FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api.py index.html ./
# ECharts se sirve localmente: los gráficos funcionan sin una CDN.
COPY echarts.min.js ECHARTS-LICENSE.txt ECHARTS-NOTICE.txt ./
ENV PYTHONUNBUFFERED=1
# Documenta el puerto interno; Compose lo publica en el host.
EXPOSE 5000
CMD ["python", "api.py"]
