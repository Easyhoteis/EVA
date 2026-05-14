from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
import base64
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
from typing import Optional, List

from anthropic import Anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

ZAPI_INSTANCE = os.getenv("ZAPI_INSTANCE", "")
ZAPI_TOKEN = os.getenv("ZAPI_TOKEN", "")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "")
ZAPI_URL = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}"
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY")
PORT = int(os.getenv("PORT", 3000))

# Pasta pra salvar imagens
UPLOAD_DIR = "/tmp/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="EVA Hotel Backend", version="4.0.0")
claude_client = Anthropic(api_key=CLAUDE_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir uploads
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

DB_PATH = "/tmp/eva.db"

def get_db_connection():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"Erro ao conectar no BD: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_cliente TEXT NOT NULL,
                nome_cliente TEXT,
                status TEXT DEFAULT 'aberto',
                usuario_id INTEGER,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fechado_em TIMESTAMP,
                observacoes TEXT
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mensagens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversa_id INTEGER NOT NULL,
                remetente TEXT NOT NULL,
                conteudo TEXT NOT NULL,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversa_id) REFERENCES conversas(id)
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS contatos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                numero TEXT UNIQUE NOT NULL,
                email TEXT,
                tags TEXT,
                observacoes TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                conteudo TEXT NOT NULL,
                categoria TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
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
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                iniciado_em TIMESTAMP,
                finalizado_em TIMESTAMP
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS envios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campanha_id INTEGER,
                contato_id INTEGER,
                numero TEXT,
                status TEXT DEFAULT 'pendente',
                resposta TEXT,
                enviado_em TIMESTAMP,
                FOREIGN KEY (campanha_id) REFERENCES campanhas(id),
                FOREIGN KEY (contato_id) REFERENCES contatos(id)
            );
        """)
        
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
                ativo BOOLEAN DEFAULT 1
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS envios_diarios (
                data TEXT PRIMARY KEY,
                total INTEGER DEFAULT 0
            );
        """)
        
        conn.commit()
        
        cur.execute("SELECT COUNT(*) FROM config_antiban")
        if cur.fetchone()[0] == 0:
            cur.execute("""
                INSERT INTO config_antiban VALUES (1, 100, 30, 3, 7, 30, 60, '08:00', '20:00', 1)
            """)
            conn.commit()
        
        # Templates da Easy 10 anos
        cur.execute("SELECT COUNT(*) FROM templates")
        if cur.fetchone()[0] == 0:
            templates_easy = [
                (
                    "Regional - Easy 10 Anos",
                    """Olá {nome}! 🏨

Confira os destaques da semana em [REGIÃO]:

🌟 [Hotel 1] - [diferencial]
🌟 [Hotel 2] - [diferencial]
🌟 [Hotel 3] - [diferencial]

🎉 Comemorando 10 anos da Easy, use o cupom EASY10 e ganhe 10% OFF!

Reserve já: [link]

---
Não quer mais receber? Responda SAIR""",
                    "regional"
                ),
                (
                    "Lançamento - Hotéis Parceiros",
                    """Olá {nome}! ✨

NOVOS PARCEIROS chegaram à Easy!

Este mês, novidades especiais:
🆕 [Hotel 1] - [diferencial]
🆕 [Hotel 2] - [diferencial]
🆕 [Hotel 3] - [diferencial]

🎊 10 anos Easy: cupom EASY10 = 10% OFF
Em todos os novos parceiros!

Conheça: [link]

---
Não quer mais receber? Responda SAIR""",
                    "lancamento"
                ),
                (
                    "Promo - Ofertas Exclusivas",
                    """Olá {nome}! 🔥

OFERTAS IMPERDÍVEIS da semana:

💰 [Hotel 1] - de R$X por R$Y
💰 [Hotel 2] - de R$X por R$Y
💰 [Hotel 3] - de R$X por R$Y

✨ Use EASY10 e ganhe MAIS 10% OFF
🎉 Em comemoração aos 10 anos da Easy

Aproveite: [link]

---
Não quer mais receber? Responda SAIR""",
                    "promocao"
                ),
                (
                    "Aniversário 10 Anos - Easy",
                    """Olá {nome}! 🎉

A EASY ESTÁ DE PARABÉNS! 🎂

Há 10 anos transformando viagens em experiências inesquecíveis!

🎁 PRESENTE PRA VOCÊ:
Cupom EASY10 com 10% OFF
Válido em TODOS os hotéis parceiros!

Use agora: [link]

Obrigado por fazer parte dessa história! ❤️

---
Não quer mais receber? Responda SAIR""",
                    "aniversario"
                ),
                (
                    "Confirmação de Reserva",
                    """Olá {nome}! 

Sua reserva foi confirmada com sucesso! ✅

Detalhes:
🏨 Hotel: [nome]
📅 Check-in: [data]
📅 Check-out: [data]
🛏️ Acomodação: [tipo]

Aguardamos sua chegada!

Qualquer dúvida, estamos à disposição.""",
                    "reserva"
                ),
                (
                    "Lembrete Check-in",
                    """Olá {nome}! 🏨

Lembrando que seu check-in é AMANHÃ!

📍 [Endereço do hotel]
🕒 Horário: a partir das 14h

Tenha uma ótima estadia! 

A Easy está com você nessa jornada! 💙""",
                    "lembrete"
                ),
                (
                    "Pós-Estadia - Feedback",
                    """Olá {nome}! 

Esperamos que tenha tido uma ótima estadia! 

Sua opinião é muito importante pra gente. 
Como foi sua experiência?

⭐⭐⭐⭐⭐ Excelente
⭐⭐⭐⭐ Muito bom
⭐⭐⭐ Bom
⭐⭐ Regular
⭐ Ruim

Conte pra gente! 💙""",
                    "feedback"
                ),
            ]
            cur.executemany(
                "INSERT INTO templates (nome, conteudo, categoria) VALUES (?, ?, ?)",
                templates_easy
            )
            conn.commit()
        
        logger.info("BD inicializado")
            
    except Exception as e:
        logger.error(f"Erro: {e}")
    finally:
        cur.close()
        conn.close()

