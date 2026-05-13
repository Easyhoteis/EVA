from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
from datetime import datetime
from dotenv import load_dotenv
import logging
import sqlite3
import json

from twilio.rest import Client as TwilioClient
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY")
PORT = int(os.getenv("PORT", 3000))
NODE_ENV = os.getenv("NODE_ENV", "development")

app = FastAPI(title="EVA Hotel Backend", version="1.0.0")
twilio_client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
claude_client = Anthropic(api_key=CLAUDE_KEY)

# CORS pra aceitar requisições do frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# BANCO DE DADOS (SQLite)
# ============================================

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
    # Cria as tabelas no banco automaticamente
    conn = get_db_connection()
    if not conn:
        logger.warning("BD não disponível")
        return
    
    cur = conn.cursor()
    
    try:
        # Tabela de conversas (chats)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_cliente TEXT NOT NULL,
                numero_hotel TEXT NOT NULL,
                status TEXT DEFAULT 'aberto',
                usuario_id INTEGER,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fechado_em TIMESTAMP,
                observacoes TEXT
            );
        """)
        
        # Tabela de mensagens
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
        
        conn.commit()
        logger.info("BD inicializado com sucesso")
    except Exception as e:
        logger.error(f"Erro ao criar tabelas: {e}")
    finally:
        cur.close()
        conn.close()

# ============================================
# FUNÇÕES PRINCIPAIS
# ============================================

def obter_resposta_ia(mensagem_cliente: str, contexto_hotel: str = "Hotel"):
    # Chama Claude pra responder a mensagem do cliente
    try:
        response = claude_client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=150,
            messages=[
                {
                    "role": "user",
                    "content": f"""Você é um assistente de atendimento ao cliente de um {contexto_hotel}.

Responda de forma breve, amigável e profissional. Máximo 2-3 linhas.

Cliente disse: "{mensagem_cliente}"

Responda:"""
                }
            ]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Erro na IA: {e}")
        return "Desculpe, tive um problema. Tente novamente."

def enviar_mensagem_whatsapp(numero_cliente: str, mensagem: str) -> bool:
    # Envia mensagem via Twilio pra WhatsApp do cliente
    try:
        twilio_client.messages.create(
            from_=f"whatsapp:{TWILIO_PHONE}",
            to=f"whatsapp:{numero_cliente}",
            body=mensagem
        )
        logger.info(f"Mensagem enviada para {numero_cliente}")
        return True
    except Exception as e:
        logger.error(f"Erro ao enviar: {e}")
        return False

def buscar_conversa_aberta(numero_cliente: str):
    # Procura se já tem uma conversa aberta com este cliente
    conn = get_db_connection()
    if not conn:
        return None
    
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM conversas WHERE numero_cliente = ? AND status = ? LIMIT 1",
            (numero_cliente, "aberto")
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()
        conn.close()

def salvar_conversa(numero_cliente: str, numero_hotel: str):
    # Cria uma nova conversa no banco
    conn = get_db_connection()
    if not conn:
        return None
    
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO conversas (numero_cliente, numero_hotel, status) VALUES (?, ?, ?)",
            (numero_cliente, numero_hotel, "aberto")
        )
        conn.commit()
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()

def salvar_mensagem(conversa_id: int, remetente: str, conteudo: str):
    # Salva a mensagem no banco (cliente ou EVA)
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (?, ?, ?)",
            (conversa_id, remetente, conteudo)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

# ============================================
# ROTAS DO BACKEND
# ============================================

@app.get("/")
async def root():
    return {
        "mensagem": "EVA Hotel Backend",
        "status": "OK",
        "versao": "1.0.0"
    }

@app.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request):
    # Webhook que recebe mensagens do Twilio
    try:
        form_data = await request.form()
        numero_cliente = form_data.get("From")
        mensagem_cliente = form_data.get("Body")
        numero_hotel = TWILIO_PHONE
        
        logger.info(f"Mensagem de {numero_cliente}: {mensagem_cliente}")
        
        # Procura conversa aberta
        conversa_id = buscar_conversa_aberta(numero_cliente)
        
        # Se não existe, cria nova
        if not conversa_id:
            conversa_id = salvar_conversa(numero_cliente, numero_hotel)
            logger.info(f"Nova conversa criada: {conversa_id}")
        
        # Salva mensagem do cliente
        salvar_mensagem(conversa_id, "cliente", mensagem_cliente)
        
        # Pega resposta da IA
        resposta_ia = obter_resposta_ia(mensagem_cliente)
        
        # Salva resposta no banco
        salvar_mensagem(conversa_id, "eva", resposta_ia)
        
        # Envia resposta pro cliente
        enviar_mensagem_whatsapp(numero_cliente, resposta_ia)
        
        return JSONResponse({"success": True, "conversa_id": conversa_id})
    
    except Exception as e:
        logger.error(f"Erro no webhook: {e}")
        return JSONResponse({"success": False, "erro": str(e)}, status_code=500)

@app.get("/api/conversas/abertas")
async def conversas_abertas():
    # Retorna todas as conversas abertas
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "conversas": [], "total": 0}
    
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 
                c.id,
                c.numero_cliente,
                c.criado_em,
                (SELECT COUNT(*) FROM mensagens WHERE conversa_id = c.id) as total_mensagens,
                (SELECT conteudo FROM mensagens WHERE conversa_id = c.id ORDER BY criado_em DESC LIMIT 1) as ultima_mensagem
            FROM conversas c
            WHERE c.status = 'aberto'
            ORDER BY c.criado_em DESC
        """)
        rows = cur.fetchall()
        conversas = [dict(row) for row in rows]
        return {"sucesso": True, "conversas": conversas, "total": len(conversas)}
    finally:
        cur.close()
        conn.close()

