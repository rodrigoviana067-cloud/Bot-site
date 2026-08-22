FROM node:20-slim

# Evitar prompts interativos
ENV DEBIAN_FRONTEND=noninteractive

# Instalar Python e dependências do sistema
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    gcc \
    g++ \
    libpq-dev \
    pkg-config \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Criar symlink para python3 como python
RUN ln -sf /usr/bin/python3 /usr/bin/python

# Diretório de trabalho
WORKDIR /app

# Copiar arquivos de dependências primeiro (para cache do Docker)
COPY package.json package-lock.json* ./
COPY requirements.txt ./

# Instalar dependências Node.js
RUN npm install

# Instalar dependências Python
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt || \
    pip3 install --no-cache-dir -r requirements.txt

# Copiar todo o código
COPY . .

# Criar diretórios necessários
RUN mkdir -p logs

# Porta exposta
EXPOSE 8080

# Comando de inicialização
CMD ["bash", "start.sh"]