# ============================================
# ANTI-BAN
# ============================================

def obter_config_antiban():
    conn = get_db_connection()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM config_antiban WHERE id = 1")
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()

def obter_envios_hoje():
    conn = get_db_connection()
    if not conn:
        return 0
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
    conn = get_db_connection()
    if not conn:
        return
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

def verificar_pode_enviar():
    config = obter_config_antiban()
    if not config or not config['ativo']:
        return True, "OK"
    
    agora = datetime.now()
    horario_atual = agora.strftime("%H:%M")
    if horario_atual < config['horario_inicio'] or horario_atual > config['horario_fim']:
        return False, f"Fora do horário"
    
    enviados_hoje = obter_envios_hoje()
    if enviados_hoje >= config['limite_diario']:
        return False, f"Limite diário atingido"
    
    return True, "OK"

# ============================================
# Z-API
# ============================================

def enviar_mensagem_zapi(numero: str, mensagem: str) -> dict:
    numero_limpo = numero.replace("+", "").replace(" ", "").replace("-", "")
    
    headers = {
        "Content-Type": "application/json",
        "Client-Token": ZAPI_CLIENT_TOKEN
    }
    
    try:
        response = requests.post(
            f"{ZAPI_URL}/send-text",
            json={"phone": numero_limpo, "message": mensagem},
            headers=headers,
            timeout=30
        )
        if response.status_code == 200:
            return {"sucesso": True, "data": response.json()}
        return {"sucesso": False, "erro": response.text}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

def enviar_imagem_zapi(numero: str, imagem_url: str, legenda: str = "") -> dict:
    # Envia imagem via Z-API
    numero_limpo = numero.replace("+", "").replace(" ", "").replace("-", "")
    
    headers = {
        "Content-Type": "application/json",
        "Client-Token": ZAPI_CLIENT_TOKEN
    }
    
    payload = {
        "phone": numero_limpo,
        "image": imagem_url
    }
    
    if legenda:
        payload["caption"] = legenda
    
    try:
        response = requests.post(
            f"{ZAPI_URL}/send-image",
            json=payload,
            headers=headers,
            timeout=60
        )
        if response.status_code == 200:
            return {"sucesso": True, "data": response.json()}
        return {"sucesso": False, "erro": response.text}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

def status_zapi() -> dict:
    headers = {"Client-Token": ZAPI_CLIENT_TOKEN}
    try:
        response = requests.get(f"{ZAPI_URL}/status", headers=headers, timeout=10)
        if response.status_code == 200:
            return {"conectado": True, "data": response.json()}
        return {"conectado": False}
    except Exception as e:
        return {"conectado": False, "erro": str(e)}

