# WA Affiliate Pro v6.0 — Backend Definitivo

Sistema de autopost e agendamento para WhatsApp com inteligência artificial, A/B testing, remarketing e proteção anti-ban.

---

## 📦 Instalação

```bash
# 1. Clone ou extraia os arquivos
cd wa_affiliate_pro

# 2. Crie ambiente virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate    # Windows

# 3. Instale dependências
pip install -r requirements.txt

# 4. Configure variáveis de ambiente
cp .env.example .env
nano .env

# 5. Inicie o servidor
python app.py
```

---

## 🏗️ Arquitetura

```
wa_affiliate_pro/
├── app.py                 # Flask app principal (rotas)
├── config.py              # Configurações Pydantic (.env)
├── database.py            # Pool SQLite + índices + WAL
├── security.py            # JWT, bcrypt, rate limiting, HMAC
├── circuit_breaker.py     # Circuit breaker customizado
├── whatsapp_service.py    # Envio WA com retry + rate limit
├── shopee_service.py      # API Shopee com cache + scoring
├── autopost_engine.py     # Motor inteligente (A/B, smart schedule, remarketing)
├── agendador.py           # Worker de agendamentos
├── requirements.txt       # Dependências
└── .env.example           # Template de configuração
```

---

## 🔐 Segurança

- **JWT** com blacklist de logout
- **bcrypt** com custo 12
- **Rate limiting** por IP + usuário
- **HMAC** para validação de webhooks
- **Validação Pydantic** em todas as rotas
- **Secrets** 100% via variáveis de ambiente

---

## 🧠 Inteligência

| Feature | Descrição |
|---------|-----------|
| **Smart Schedule** | Aprende o melhor horário de cada grupo baseado em CTR histórico |
| **A/B Testing** | Testa 2+ templates, escolhe vencedor automaticamente (min 30 envios, 15% dif) |
| **Remarketing** | Sequência de 3 mensagens para quem clicou mas não comprou (24h, 72h, 7d) |
| **Smart Scoring** | Prioriza produtos com maior comissão × desconto × popularidade |
| **Anti-Ban** | Cooldown 3s entre mensagens, limite 20/min, pausa automática em falhas |

---

## 🛣️ Rotas da API

### Autenticação
- `POST /api/auth/register` — Registro com validação Pydantic
- `POST /api/auth/login` — Login com JWT
- `POST /api/auth/logout` — Revoga token

### Dashboard
- `GET /api/dashboard` — Estatísticas completas + circuit breakers

### Grupos
- `GET /api/grupos` — Lista grupos
- `POST /api/grupos` — Cria grupo
- `DELETE /api/grupos/<id>` — Remove grupo (soft delete)
- `PATCH /api/grupos/<id>/toggle` — Ativa/desativa autopost
- `POST /api/grupos/sincronizar` — Sincroniza do WhatsApp

### Templates
- `GET /api/templates` — Lista templates
- `POST /api/templates` — Cria template (com A/B group)
- `PATCH /api/templates/<id>/select` — Seleciona ativo

### Configuração
- `GET /api/config` — Obtém config
- `PUT /api/config` — Atualiza config

### Autopost
- `PATCH /api/autopost/toggle` — Liga/desliga
- `GET /api/autopost/stats` — Estatísticas
- `POST /api/autopost/reset-errors` — Reseta erros

### Agendamento
- `GET /api/agendamentos` — Lista
- `POST /api/agendamentos` — Cria
- `DELETE /api/agendamentos/<id>` — Remove

### Envio Manual
- `POST /api/enviar-mensagem` — Envia mensagem manual

### Produtos
- `POST /api/buscar-produto` — Busca por link

### Analytics
- `GET /api/analytics` — Analytics completo (posts, CTR, templates, horários)

### Cliques
- `GET /r/<short_code>` — Redireciona e registra clique
- `GET /api/clicks/stats` — Estatísticas de cliques

### Plano
- `GET /api/plano/limites` — Limites do plano atual
- `GET /api/plano/disponiveis` — Planos disponíveis

### Sistema
- `GET /api/health` — Health check profundo (DB + Bridge + Circuit Breakers)
- `GET /api/version` — Versão do sistema

---

## 📊 Banco de Dados

### Tabelas Principais
- `users` — Usuários
- `configs` — Configurações por usuário
- `grupos` — Grupos WhatsApp (com métricas CTR)
- `templates` — Templates de mensagem (com A/B test)
- `posts` — Posts enviados
- `clicks` — Cliques rastreados
- `remarketing` — Sequências de remarketing
- `agendamentos` — Agendamentos pendentes
- `autopost_control` — Controle do autopost
- `produtos_enviados` — Evita duplicados
- `horario_metrics` — Dados para Smart Schedule

### Índices Otimizados
- `idx_posts_user_date` — Posts por usuário/data
- `idx_clicks_short` — Busca de cliques por short_code
- `idx_grupos_user` — Grupos ativos do usuário
- `idx_horario_metrics` — Smart Schedule

---

## 🚀 Performance

- **Connection Pool** SQLite com WAL mode
- **Cache TTL** por usuário (3 minutos)
- **Session Reuse** requests (keep-alive)
- **Batch Processing** agendamentos (50 por ciclo)
- **Índices** em todas as queries frequentes

---

## 🛡️ Robustez

- **Circuit Breaker** para Bridge e Shopee API
- **Retry** com backoff exponencial (3 tentativas)
- **Graceful Shutdown** (SIGTERM/SIGINT)
- **Isolamento** de erros por usuário
- **Pausa automática** após 3 erros consecutivos

---

## 📈 Métricas

O sistema expõe estatísticas de circuit breakers no dashboard:
```json
{
  "circuit_breakers": {
    "wa_bridge": {"state": "closed", "fail_count": 0},
    "shopee_api": {"state": "closed", "fail_count": 0}
  }
}
```

---

## 📝 Changelog v6.0

- ✅ JWT dinâmico + blacklist
- ✅ Rate limiting por IP/user
- ✅ Circuit breaker (bridge + Shopee)
- ✅ Retry com backoff exponencial
- ✅ Cache TTL por usuário
- ✅ Connection pool SQLite + WAL
- ✅ Índices SQL otimizados
- ✅ Isolamento de erros por usuário
- ✅ Pausa por erros (backoff exp.)
- ✅ Limite de posts por dia
- ✅ A/B Testing real com CTR
- ✅ Smart Schedule (aprende horário)
- ✅ Remarketing automático
- ✅ Scoring inteligente de produtos
- ✅ Filtro por nicho em memória
- ✅ Controle de duplicados
- ✅ Validação Pydantic em todas as rotas
- ✅ Health check profundo
- ✅ Graceful shutdown
- ✅ Logging estruturado JSON
- ✅ Soft delete de grupos

---

**WA Affiliate Pro v6.0 — Production Ready**
# Sat Aug 22 17:44:37 -03 2026
