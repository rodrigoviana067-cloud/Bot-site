# 🚀 Guia de Instalação — WA Affiliate Pro v6.0

## Passo 1: Extrair os Arquivos

```bash
# Extraia o ZIP em qualquer pasta
unzip wa_affiliate_pro_v6.zip -d wa_affiliate_pro
cd wa_affiliate_pro
```

## Passo 2: Criar Ambiente Virtual

```bash
# Linux/Mac
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

## Passo 3: Instalar Dependências

```bash
pip install -r requirements.txt
```

## Passo 4: Configurar Variáveis de Ambiente

```bash
# Copie o template
cp .env.example .env

# Edite com seus dados
nano .env
```

**Mínimo necessário:**
```bash
JWT_SECRET=$(openssl rand -hex 32)  # Gere um novo!
WA_BRIDGE_URL=http://127.0.0.1:3000
```

## Passo 5: Iniciar o Servidor

```bash
python app.py
```

**Saída esperada:**
```
============================================================
🚀 WA Affiliate Pro v6.0 — Iniciando
📊 Banco: /caminho/affiliate.db
🔐 JWT: a3f8b2c1...
============================================================
✅ Auto-poster v6.0 iniciado!
✅ Agendador iniciado!
 * Running on http://0.0.0.0:5001
```

## Passo 6: Testar

```bash
# Health check
curl http://localhost:5001/api/health

# Registro
curl -X POST http://localhost:5001/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"nome":"Teste","email":"teste@email.com","senha":"123456"}'

# Login
curl -X POST http://localhost:5001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"teste@email.com","senha":"123456"}'
```

## 📁 Estrutura Final

```
wa_affiliate_pro/
├── app.py              ← Inicie por aqui
├── config.py           ← Configurações
├── database.py         ← Banco de dados
├── security.py         ← Autenticação
├── circuit_breaker.py  ← Proteção
├── whatsapp_service.py ← Envio WA
├── shopee_service.py   ← API Shopee
├── autopost_engine.py  ← Motor inteligente
├── agendador.py        ← Agendamentos
├── requirements.txt    ← Dependências
├── .env.example        ← Template config
├── .env                ← SUAS configurações
└── README.md           ← Documentação
```

## 🔧 Configurações Importantes

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `JWT_SECRET` | Chave secreta JWT | **OBRIGATÓRIO** |
| `WA_BRIDGE_URL` | URL do bridge WhatsApp | `http://127.0.0.1:3000` |
| `SHOPEE_CACHE_TTL_SECONDS` | Cache produtos (seg) | `180` (3min) |
| `AUTOPOST_MAX_POSTS_PER_DAY` | Máx posts/dia | `50` |
| `WA_COOLDOWN_BETWEEN_MESSAGES` | Delay entre envios | `3.0` seg |

## 🐛 Troubleshooting

**Erro: `sqlite3.ProgrammingError`**
→ Já corrigido no v6.0 (check_same_thread=False + pool)

**Erro: `ModuleNotFoundError: No module named 'pydantic_settings'`**
→ `pip install pydantic-settings`

**Bridge não conecta**
→ Verifique se o bridge está rodando em `http://127.0.0.1:3000`

**Nenhum produto encontrado**
→ Verifique `shopee_app_id` e `shopee_api_key` na configuração

---
**Pronto para usar! 🎉**
