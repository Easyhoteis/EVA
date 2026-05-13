# 🤖 EVA - Backend em Python

Backend WhatsApp com IA para gerenciar atendimentos hoteleiros.

---

## 📋 O que você recebeu

- **backend.py** - API FastAPI com Twilio + Claude Haiku + PostgreSQL
- **painel.html** - Interface admin pra gerenciar conversas
- **requirements.txt** - Dependências Python
- **.env.example** - Template de variáveis de ambiente

---

## ⚙️ Setup Inicial (10 minutos)

### 1. Criar pasta e arquivos

```bash
mkdir eva-hotel
cd eva-hotel
```

Copie os arquivos:
- `backend.py`
- `painel.html`
- `requirements.txt`
- `.env.example` (e renomeie para `.env`)

### 2. Instalar dependências

```bash
pip install -r requirements.txt --break-system-packages
```

(Ou sem `--break-system-packages` se você tiver venv)

### 3. Configurar `.env`

Renomeie `.env.example` para `.env` e preenda com suas credenciais:

```
TWILIO_ACCOUNT_SID=sua_chave_aqui
TWILIO_AUTH_TOKEN=seu_token_aqui
TWILIO_PHONE_NUMBER=+5511XXXXXXXXX
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxx
DATABASE_URL=postgresql://user:pass@localhost:5432/eva_db
PORT=3000
```

### 4. Rodar

```bash
python backend.py
```

Deve aparecer:

```
============================================================
🤖 EVA Backend - Iniciando...
============================================================
✅ Banco de dados inicializado!
📍 URL: http://localhost:3000
📋 Painel: http://localhost:3000/painel
============================================================
```

---

## 🌐 Acessar

- **Painel:** http://localhost:3000/painel
- **API:** http://localhost:3000
- **Webhook Twilio:** http://localhost:3000/webhook/whatsapp

---

## 📱 Testar Webhook Localmente

Use `ngrok` pra expor seu localhost:

```bash
pip install ngrok
ngrok http 3000
```

Copie a URL e configure no Twilio:
- Twilio Console → Messaging → WhatsApp Sandbox
- "When a message comes in" → URL do ngrok + `/webhook/whatsapp`

---

## 🗄️ Banco de Dados

### Se usar PostgreSQL local

```bash
# Instalar PostgreSQL (Windows: https://www.postgresql.org/download/windows/)
# Criar banco:
psql -U postgres -c "CREATE DATABASE eva_db;"

# No .env:
DATABASE_URL=postgresql://postgres:sua_senha@localhost:5432/eva_db
```

### Se usar Railway (recomendado)

1. Vá em https://railway.app
2. Novo projeto → PostgreSQL
3. Copie a DATABASE_URL
4. Cole no `.env`

---

## 📊 Endpoints da API

**GET** `/api/conversas/abertas` - Conversas abertas
**GET** `/api/conversas/fechadas` - Conversas fechadas  
**GET** `/api/conversas/{id}/mensagens` - Mensagens de uma conversa
**POST** `/api/conversas/{id}/fechar` - Fecha atendimento
**GET** `/api/relatorios` - Estatísticas

---

## 🚀 Deploy no Railway

```bash
git init
git add .
git commit -m "EVA v1"
git remote add origin https://github.com/seu-user/eva-hotel
git push -u origin main
```

No Railway:
1. New Project → Deploy from GitHub
2. Selecione `eva-hotel`
3. Adicione variáveis de ambiente (copie de `.env`)
4. Deploy automático!

---

## 🐛 Troubleshooting

**Erro: "ModuleNotFoundError: No module named 'fastapi'"**
→ Execute: `pip install -r requirements.txt --break-system-packages`

**Erro: "postgres connection refused"**
→ PostgreSQL não está rodando. Instale ou use Railway.

**Webhook não chega no Twilio**
→ Verifique URL do ngrok no Twilio. Teste com curl.

---

## 📝 Próximas Fases

- Fase 2: Integração Aresta (automático)
- Fase 3: Omnibees (sincronizar tarifas)
- Fase 4: Painel em React (melhor UI)

---

**Dúvidas?** Me manda uma mensagem! 💪
