from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
import hashlib
import secrets
from datetime import datetime, date
from dotenv import load_dotenv
import logging
import sqlite3
import json
import asyncio
import csv
import io
import random
import requests
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY")
PORT = int(os.getenv("PORT", 3000))

# Configs Z-API podem vir do env OU do banco (admin cadastra)
ZAPI_INSTANCE_ATEND = os.getenv("ZAPI_INSTANCE", "")
ZAPI_TOKEN_ATEND = os.getenv("ZAPI_TOKEN", "")
ZAPI_CLIENT_TOKEN_ATEND = os.getenv("ZAPI_CLIENT_TOKEN", "")

UPLOAD_DIR = "/tmp/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="EVA Hotel Backend", version="5.0.0")
claude_client = Anthropic(api_key=CLAUDE_KEY)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

DB_PATH = "/tmp/eva.db"

def get_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"BD erro: {e}")
        return None

def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()

def gerar_token() -> str:
    return secrets.token_urlsafe(32)

def init_db():
    conn = get_db()
    if not conn: return
    cur = conn.cursor()
    
    try:
        # USUARIOS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                nome TEXT NOT NULL,
                perfil TEXT NOT NULL,
                ativo INTEGER DEFAULT 1,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ultimo_login TIMESTAMP
            )
        """)
        
        # SESSÕES
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessoes (
                token TEXT PRIMARY KEY,
                usuario_id INTEGER,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            )
        """)
        
        # LOGS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER,
                acao TEXT NOT NULL,
                detalhes TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # CONVERSAS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_cliente TEXT NOT NULL,
                nome_cliente TEXT,
                status TEXT DEFAULT 'aberto',
                fechado_por_id INTEGER,
                fechado_por_nome TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fechado_em TIMESTAMP,
                observacoes TEXT
            )
        """)
        
        # MENSAGENS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mensagens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversa_id INTEGER NOT NULL,
                remetente TEXT NOT NULL,
                conteudo TEXT NOT NULL,
                usuario_nome TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # CONTATOS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS contatos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                numero TEXT UNIQUE NOT NULL,
                email TEXT,
                tags TEXT,
                observacoes TEXT,
                conhecimento_ia TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # TEMPLATES
        cur.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                conteudo TEXT NOT NULL,
                categoria TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # CAMPANHAS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS campanhas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                template_id INTEGER,
                mensagem TEXT NOT NULL,
                imagem_url TEXT,
                total_contatos INTEGER DEFAULT 0,
                enviadas INTEGER DEFAULT 0,
                falhadas INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pendente',
                criado_por_id INTEGER,
                criado_por_nome TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                iniciado_em TIMESTAMP,
                finalizado_em TIMESTAMP
            )
        """)
        
        # ENVIOS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS envios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campanha_id INTEGER,
                contato_id INTEGER,
                numero TEXT,
                nome TEXT,
                status TEXT DEFAULT 'pendente',
                resposta TEXT,
                enviado_em TIMESTAMP
            )
        """)
        
        # ANTI-BAN
        cur.execute("""
            CREATE TABLE IF NOT EXISTS config_antiban (
                id INTEGER PRIMARY KEY DEFAULT 1,
                limite_diario INTEGER DEFAULT 100,
                limite_por_hora INTEGER DEFAULT 30,
                delay_min INTEGER DEFAULT 3,
                delay_max INTEGER DEFAULT 7,
                pausa_a_cada INTEGER DEFAULT 30,
                pausa_segundos INTEGER DEFAULT 60,
                horario_inicio TEXT DEFAULT '08:00',
                horario_fim TEXT DEFAULT '20:00',
                ativo INTEGER DEFAULT 1
            )
        """)
        
        # ENVIOS DIÁRIOS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS envios_diarios (
                data TEXT PRIMARY KEY,
                total INTEGER DEFAULT 0
            )
        """)
        
        # CONFIG Z-API (2 contas)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS config_zapi (
                tipo TEXT PRIMARY KEY,
                instance_id TEXT,
                token TEXT,
                client_token TEXT
            )
        """)
        
        # CONFIG SISTEMA (pausar robô, etc)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS config_sistema (
                chave TEXT PRIMARY KEY,
                valor TEXT
            )
        """)
        
        conn.commit()
        
        # Cria admin padrão
        cur.execute("SELECT COUNT(*) FROM usuarios")
        if cur.fetchone()[0] == 0:
            cur.execute(
                "INSERT INTO usuarios (email, senha_hash, nome, perfil) VALUES (?, ?, ?, ?)",
                ("admin@easy.com", hash_senha("admin123"), "Administrador", "admin")
            )
            conn.commit()
            logger.info("Usuário admin padrão criado: admin@easy.com / admin123")
        
        # Config anti-ban padrão
        cur.execute("SELECT COUNT(*) FROM config_antiban")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO config_antiban VALUES (1, 100, 30, 3, 7, 30, 60, '08:00', '20:00', 1)")
            conn.commit()
        
        # Config Z-API (do env, se tiver)
        cur.execute("SELECT COUNT(*) FROM config_zapi WHERE tipo = 'atendimento'")
        if cur.fetchone()[0] == 0 and ZAPI_INSTANCE_ATEND:
            cur.execute(
                "INSERT INTO config_zapi (tipo, instance_id, token, client_token) VALUES (?, ?, ?, ?)",
                ("atendimento", ZAPI_INSTANCE_ATEND, ZAPI_TOKEN_ATEND, ZAPI_CLIENT_TOKEN_ATEND)
            )
            conn.commit()
        
        # Config sistema padrão (robô ativo)
        cur.execute("SELECT COUNT(*) FROM config_sistema WHERE chave = 'robo_ativo'")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO config_sistema (chave, valor) VALUES ('robo_ativo', '1')")
            conn.commit()
        
        # Templates Easy
        cur.execute("SELECT COUNT(*) FROM templates")
        if cur.fetchone()[0] == 0:
            templates = [
                ("Regional - Easy 10 Anos", """Olá {nome}! 🏨

Confira os destaques da semana em [REGIÃO]:

🌟 [Hotel 1]
🌟 [Hotel 2]
🌟 [Hotel 3]

🎉 Comemorando 10 anos da Easy, use o cupom EASY10 e ganhe 10% OFF!

Reserve já: [link]

---
Não quer mais receber? Responda SAIR""", "regional"),
                ("Lançamento - Hotéis Parceiros", """Olá {nome}! ✨

NOVOS PARCEIROS chegaram à Easy!

🆕 [Hotel 1]
🆕 [Hotel 2]
🆕 [Hotel 3]

🎊 10 anos Easy: cupom EASY10 = 10% OFF

Conheça: [link]

---
Não quer mais receber? Responda SAIR""", "lancamento"),
                ("Promo - Ofertas Exclusivas", """Olá {nome}! 🔥

OFERTAS IMPERDÍVEIS:

💰 [Hotel 1] - de R$X por R$Y
💰 [Hotel 2] - de R$X por R$Y

✨ Use EASY10 e ganhe MAIS 10% OFF
🎉 10 anos da Easy

Aproveite: [link]

---
Não quer mais receber? Responda SAIR""", "promocao"),
                ("Aniversário 10 Anos", """Olá {nome}! 🎉

A EASY ESTÁ DE PARABÉNS! 🎂

10 anos transformando viagens em experiências!

🎁 Cupom EASY10 com 10% OFF
Em TODOS os hotéis parceiros!

Use agora: [link]

Obrigado! ❤️

---
Não quer mais receber? Responda SAIR""", "aniversario"),
            ]
            cur.executemany("INSERT INTO templates (nome, conteudo, categoria) VALUES (?, ?, ?)", templates)
            conn.commit()
        
        logger.info("BD inicializado")
        
    except Exception as e:
        logger.error(f"Erro init_db: {e}")
    finally:
        cur.close()
        conn.close()