# ============================================
# CLAUDE
# ============================================

def obter_resposta_ia(mensagem_cliente: str, contexto_hotel: str = "Hotel"):
    try:
        response = claude_client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": f"""Você é um assistente de atendimento ao cliente de um {contexto_hotel}.

Responda de forma breve, amigável e profissional. Máximo 2-3 linhas.

Cliente disse: "{mensagem_cliente}"

Responda:"""
            }]
        )
        return response.content[0].text
    except Exception as e:
        return "Desculpe, tive um problema. Vou chamar um atendente."

# ============================================
# BANCO
# ============================================

def buscar_conversa_aberta(numero_cliente: str):
    conn = get_db_connection()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM conversas WHERE numero_cliente = ? AND status = ? LIMIT 1", (numero_cliente, "aberto"))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()
        conn.close()

def salvar_conversa(numero_cliente: str, nome_cliente: str = None):
    conn = get_db_connection()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO conversas (numero_cliente, nome_cliente, status) VALUES (?, ?, ?)", (numero_cliente, nome_cliente, "aberto"))
        conn.commit()
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()

def salvar_mensagem(conversa_id: int, remetente: str, conteudo: str):
    conn = get_db_connection()
    if not conn:
        return
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (?, ?, ?)", (conversa_id, remetente, conteudo))
        conn.commit()
    finally:
        cur.close()
        conn.close()

# ============================================
# ROTAS
# ============================================

@app.get("/")
async def root():
    return {"mensagem": "EVA Hotel Backend", "status": "OK", "versao": "4.0.0"}

@app.get("/painel")
async def painel():
    return FileResponse("painel.html", media_type="text/html")

@app.post("/webhook/zapi")
async def webhook_zapi(request: Request):
    try:
        data = await request.json()
        if data.get("fromMe"):
            return JSONResponse({"success": True})
        
        numero_cliente = data.get("phone")
        nome_cliente = data.get("senderName", "")
        
        mensagem_cliente = None
        if "text" in data and data["text"]:
            mensagem_cliente = data["text"].get("message")
        
        if not numero_cliente or not mensagem_cliente:
            return JSONResponse({"success": False})
        
        conversa_id = buscar_conversa_aberta(numero_cliente)
        if not conversa_id:
            conversa_id = salvar_conversa(numero_cliente, nome_cliente)
        
        salvar_mensagem(conversa_id, "cliente", mensagem_cliente)
        resposta = obter_resposta_ia(mensagem_cliente)
        salvar_mensagem(conversa_id, "eva", resposta)
        enviar_mensagem_zapi(numero_cliente, resposta)
        
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "erro": str(e)}, status_code=500)

# ============================================
# CONVERSAS
# ============================================

@app.get("/api/conversas/abertas")
async def conversas_abertas():
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "conversas": [], "total": 0}
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT c.id, c.numero_cliente, c.nome_cliente, c.criado_em,
                (SELECT COUNT(*) FROM mensagens WHERE conversa_id = c.id) as total_mensagens,
                (SELECT conteudo FROM mensagens WHERE conversa_id = c.id ORDER BY criado_em DESC LIMIT 1) as ultima_mensagem
            FROM conversas c WHERE c.status = 'aberto' ORDER BY c.criado_em DESC
        """)
        rows = cur.fetchall()
        return {"sucesso": True, "conversas": [dict(r) for r in rows], "total": len(rows)}
    finally:
        cur.close()
        conn.close()

