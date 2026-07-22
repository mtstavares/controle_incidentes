# Usa uma imagem leve do Python baseada em Debian
FROM python:3.12-slim

ENV TZ=America/Sao_Paulo

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Instala dependências do sistema necessárias para o SQLite e compilação
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libreoffice \
    poppler-utils \
    python3-dev \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copia apenas o arquivo de requisitos primeiro (otimiza o cache do Docker)
COPY requirements.txt .

# Instala as dependências do Python
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install gunicorn

# Copia todo o conteúdo do projeto para dentro do container
COPY . .

# Cria a pasta instance para o banco de dados, se não existir
RUN mkdir -p instance

# Expõe a porta interna que o Gunicorn usará
EXPOSE 8000

# Comando para iniciar a aplicação usando Gunicorn
# "run:app" refere-se ao arquivo run.py e à variável app dentro dele
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "run:app"]