# ============================================
# AUTH
# ============================================

def validar_token(token: str):
    if not token:
        return None
    conn = get_db()
    if not conn: return None
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT u.id, u.email, u.nome, u.perfil, u.ativo 
            FROM sessoes s JOIN usuarios u ON s.usuario_id = u.id
            WHERE s.token = ?
        """, (token,))
        row = cur.fetchone()
        if row and row['ativo']:
            return dict(row)
        return None
    finally:
        cur.close()
        conn.close()

def get_usuario(request: Request):
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "") if auth else None
    return validar_token(token)

def registrar_log(usuario_id: int, acao: str, detalhes: str = ""):
    conn = get_db()
    if not conn: return
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO logs (usuario_id, acao, detalhes) VALUES (?, ?, ?)", (usuario_id, acao, detalhes))
        conn.commit()
    finally:
        cur.close()
        conn.close()

# ============================================
# Z-API
# ============================================

def get_zapi_config(tipo: str = "atendimento"):
    # Pega config do banco
    conn = get_db()
    if not conn: return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM config_zapi WHERE tipo = ?", (tipo,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()

def enviar_mensagem_zapi(numero: str, mensagem: str, tipo: str = "atendimento") -> dict:
    config = get_zapi_config(tipo)
    if not config:
        return {"sucesso": False, "erro": f"Z-API {tipo} não configurada"}
    
    numero_limpo = numero.replace("+", "").replace(" ", "").replace("-", "")
    url = f"https://api.z-api.io/instances/{config['instance_id']}/token/{config['token']}/send-text"
    headers = {"Content-Type": "application/json", "Client-Token": config['client_token']}
    
    try:
        response = requests.post(url, json={"phone": numero_limpo, "message": mensagem}, headers=headers, timeout=30)
        if response.status_code == 200:
            return {"sucesso": True, "data": response.json()}
        return {"sucesso": False, "erro": response.text}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

def enviar_imagem_zapi(numero: str, imagem_url: str, legenda: str = "", tipo: str = "disparos") -> dict:
    config = get_zapi_config(tipo)
    if not config:
        return {"sucesso": False, "erro": f"Z-API {tipo} não configurada"}
    
    numero_limpo = numero.replace("+", "").replace(" ", "").replace("-", "")
    url = f"https://api.z-api.io/instances/{config['instance_id']}/token/{config['token']}/send-image"
    headers = {"Content-Type": "application/json", "Client-Token": config['client_token']}
    payload = {"phone": numero_limpo, "image": imagem_url}
    if legenda:
        payload["caption"] = legenda
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        if response.status_code == 200:
            return {"sucesso": True, "data": response.json()}
        return {"sucesso": False, "erro": response.text}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

def status_zapi(tipo: str = "atendimento") -> dict:
    config = get_zapi_config(tipo)
    if not config:
        return {"conectado": False, "erro": "Não configurada"}
    
    url = f"https://api.z-api.io/instances/{config['instance_id']}/token/{config['token']}/status"
    headers = {"Client-Token": config['client_token']}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return {"conectado": True, "data": response.json()}
        return {"conectado": False}
    except Exception as e:
        return {"conectado": False, "erro": str(e)}

# ============================================
# ANTI-BAN
# ============================================

def obter_config_antiban():
    conn = get_db()
    if not conn: return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM config_antiban WHERE id = 1")
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()

def obter_envios_hoje():
    conn = get_db()
    if not conn: return 0
    cur = conn.cursor()
    try:
        hoje = date.today().isoformat()
        cur.execute("SELECT total FROM envios_diarios WHERE data = ?", (hoje,))
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        cur.close()
        conn.close()

def incrementar_envio_diario():
    conn = get_db()
    if not conn: return
    cur = conn.cursor()
    try:
        hoje = date.today().isoformat()
        cur.execute("""
            INSERT INTO envios_diarios (data, total) VALUES (?, 1)
            ON CONFLICT(data) DO UPDATE SET total = total + 1
        """, (hoje,))
        conn.commit()
    finally:
        cur.close()
        conn.close()

def pode_enviar():
    config = obter_config_antiban()
    if not config or not config['ativo']:
        return True, "OK"
    agora = datetime.now().strftime("%H:%M")
    if agora < config['horario_inicio'] or agora > config['horario_fim']:
        return False, "Fora do horário"
    if obter_envios_hoje() >= config['limite_diario']:
        return False, "Limite atingido"
    return True, "OK"

# ============================================
# CONFIG SISTEMA
# ============================================

def robo_esta_ativo():
    conn = get_db()
    if not conn: return True  # Default ativo se falhar
    cur = conn.cursor()
    try:
        cur.execute("SELECT valor FROM config_sistema WHERE chave = 'robo_ativo'")
        row = cur.fetchone()
        return row[0] == '1' if row else True
    finally:
        cur.close()
        conn.close()

def toggle_robo(ativo: bool):
    conn = get_db()
    if not conn: return False
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO config_sistema (chave, valor) VALUES ('robo_ativo', ?)
            ON CONFLICT(chave) DO UPDATE SET valor = ?
        """, ('1' if ativo else '0', '1' if ativo else '0'))
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()