@app.get("/api/conversas/fechadas")
async def conversas_fechadas():
    # Retorna todas as conversas fechadas
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "conversas": [], "total": 0}
    
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 
                c.id,
                c.numero_cliente,
                c.criado_em,
                c.fechado_em,
                (SELECT COUNT(*) FROM mensagens WHERE conversa_id = c.id) as total_mensagens
            FROM conversas c
            WHERE c.status = 'fechado'
            ORDER BY c.fechado_em DESC
        """)
        rows = cur.fetchall()
        conversas = [dict(row) for row in rows]
        return {"sucesso": True, "conversas": conversas, "total": len(conversas)}
    finally:
        cur.close()
        conn.close()

@app.get("/api/conversas/{conversa_id}/mensagens")
async def mensagens_conversa(conversa_id: int):
    # Retorna todas as mensagens de uma conversa específica
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "mensagens": []}
    
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM mensagens WHERE conversa_id = ? ORDER BY criado_em ASC",
            (conversa_id,)
        )
        rows = cur.fetchall()
        mensagens = [dict(row) for row in rows]
        return {"sucesso": True, "mensagens": mensagens}
    finally:
        cur.close()
        conn.close()

@app.post("/api/conversas/{conversa_id}/fechar")
async def fechar_atendimento(conversa_id: int, request: Request):
    # Fecha um atendimento
    try:
        data = await request.json()
        observacoes = data.get("observacoes", "")
        usuario_id = data.get("usuario_id")
        
        conn = get_db_connection()
        if not conn:
            return JSONResponse({"sucesso": False, "erro": "BD indisponível"}, status_code=500)
        
        cur = conn.cursor()
        
        # Busca número do cliente
        cur.execute("SELECT numero_cliente FROM conversas WHERE id = ?", (conversa_id,))
        row = cur.fetchone()
        numero_cliente = row[0] if row else None
        
        # Marca como fechado no banco
        cur.execute(
            "UPDATE conversas SET status = ?, fechado_em = CURRENT_TIMESTAMP, usuario_id = ? WHERE id = ?",
            ("fechado", usuario_id, conversa_id)
        )
        conn.commit()
        
        # Envia mensagem final pro cliente
        if numero_cliente:
            enviar_mensagem_whatsapp(
                numero_cliente,
                "Obrigado por entrar em contato! Seu atendimento foi encerrado. 😊"
            )
        
        cur.close()
        conn.close()
        
        return JSONResponse({"sucesso": True, "mensagem": "Atendimento fechado"})
    
    except Exception as e:
        logger.error(f"Erro ao fechar: {e}")
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.get("/api/relatorios")
async def relatorios():
    # Retorna estatísticas das conversas
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "relatorios": {}}
    
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) as total FROM conversas")
        total = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) as total FROM conversas WHERE status = ?", ("aberto",))
        abertos = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) as total FROM conversas WHERE status = ?", ("fechado",))
        fechados = cur.fetchone()[0]
        
        # Calcula tempo médio (em minutos)
        cur.execute("""
            SELECT AVG((julianday(fechado_em) - julianday(criado_em)) * 24 * 60) as minutos_medio
            FROM conversas WHERE status = 'fechado'
        """)
        tempo_row = cur.fetchone()
        tempo_medio = int(tempo_row[0] or 0) if tempo_row[0] else 0
        
        return {
            "sucesso": True,
            "relatorios": {
                "totalConversas": total,
                "abertos": abertos,
                "fechados": fechados,
                "tempoMedioMinutos": tempo_medio
            }
        }
    finally:
        cur.close()
        conn.close()

@app.get("/painel")
async def painel():
    # Serve o arquivo HTML do painel
    return FileResponse("painel.html", media_type="text/html")

# ============================================
# INICIAR
# ============================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print("EVA Hotel Backend - Iniciando")
    print("="*60)
    
    init_db()
    
    print(f"URL: http://localhost:{PORT}")
    print(f"Painel: http://localhost:{PORT}/painel")
    print(f"Webhook: http://localhost:{PORT}/webhook/whatsapp")
    print("="*60 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=PORT)
