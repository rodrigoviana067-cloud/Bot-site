FROM node:20-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV NODE_ENV=production

# Instalar Python e dependências
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    gcc g++ libpq-dev pkg-config curl git \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/bin/python

WORKDIR /app

# Copiar e instalar Node.js dependencies PRIMEIRO
COPY package.json ./
RUN npm install --production

# Copiar e instalar Python dependencies
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# Copiar todo o código
COPY . .

RUN mkdir -p logs

EXPOSE 8080

CMD ["bash", "start.sh"]