# ============================================
# CLAUDE IA
# ============================================

def obter_resposta_ia(mensagem: str, conhecimento_hotel: str = "", nome_hotel: str = "Hotel"):
    # IA especializada em confirmar solicitações hoteleiras SEM opinar como RM
    try:
        prompt_base = f"""Você é EVA, assistente operacional da Easy Hotéis para atendimento aos hotéis administrados.

SEU PAPEL: Confirmar solicitações com CLAREZA. Você NÃO é um Revenue Manager.

REGRAS IMPORTANTES:
- SEMPRE confirme o pedido repetindo os detalhes específicos (hotel, categoria de quarto, data)
- NÃO opine sobre preços ou estratégia de revenue
- NÃO sugira alterações nas tarifas
- NÃO faça recomendações de ocupação
- Seja breve, claro e profissional (máximo 3-4 linhas)
- Use emojis moderadamente (apenas ícones funcionais: 🏨 📅 🛏️ ✅)

TIPOS DE SOLICITAÇÃO COMUNS:
- Fecho de disponibilidade (bloquear categoria de quarto)
- Alteração de tarifa
- Liberação/bloqueio de quartos
- Ajustes operacionais"""

        if conhecimento_hotel:
            prompt_base += f"\n\nCONTEXTO DO HOTEL:\n{conhecimento_hotel}"
        
        prompt_base += f"\n\nCliente ({nome_hotel}) disse: \"{mensagem}\"\n\nResponda confirmando a solicitação de forma clara:"
        
        response = claude_client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=250,
            messages=[{"role": "user", "content": prompt_base}]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Erro IA: {e}")
        return "Desculpe, tive um problema técnico. Um atendente humano vai te ajudar em breve."

# ============================================
# ROTAS - AUTH
# ============================================

@app.get("/")
async def root():
    return {"mensagem": "EVA Hotel Backend", "versao": "5.0.0", "status": "OK"}

@app.get("/painel")
async def painel():
    return FileResponse("painel.html", media_type="text/html")