@app.get("/api/conversas/fechadas")
async def conversas_fechadas():
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "conversas": [], "total": 0}
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT c.id, c.numero_cliente, c.nome_cliente, c.criado_em, c.fechado_em,
                (SELECT COUNT(*) FROM mensagens WHERE conversa_id = c.id) as total_mensagens
            FROM conversas c WHERE c.status = 'fechado' ORDER BY c.fechado_em DESC
        """)
        rows = cur.fetchall()
        return {"sucesso": True, "conversas": [dict(r) for r in rows], "total": len(rows)}
    finally:
        cur.close()
        conn.close()

@app.get("/api/conversas/{conversa_id}/mensagens")
async def mensagens_conversa(conversa_id: int):
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "mensagens": []}
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM mensagens WHERE conversa_id = ? ORDER BY criado_em ASC", (conversa_id,))
        rows = cur.fetchall()
        return {"sucesso": True, "mensagens": [dict(r) for r in rows]}
    finally:
        cur.close()
        conn.close()

@app.post("/api/conversas/{conversa_id}/fechar")
async def fechar_atendimento(conversa_id: int, request: Request):
    try:
        data = await request.json()
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT numero_cliente FROM conversas WHERE id = ?", (conversa_id,))
        row = cur.fetchone()
        numero = row[0] if row else None
        
        cur.execute(
            "UPDATE conversas SET status = ?, fechado_em = CURRENT_TIMESTAMP, usuario_id = ?, observacoes = ? WHERE id = ?",
            ("fechado", data.get("usuario_id"), data.get("observacoes", ""), conversa_id)
        )
        conn.commit()
        
        if numero:
            enviar_mensagem_zapi(numero, "Obrigado por entrar em contato! Atendimento encerrado. 😊")
        
        cur.close()
        conn.close()
        return {"sucesso": True}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

# ============================================
# CONTATOS
# ============================================

@app.get("/api/contatos")
async def listar_contatos():
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "contatos": []}
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM contatos ORDER BY nome ASC")
        rows = cur.fetchall()
        return {"sucesso": True, "contatos": [dict(r) for r in rows], "total": len(rows)}
    finally:
        cur.close()
        conn.close()

@app.post("/api/contatos")
async def criar_contato(request: Request):
    try:
        data = await request.json()
        if not data.get("nome") or not data.get("numero"):
            return JSONResponse({"sucesso": False, "erro": "Nome e número obrigatórios"}, status_code=400)
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO contatos (nome, numero, email, tags, observacoes) VALUES (?, ?, ?, ?, ?)",
                (data["nome"], data["numero"], data.get("email", ""), data.get("tags", ""), data.get("observacoes", ""))
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

@app.delete("/api/contatos/{contato_id}")
async def deletar_contato(contato_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM contatos WHERE id = ?", (contato_id,))
        conn.commit()
        return {"sucesso": True}
    finally:
        cur.close()
        conn.close()

@app.post("/api/contatos/importar")
async def importar_contatos_csv(file: UploadFile = File(...)):
    try:
        content = await file.read()
        text = content.decode('utf-8')
        reader = csv.DictReader(io.StringIO(text))
        
        conn = get_db_connection()
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

# ============================================
# UPLOAD DE IMAGEM
# ============================================

@app.post("/api/upload/imagem")
async def upload_imagem(file: UploadFile = File(...)):
    # Faz upload de imagem pra usar em campanhas
    try:
        # Valida extensão
        ext = file.filename.split(".")[-1].lower()
        if ext not in ["jpg", "jpeg", "png", "webp"]:
            return JSONResponse({"sucesso": False, "erro": "Formato inválido. Use JPG, PNG ou WEBP"}, status_code=400)
        
        # Gera nome único
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        nome_arquivo = f"campanha_{timestamp}.{ext}"
        caminho = os.path.join(UPLOAD_DIR, nome_arquivo)
        
        # Salva
        content = await file.read()
        with open(caminho, "wb") as f:
            f.write(content)
        
        # Monta URL pública
        url = f"/uploads/{nome_arquivo}"
        
        return {"sucesso": True, "url": url, "nome": nome_arquivo}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

# ============================================
# TEMPLATES
# ============================================

@app.get("/api/templates")
async def listar_templates():
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "templates": []}
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM templates ORDER BY nome ASC")
        rows = cur.fetchall()
        return {"sucesso": True, "templates": [dict(r) for r in rows]}
    finally:
        cur.close()
        conn.close()

@app.post("/api/templates")
async def criar_template(request: Request):
    try:
        data = await request.json()
        if not data.get("nome") or not data.get("conteudo"):
            return JSONResponse({"sucesso": False, "erro": "Dados incompletos"}, status_code=400)
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO templates (nome, conteudo, categoria) VALUES (?, ?, ?)",
                    (data["nome"], data["conteudo"], data.get("categoria", "geral")))
        conn.commit()
        cur.close()
        conn.close()
        return {"sucesso": True}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.delete("/api/templates/{template_id}")
async def deletar_template(template_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        conn.commit()
        return {"sucesso": True}
    finally:
        cur.close()
        conn.close()

# ============================================
# CAMPANHAS
# ============================================

@app.get("/api/campanhas")
async def listar_campanhas():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM campanhas ORDER BY criado_em DESC")
        rows = cur.fetchall()
        return {"sucesso": True, "campanhas": [dict(r) for r in rows]}
    finally:
        cur.close()
        conn.close()

@app.post("/api/campanhas")
async def criar_campanha(request: Request):
    try:
        data = await request.json()
        nome = data.get("nome")
        mensagem = data.get("mensagem")
        template_id = data.get("template_id")
        contato_ids = data.get("contato_ids", [])
        imagem_url = data.get("imagem_url")  # NOVO!
        
        if not nome or not mensagem or not contato_ids:
            return JSONResponse({"sucesso": False, "erro": "Dados incompletos"}, status_code=400)
        
        config = obter_config_antiban()
        enviados_hoje = obter_envios_hoje()
        
        if config and config['ativo']:
            disponivel = config['limite_diario'] - enviados_hoje
            if disponivel <= 0:
                return JSONResponse({"sucesso": False, "erro": f"Limite diário atingido"}, status_code=400)
            if len(contato_ids) > disponivel:
                return JSONResponse({"sucesso": False, "erro": f"Só pode enviar mais {disponivel} hoje"}, status_code=400)
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            "INSERT INTO campanhas (nome, template_id, mensagem, imagem_url, total_contatos, status) VALUES (?, ?, ?, ?, ?, ?)",
            (nome, template_id, mensagem, imagem_url, len(contato_ids), "pendente")
        )
        campanha_id = cur.lastrowid
        
        for contato_id in contato_ids:
            cur.execute("SELECT numero FROM contatos WHERE id = ?", (contato_id,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO envios (campanha_id, contato_id, numero, status) VALUES (?, ?, ?, ?)",
                    (campanha_id, contato_id, row[0], "pendente")
                )
        
        conn.commit()
        cur.close()
        conn.close()
        
        asyncio.create_task(processar_campanha(campanha_id))
        
        return {"sucesso": True, "campanha_id": campanha_id}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

async def processar_campanha(campanha_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("UPDATE campanhas SET status = ?, iniciado_em = CURRENT_TIMESTAMP WHERE id = ?", ("enviando", campanha_id))
        conn.commit()
        
        cur.execute("SELECT mensagem, imagem_url FROM campanhas WHERE id = ?", (campanha_id,))
        campanha = cur.fetchone()
        if not campanha:
            return
        
        mensagem_base = campanha[0]
        imagem_url = campanha[1]
        config = obter_config_antiban()
        
        # Se tem imagem, monta URL completa
        imagem_completa = None
        if imagem_url:
            base_url = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
            if base_url:
                imagem_completa = f"https://{base_url}{imagem_url}"
            else:
                # Fallback: usa hardcoded
                imagem_completa = f"https://web-production-69fb05.up.railway.app{imagem_url}"
        
        cur.execute("""
            SELECT e.id, e.contato_id, e.numero, c.nome 
            FROM envios e LEFT JOIN contatos c ON e.contato_id = c.id
            WHERE e.campanha_id = ? AND e.status = 'pendente'
        """, (campanha_id,))
        
        envios = cur.fetchall()
        enviadas = 0
        falhadas = 0
        
        for idx, envio in enumerate(envios):
            envio_id, contato_id, numero, nome = envio
            
            pode_enviar, motivo = verificar_pode_enviar()
            if not pode_enviar:
                cur.execute("UPDATE envios SET status = ?, resposta = ? WHERE id = ?", ("pausado", motivo, envio_id))
                conn.commit()
                continue
            
            if config and idx > 0 and idx % config['pausa_a_cada'] == 0:
                await asyncio.sleep(config['pausa_segundos'])
            
            mensagem_final = mensagem_base.replace("{nome}", nome or "").replace("{numero}", numero or "")
            
            # Envia imagem com legenda ou só texto
            if imagem_completa:
                resultado = enviar_imagem_zapi(numero, imagem_completa, mensagem_final)
            else:
                resultado = enviar_mensagem_zapi(numero, mensagem_final)
            
            if resultado.get("sucesso"):
                cur.execute("UPDATE envios SET status = ?, enviado_em = CURRENT_TIMESTAMP WHERE id = ?", ("enviado", envio_id))
                enviadas += 1
                incrementar_envio_diario()
            else:
                cur.execute("UPDATE envios SET status = ?, resposta = ? WHERE id = ?", ("falhou", json.dumps(resultado), envio_id))
                falhadas += 1
            
            cur.execute("UPDATE campanhas SET enviadas = ?, falhadas = ? WHERE id = ?", (enviadas, falhadas, campanha_id))
            conn.commit()
            
            delay = random.uniform(config['delay_min'], config['delay_max']) if config else 4
            await asyncio.sleep(delay)
        
        cur.execute("UPDATE campanhas SET status = ?, finalizado_em = CURRENT_TIMESTAMP WHERE id = ?", ("finalizada", campanha_id))
        conn.commit()
        
    except Exception as e:
        logger.error(f"Erro campanha: {e}")
        cur.execute("UPDATE campanhas SET status = ? WHERE id = ?", ("erro", campanha_id))
        conn.commit()
    finally:
        cur.close()
        conn.close()

@app.get("/api/campanhas/{campanha_id}/envios")
async def envios_campanha(campanha_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT e.*, c.nome as contato_nome 
            FROM envios e LEFT JOIN contatos c ON e.contato_id = c.id
            WHERE e.campanha_id = ? ORDER BY e.id ASC
        """, (campanha_id,))
        rows = cur.fetchall()
        return {"sucesso": True, "envios": [dict(r) for r in rows]}
    finally:
        cur.close()
        conn.close()

