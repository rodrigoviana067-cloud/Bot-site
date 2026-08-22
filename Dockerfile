FROM node:20

# Instalar Python e dependências
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv python3-full \
    gcc g++ libpq-dev pkg-config curl git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar e instalar Node.js dependencies
COPY package.json ./
RUN npm install --production

# Copiar requirements.txt
COPY requirements.txt ./

# Criar virtual environment e instalar Python dependencies
RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir -r requirements.txt

# Copiar todo o código
COPY . .

RUN mkdir -p logs

EXPOSE 8080

CMD ["bash", "start.sh"]