@app.post("/api/login")
async def login(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").lower().strip()
        senha = data.get("senha", "")
        
        if not email or not senha:
            return JSONResponse({"sucesso": False, "erro": "Email e senha obrigatórios"}, status_code=400)
        
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM usuarios WHERE email = ? AND ativo = 1", (email,))
            user = cur.fetchone()
            
            if not user or user['senha_hash'] != hash_senha(senha):
                return JSONResponse({"sucesso": False, "erro": "Email ou senha incorretos"}, status_code=401)
            
            # Gera token
            token = gerar_token()
            cur.execute("INSERT INTO sessoes (token, usuario_id) VALUES (?, ?)", (token, user['id']))
            cur.execute("UPDATE usuarios SET ultimo_login = CURRENT_TIMESTAMP WHERE id = ?", (user['id'],))
            conn.commit()
            
            registrar_log(user['id'], "login", f"Login realizado")
            
            return {
                "sucesso": True,
                "token": token,
                "usuario": {
                    "id": user['id'],
                    "nome": user['nome'],
                    "email": user['email'],
                    "perfil": user['perfil']
                }
            }
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.post("/api/logout")
async def logout(request: Request):
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "") if auth else None
    if token:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM sessoes WHERE token = ?", (token,))
        conn.commit()
        cur.close()
        conn.close()
    return {"sucesso": True}

@app.get("/api/me")
async def me(request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    return {"sucesso": True, "usuario": user}

# ============================================
# USUARIOS (admin)
# ============================================

@app.get("/api/usuarios")
async def listar_usuarios(request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] != 'admin':
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, email, nome, perfil, ativo, criado_em, ultimo_login FROM usuarios ORDER BY nome ASC")
        rows = cur.fetchall()
        return {"sucesso": True, "usuarios": [dict(r) for r in rows]}
    finally:
        cur.close()
        conn.close()

@app.post("/api/usuarios")
async def criar_usuario(request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] != 'admin':
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    try:
        data = await request.json()
        email = data.get("email", "").lower().strip()
        senha = data.get("senha", "")
        nome = data.get("nome", "")
        perfil = data.get("perfil", "atendente")
        
        if not email or not senha or not nome:
            return JSONResponse({"sucesso": False, "erro": "Preencha todos os campos"}, status_code=400)
        
        if perfil not in ["admin", "atendente", "marketing"]:
            return JSONResponse({"sucesso": False, "erro": "Perfil inválido"}, status_code=400)
        
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO usuarios (email, senha_hash, nome, perfil) VALUES (?, ?, ?, ?)",
                (email, hash_senha(senha), nome, perfil)
            )
            conn.commit()
            registrar_log(user['id'], "criar_usuario", f"Criou {email} ({perfil})")
            return {"sucesso": True, "id": cur.lastrowid}
        except sqlite3.IntegrityError:
            return JSONResponse({"sucesso": False, "erro": "Email já cadastrado"}, status_code=400)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.put("/api/usuarios/{user_id}")
async def atualizar_usuario(user_id: int, request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] != 'admin':
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    try:
        data = await request.json()
        conn = get_db()
        cur = conn.cursor()
        
        campos = []
        valores = []
        
        if "nome" in data:
            campos.append("nome = ?")
            valores.append(data["nome"])
        if "perfil" in data and data["perfil"] in ["admin", "atendente", "marketing"]:
            campos.append("perfil = ?")
            valores.append(data["perfil"])
        if "ativo" in data:
            campos.append("ativo = ?")
            valores.append(1 if data["ativo"] else 0)
        if "senha" in data and data["senha"]:
            campos.append("senha_hash = ?")
            valores.append(hash_senha(data["senha"]))
        
        if campos:
            valores.append(user_id)
            cur.execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE id = ?", valores)
            conn.commit()
            registrar_log(user['id'], "atualizar_usuario", f"ID {user_id}")
        
        cur.close()
        conn.close()
        return {"sucesso": True}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.delete("/api/usuarios/{user_id}")
async def deletar_usuario(user_id: int, request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] != 'admin':
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    if user_id == user['id']:
        return JSONResponse({"sucesso": False, "erro": "Não pode deletar a si mesmo"}, status_code=400)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))
    cur.execute("DELETE FROM sessoes WHERE usuario_id = ?", (user_id,))
    conn.commit()
    cur.close()
    conn.close()
    registrar_log(user['id'], "deletar_usuario", f"ID {user_id}")
    return {"sucesso": True}

# ============================================
# CONFIG Z-API
# ============================================

@app.get("/api/config/zapi")
async def get_config_zapi(request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] != 'admin':
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM config_zapi")
    rows = cur.fetchall()
    configs = {row['tipo']: dict(row) for row in rows}
    cur.close()
    conn.close()
    return {"sucesso": True, "configs": configs}

