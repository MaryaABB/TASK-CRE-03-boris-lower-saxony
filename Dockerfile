FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código, pruebas y SQL
COPY src/ ./src/
COPY tests/ ./tests/
COPY sql/ ./sql/

# Comando de inicio por defecto
CMD ["python", "-m", "src.ingest_boris"]