# ============================================
# ANTI-BAN CONFIG
# ============================================

@app.get("/api/antiban/config")
async def get_config_antiban():
    config = obter_config_antiban()
    enviados_hoje = obter_envios_hoje()
    return {
        "sucesso": True,
        "config": config,
        "envios_hoje": enviados_hoje,
        "disponivel_hoje": (config['limite_diario'] - enviados_hoje) if config else 0
    }

@app.post("/api/antiban/config")
async def salvar_config_antiban(request: Request):
    try:
        data = await request.json()
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE config_antiban SET
                limite_diario = ?, limite_por_hora = ?,
                delay_min = ?, delay_max = ?,
                pausa_a_cada = ?, pausa_segundos = ?,
                horario_inicio = ?, horario_fim = ?, ativo = ?
            WHERE id = 1
        """, (
            data.get("limite_diario", 100),
            data.get("limite_por_hora", 30),
            data.get("delay_min", 3),
            data.get("delay_max", 7),
            data.get("pausa_a_cada", 30),
            data.get("pausa_segundos", 60),
            data.get("horario_inicio", "08:00"),
            data.get("horario_fim", "20:00"),
            1 if data.get("ativo", True) else 0
        ))
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
async def relatorios():
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "relatorios": {}}
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM conversas")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM conversas WHERE status = 'aberto'")
        abertos = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM conversas WHERE status = 'fechado'")
        fechados = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM contatos")
        contatos = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM campanhas")
        campanhas = cur.fetchone()[0]
        cur.execute("SELECT SUM(enviadas) FROM campanhas")
        enviadas = cur.fetchone()[0] or 0
        
        cur.execute("SELECT AVG((julianday(fechado_em) - julianday(criado_em)) * 24 * 60) FROM conversas WHERE status = 'fechado'")
        tempo = cur.fetchone()
        tempo_medio = int(tempo[0] or 0) if tempo[0] else 0
        
        return {
            "sucesso": True,
            "relatorios": {
                "totalConversas": total, "abertos": abertos, "fechados": fechados,
                "tempoMedioMinutos": tempo_medio, "totalContatos": contatos,
                "totalCampanhas": campanhas, "totalEnviadas": enviadas,
                "enviadosHoje": obter_envios_hoje()
            }
        }
    finally:
        cur.close()
        conn.close()

@app.get("/api/zapi/status")
async def api_status_zapi():
    return status_zapi()

if __name__ == "__main__":
    print("\n" + "="*60)
    print("EVA Hotel Backend v4.0 - Iniciando")
    print("="*60)
    init_db()
    print(f"URL: http://localhost:{PORT}")
    print(f"Painel: http://localhost:{PORT}/painel")
    print("="*60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