@app.post("/api/config/zapi")
async def salvar_config_zapi(request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] != 'admin':
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    try:
        data = await request.json()
        tipo = data.get("tipo")
        if tipo not in ["atendimento", "disparos"]:
            return JSONResponse({"sucesso": False, "erro": "Tipo inválido"}, status_code=400)
        
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO config_zapi (tipo, instance_id, token, client_token) VALUES (?, ?, ?, ?)
            ON CONFLICT(tipo) DO UPDATE SET instance_id = ?, token = ?, client_token = ?
        """, (tipo, data.get("instance_id"), data.get("token"), data.get("client_token"),
              data.get("instance_id"), data.get("token"), data.get("client_token")))
        conn.commit()
        cur.close()
        conn.close()
        registrar_log(user['id'], "config_zapi", tipo)
        return {"sucesso": True}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

# ============================================
# WEBHOOK
# ============================================

@app.post("/webhook/zapi")
async def webhook(request: Request):
    try:
        data = await request.json()
        if data.get("fromMe"):
            return {"success": True}
        
        numero = data.get("phone")
        nome = data.get("senderName", "")
        mensagem = data.get("text", {}).get("message") if data.get("text") else None
        
        if not numero or not mensagem:
            return {"success": False}
        
        # Verifica se robô está ativo
        if not robo_esta_ativo():
            logger.info(f"Robô pausado - mensagem de {numero} não será respondida automaticamente")
            # Só salva a mensagem, não responde
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id FROM conversas WHERE numero_cliente = ? AND status = 'aberto' LIMIT 1", (numero,))
            row = cur.fetchone()
            if row:
                conversa_id = row[0]
            else:
                cur.execute("INSERT INTO conversas (numero_cliente, nome_cliente, status) VALUES (?, ?, 'aberto')", (numero, nome))
                conversa_id = cur.lastrowid
            cur.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (?, ?, ?)", (conversa_id, "cliente", mensagem))
            conn.commit()
            cur.close()
            conn.close()
            return {"success": True, "robo": "pausado"}
        
        conn = get_db()
        cur = conn.cursor()
        
        # Busca ou cria conversa
        cur.execute("SELECT id FROM conversas WHERE numero_cliente = ? AND status = 'aberto' LIMIT 1", (numero,))
        row = cur.fetchone()
        
        if row:
            conversa_id = row[0]
        else:
            cur.execute("INSERT INTO conversas (numero_cliente, nome_cliente, status) VALUES (?, ?, 'aberto')", (numero, nome))
            conversa_id = cur.lastrowid
        
        # Busca conhecimento do hotel (se cadastrado como contato)
        cur.execute("SELECT conhecimento_ia, nome FROM contatos WHERE numero = ?", (numero,))
        contato = cur.fetchone()
        conhecimento = contato[0] if contato and contato[0] else ""
        nome_hotel = contato[1] if contato else nome or "Hotel"
        
        cur.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (?, ?, ?)", (conversa_id, "cliente", mensagem))
        conn.commit()
        cur.close()
        conn.close()
        
        # IA responde com contexto do hotel
        resposta = obter_resposta_ia(mensagem, conhecimento, nome_hotel)
        
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (?, ?, ?)", (conversa_id, "eva", resposta))
        conn.commit()
        cur.close()
        conn.close()
        
        enviar_mensagem_zapi(numero, resposta, "atendimento")
        return {"success": True}
    except Exception as e:
        logger.error(f"Erro webhook: {e}")
        return JSONResponse({"success": False, "erro": str(e)}, status_code=500)

# ============================================
# CONVERSAS
# ============================================

@app.get("/api/conversas/abertas")
async def conv_abertas(request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.*, 
            (SELECT COUNT(*) FROM mensagens WHERE conversa_id = c.id) as total_mensagens,
            (SELECT conteudo FROM mensagens WHERE conversa_id = c.id ORDER BY criado_em DESC LIMIT 1) as ultima_mensagem
        FROM conversas c WHERE status = 'aberto' ORDER BY criado_em DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"sucesso": True, "conversas": [dict(r) for r in rows], "total": len(rows)}

@app.get("/api/conversas/fechadas")
async def conv_fechadas(request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM mensagens WHERE conversa_id = c.id) as total_mensagens
        FROM conversas c WHERE status = 'fechado' ORDER BY fechado_em DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"sucesso": True, "conversas": [dict(r) for r in rows], "total": len(rows)}

@app.get("/api/conversas/{cid}/mensagens")
async def msgs_conversa(cid: int, request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM mensagens WHERE conversa_id = ? ORDER BY criado_em ASC", (cid,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"sucesso": True, "mensagens": [dict(r) for r in rows]}

@app.post("/api/conversas/{cid}/fechar")
async def fechar_conv(cid: int, request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    
    try:
        data = await request.json()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT numero_cliente FROM conversas WHERE id = ?", (cid,))
        row = cur.fetchone()
        numero = row[0] if row else None
        
        cur.execute("""
            UPDATE conversas SET status = 'fechado', fechado_em = CURRENT_TIMESTAMP,
                fechado_por_id = ?, fechado_por_nome = ?, observacoes = ? WHERE id = ?
        """, (user['id'], user['nome'], data.get("observacoes", ""), cid))
        conn.commit()
        cur.close()
        conn.close()
        
        if numero:
            mensagem_final = f"*{user['nome']}:*\nObrigado por entrar em contato! Atendimento encerrado. 😊"
            enviar_mensagem_zapi(numero, mensagem_final, "atendimento")
        
        registrar_log(user['id'], "fechar_conversa", f"ID {cid}")
        return {"sucesso": True}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.post("/api/conversas/{cid}/responder")
async def responder_conv(cid: int, request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    
    try:
        data = await request.json()
        mensagem = data.get("mensagem", "")
        
        if not mensagem:
            return JSONResponse({"sucesso": False, "erro": "Mensagem vazia"}, status_code=400)
        
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT numero_cliente FROM conversas WHERE id = ?", (cid,))
        row = cur.fetchone()
        if not row:
            return JSONResponse({"sucesso": False, "erro": "Conversa não encontrada"}, status_code=404)
        
        numero = row[0]
        
        # Mensagem com nome do atendente em negrito
        mensagem_final = f"*{user['nome']}:*\n{mensagem}"
        
        # Salva no banco
        cur.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo, usuario_nome) VALUES (?, ?, ?, ?)",
                    (cid, "atendente", mensagem_final, user['nome']))
        conn.commit()
        cur.close()
        conn.close()
        
        # Envia
        resultado = enviar_mensagem_zapi(numero, mensagem_final, "atendimento")
        return {"sucesso": True, "envio": resultado}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

# ============================================
# CONTATOS
# ============================================

@app.get("/api/contatos")
async def listar_contatos(request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM contatos ORDER BY nome ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"sucesso": True, "contatos": [dict(r) for r in rows], "total": len(rows)}

@app.post("/api/contatos")
async def criar_contato(request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] not in ['admin', 'marketing']:
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    try:
        data = await request.json()
        if not data.get("nome") or not data.get("numero"):
            return JSONResponse({"sucesso": False, "erro": "Nome e número obrigatórios"}, status_code=400)
        
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO contatos (nome, numero, email, tags, observacoes, conhecimento_ia) VALUES (?, ?, ?, ?, ?, ?)",
                (data["nome"], data["numero"], data.get("email", ""), data.get("tags", ""), 
                 data.get("observacoes", ""), data.get("conhecimento_ia", ""))
            )
            conn.commit()
            return {"sucesso": True, "id": cur.lastrowid}
        except sqlite3.IntegrityError:
            return JSONResponse({"sucesso": False, "erro": "Número já cadastrado"}, status_code=400)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.delete("/api/contatos/{cid}")
async def deletar_contato(cid: int, request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] not in ['admin', 'marketing']:
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM contatos WHERE id = ?", (cid,))
    conn.commit()
    cur.close()
    conn.close()
    return {"sucesso": True}

@app.post("/api/contatos/importar")
async def importar_contatos(file: UploadFile = File(...)):
    try:
        content = await file.read()
        text = content.decode('utf-8')
        reader = csv.DictReader(io.StringIO(text))
        
        conn = get_db()
        cur = conn.cursor()
        importados = 0
        erros = 0
        
        for row in reader:
            try:
                nome = row.get('nome') or row.get('Nome') or row.get('NOME')
                numero = row.get('numero') or row.get('Numero') or row.get('NUMERO') or row.get('telefone') or row.get('Telefone')
                email = row.get('email') or row.get('Email') or ""
                
                if nome and numero:
                    cur.execute("INSERT OR IGNORE INTO contatos (nome, numero, email) VALUES (?, ?, ?)", (nome, numero, email))
                    if cur.rowcount > 0:
                        importados += 1
                    else:
                        erros += 1
            except:
                erros += 1
        
        conn.commit()
        cur.close()
        conn.close()
        return {"sucesso": True, "importados": importados, "erros": erros}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.post("/api/contatos/preview-csv")
async def preview_csv(file: UploadFile = File(...)):
    # Faz preview dos contatos do CSV sem salvar (pra usar em campanha)
    try:
        content = await file.read()
        text = content.decode('utf-8')
        reader = csv.DictReader(io.StringIO(text))
        
        contatos_csv = []
        for row in reader:
            nome = row.get('nome') or row.get('Nome') or row.get('NOME')
            numero = row.get('numero') or row.get('Numero') or row.get('NUMERO') or row.get('telefone') or row.get('Telefone')
            if nome and numero:
                contatos_csv.append({"nome": nome, "numero": numero})
        
        return {"sucesso": True, "contatos": contatos_csv, "total": len(contatos_csv)}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

# ============================================
# UPLOAD IMAGEM
# ============================================

@app.post("/api/upload/imagem")
async def upload_imagem(file: UploadFile = File(...)):
    try:
        ext = file.filename.split(".")[-1].lower()
        if ext not in ["jpg", "jpeg", "png", "webp"]:
            return JSONResponse({"sucesso": False, "erro": "Formato inválido"}, status_code=400)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        nome = f"campanha_{timestamp}.{ext}"
        caminho = os.path.join(UPLOAD_DIR, nome)
        
        content = await file.read()
        with open(caminho, "wb") as f:
            f.write(content)
        
        return {"sucesso": True, "url": f"/uploads/{nome}"}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

# ============================================
# TEMPLATES
# ============================================

@app.get("/api/templates")
async def listar_templates(request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM templates ORDER BY nome ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"sucesso": True, "templates": [dict(r) for r in rows]}

@app.post("/api/templates")
async def criar_template(request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] not in ['admin', 'marketing']:
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    try:
        data = await request.json()
        if not data.get("nome") or not data.get("conteudo"):
            return JSONResponse({"sucesso": False, "erro": "Dados incompletos"}, status_code=400)
        
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO templates (nome, conteudo, categoria) VALUES (?, ?, ?)",
                    (data["nome"], data["conteudo"], data.get("categoria", "geral")))
        conn.commit()
        cur.close()
        conn.close()
        return {"sucesso": True}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.delete("/api/templates/{tid}")
async def deletar_template(tid: int, request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] not in ['admin', 'marketing']:
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM templates WHERE id = ?", (tid,))
    conn.commit()
    cur.close()
    conn.close()
    return {"sucesso": True}

# ============================================
# CAMPANHAS
# ============================================

@app.get("/api/campanhas")
async def listar_campanhas(request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM campanhas ORDER BY criado_em DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"sucesso": True, "campanhas": [dict(r) for r in rows]}

@app.post("/api/campanhas")
async def criar_campanha(request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] not in ['admin', 'marketing']:
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    try:
        data = await request.json()
        nome = data.get("nome")
        mensagem = data.get("mensagem")
        contato_ids = data.get("contato_ids", [])
        contatos_csv = data.get("contatos_csv", [])  # NOVO: contatos vindo de CSV
        imagem_url = data.get("imagem_url")
        
        if not nome or not mensagem:
            return JSONResponse({"sucesso": False, "erro": "Dados incompletos"}, status_code=400)
        
        # Junta contatos do banco + CSV
        total_contatos = len(contato_ids) + len(contatos_csv)
        if total_contatos == 0:
            return JSONResponse({"sucesso": False, "erro": "Selecione contatos"}, status_code=400)
        
        # Valida anti-ban
        config = obter_config_antiban()
        enviados_hoje = obter_envios_hoje()
        if config and config['ativo']:
            disponivel = config['limite_diario'] - enviados_hoje
            if total_contatos > disponivel:
                return JSONResponse({"sucesso": False, "erro": f"Só pode enviar mais {disponivel} hoje"}, status_code=400)
        
        conn = get_db()
        cur = conn.cursor()
        
        cur.execute(
            "INSERT INTO campanhas (nome, template_id, mensagem, imagem_url, total_contatos, status, criado_por_id, criado_por_nome) VALUES (?, ?, ?, ?, ?, 'pendente', ?, ?)",
            (nome, data.get("template_id"), mensagem, imagem_url, total_contatos, user['id'], user['nome'])
        )
        campanha_id = cur.lastrowid
        
        # Contatos do banco
        for contato_id in contato_ids:
            cur.execute("SELECT nome, numero FROM contatos WHERE id = ?", (contato_id,))
            row = cur.fetchone()
            if row:
                cur.execute("INSERT INTO envios (campanha_id, contato_id, numero, nome, status) VALUES (?, ?, ?, ?, 'pendente')",
                            (campanha_id, contato_id, row[1], row[0]))
        
        # Contatos do CSV (sem salvar na lista de contatos)
        for c in contatos_csv:
            cur.execute("INSERT INTO envios (campanha_id, contato_id, numero, nome, status) VALUES (?, NULL, ?, ?, 'pendente')",
                        (campanha_id, c['numero'], c['nome']))
        
        conn.commit()
        cur.close()
        conn.close()
        
        asyncio.create_task(processar_campanha(campanha_id))
        registrar_log(user['id'], "criar_campanha", f"{nome} ({total_contatos} contatos)")
        
        return {"sucesso": True, "campanha_id": campanha_id}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

async def processar_campanha(campanha_id: int):
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("UPDATE campanhas SET status = 'enviando', iniciado_em = CURRENT_TIMESTAMP WHERE id = ?", (campanha_id,))
        conn.commit()
        
        cur.execute("SELECT mensagem, imagem_url FROM campanhas WHERE id = ?", (campanha_id,))
        c = cur.fetchone()
        if not c: return
        
        mensagem_base, imagem_url = c[0], c[1]
        config = obter_config_antiban()
        
        imagem_completa = None
        if imagem_url:
            base = os.getenv("RAILWAY_PUBLIC_DOMAIN", "web-production-69fb05.up.railway.app")
            imagem_completa = f"https://{base}{imagem_url}" if base else None
        
        cur.execute("SELECT id, numero, nome FROM envios WHERE campanha_id = ? AND status = 'pendente'", (campanha_id,))
        envios = cur.fetchall()
        
        enviadas = 0
        falhadas = 0
        
        for idx, envio in enumerate(envios):
            envio_id, numero, nome = envio
            
            ok, motivo = pode_enviar()
            if not ok:
                cur.execute("UPDATE envios SET status = 'pausado', resposta = ? WHERE id = ?", (motivo, envio_id))
                conn.commit()
                continue
            
            if config and idx > 0 and idx % config['pausa_a_cada'] == 0:
                await asyncio.sleep(config['pausa_segundos'])
            
            mensagem_final = mensagem_base.replace("{nome}", nome or "").replace("{numero}", numero or "")
            
            # Usa Z-API de DISPAROS!
            if imagem_completa:
                resultado = enviar_imagem_zapi(numero, imagem_completa, mensagem_final, "disparos")
            else:
                resultado = enviar_mensagem_zapi(numero, mensagem_final, "disparos")
            
            if resultado.get("sucesso"):
                cur.execute("UPDATE envios SET status = 'enviado', enviado_em = CURRENT_TIMESTAMP WHERE id = ?", (envio_id,))
                enviadas += 1
                incrementar_envio_diario()
            else:
                cur.execute("UPDATE envios SET status = 'falhou', resposta = ? WHERE id = ?", (json.dumps(resultado), envio_id))
                falhadas += 1
            
            cur.execute("UPDATE campanhas SET enviadas = ?, falhadas = ? WHERE id = ?", (enviadas, falhadas, campanha_id))
            conn.commit()
            
            delay = random.uniform(config['delay_min'], config['delay_max']) if config else 4
            await asyncio.sleep(delay)
        
        cur.execute("UPDATE campanhas SET status = 'finalizada', finalizado_em = CURRENT_TIMESTAMP WHERE id = ?", (campanha_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"Erro campanha: {e}")
        cur.execute("UPDATE campanhas SET status = 'erro' WHERE id = ?", (campanha_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()

# ============================================
# ANTI-BAN
# ============================================

@app.get("/api/antiban/config")
async def get_antiban(request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    
    config = obter_config_antiban()
    enviados = obter_envios_hoje()
    return {
        "sucesso": True,
        "config": config,
        "envios_hoje": enviados,
        "disponivel_hoje": (config['limite_diario'] - enviados) if config else 0
    }

@app.post("/api/antiban/config")
async def salvar_antiban(request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] != 'admin':
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    try:
        d = await request.json()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""UPDATE config_antiban SET 
            limite_diario=?, limite_por_hora=?, delay_min=?, delay_max=?,
            pausa_a_cada=?, pausa_segundos=?, horario_inicio=?, horario_fim=?, ativo=? WHERE id=1""",
            (d.get("limite_diario",100), d.get("limite_por_hora",30), d.get("delay_min",3), d.get("delay_max",7),
             d.get("pausa_a_cada",30), d.get("pausa_segundos",60), d.get("horario_inicio","08:00"),
             d.get("horario_fim","20:00"), 1 if d.get("ativo",True) else 0))
        conn.commit()
        cur.close()
        conn.close()
        return {"sucesso": True}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

# ============================================
# RELATÓRIOS
# ============================================

@app.get("/api/relatorios")
async def relatorios(request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM conversas")
    total_conv = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM conversas WHERE status='aberto'")
    abertos = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM conversas WHERE status='fechado'")
    fechados = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM contatos")
    contatos = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM campanhas")
    camps = cur.fetchone()[0]
    cur.execute("SELECT SUM(enviadas) FROM campanhas")
    enviadas = cur.fetchone()[0] or 0
    
    cur.execute("SELECT AVG((julianday(fechado_em) - julianday(criado_em))*24*60) FROM conversas WHERE status='fechado'")
    tempo = cur.fetchone()[0]
    tempo_medio = int(tempo) if tempo else 0
    
    # Top atendentes
    cur.execute("""
        SELECT fechado_por_nome, COUNT(*) as total
        FROM conversas WHERE status='fechado' AND fechado_por_nome IS NOT NULL
        GROUP BY fechado_por_nome ORDER BY total DESC LIMIT 5
    """)
    top_atendentes = [dict(r) for r in cur.fetchall()]
    
    cur.close()
    conn.close()
    
    return {
        "sucesso": True,
        "relatorios": {
            "totalConversas": total_conv, "abertos": abertos, "fechados": fechados,
            "tempoMedioMinutos": tempo_medio, "totalContatos": contatos,
            "totalCampanhas": camps, "totalEnviadas": enviadas,
            "enviadosHoje": obter_envios_hoje(),
            "topAtendentes": top_atendentes
        }
    }

@app.get("/api/zapi/status")
async def zapi_status(request: Request):
    return {"atendimento": status_zapi("atendimento"), "disparos": status_zapi("disparos")}

@app.get("/api/sistema/robo")
async def get_status_robo(request: Request):
    user = get_usuario(request)
    if not user:
        return JSONResponse({"sucesso": False}, status_code=401)
    return {"sucesso": True, "ativo": robo_esta_ativo()}

@app.post("/api/sistema/robo")
async def toggle_status_robo(request: Request):
    user = get_usuario(request)
    if not user or user['perfil'] != 'admin':
        return JSONResponse({"sucesso": False, "erro": "Sem permissão"}, status_code=403)
    
    try:
        data = await request.json()
        ativo = data.get("ativo", True)
        sucesso = toggle_robo(ativo)
        if sucesso:
            registrar_log(user['id'], "toggle_robo", f"Robô {'ativado' if ativo else 'pausado'}")
        return {"sucesso": sucesso, "ativo": ativo}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

if __name__ == "__main__":
    print("\n" + "="*60)
    print("EVA Hotel Backend v5.0 - Sistema Completo")
    print("="*60)
    init_db()
    print(f"URL: http://localhost:{PORT}")
    print(f"Login padrão: admin@easy.com / admin123")
    print("="*60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
