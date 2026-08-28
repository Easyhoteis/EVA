# ==============================================================================
# enviar_status_diario.py
# Roda 1x por dia (via Heroku Scheduler, agendado pra ~8h): busca a receita do
# mês de todos os hotéis, compara com a meta cadastrada, e manda um resumo
# único no grupo do WhatsApp da EVA — destacando quem bateu a meta.
#
# Variáveis de ambiente necessárias (já devem existir no Heroku da EVA):
#   DATABASE_URL         -> conexão do Postgres
#   ZAPI_INSTANCE_ID      -> instance id da Z-API
#   ZAPI_TOKEN             -> token da Z-API
#   ZAPI_CLIENT_TOKEN       -> client-token da Z-API
#   ZAPI_GRUPO_ID            -> id do grupo de notificações (ex: ...-group)
#
# Rodar com:  python enviar_status_diario.py
# ==============================================================================
import os
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import requests

from hits_api import HOTEIS_HITS, obter_token, obter_property_code, buscar_relatorio, resumo_mensal, tem_dados_relevantes

DATABASE_URL = os.environ.get("DATABASE_URL")
ZAPI_INSTANCE_ID = os.environ.get("ZAPI_INSTANCE_ID")
ZAPI_TOKEN = os.environ.get("ZAPI_TOKEN")
ZAPI_CLIENT_TOKEN = os.environ.get("ZAPI_CLIENT_TOKEN")
ZAPI_GRUPO_ID = os.environ.get("ZAPI_GRUPO_ID")

HOJE = date.today()
D_INI_MES = date(HOJE.year, HOJE.month, 1)
if HOJE.month == 12:
    D_FIM_MES = date(HOJE.year, 12, 31)
else:
    D_FIM_MES = date(HOJE.year, HOJE.month + 1, 1) - timedelta(days=1)

# mesmo período, mas do ano passado (pra comparação)
D_INI_MES_ANT = date(HOJE.year - 1, HOJE.month, 1)
if HOJE.month == 12:
    D_FIM_MES_ANT = date(HOJE.year - 1, 12, 31)
else:
    D_FIM_MES_ANT = date(HOJE.year - 1, HOJE.month + 1, 1) - timedelta(days=1)

_cache_metas = {}  # preenchido durante o loop principal, usado pelo gerar_html_relatorio


def fmt_moeda(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def buscar_dados_mes(nome, dominio):
    try:
        token = obter_token(dominio)
        property_code = obter_property_code(dominio, token, nome)

        dados_at = buscar_relatorio(dominio, token, D_INI_MES, D_FIM_MES, property_code)
        meses_at = resumo_mensal(dados_at)
        receita = sum(m["receita"] for m in meses_at)
        dias_totais = sum(m["dias"] for m in meses_at) or 1
        occ = sum(m["ocupacao"] * m["dias"] for m in meses_at) / dias_totais / 100
        diarias_validas = [m["diaria_media"] for m in meses_at if m["diaria_media"] > 0]
        dm = sum(diarias_validas) / len(diarias_validas) if diarias_validas else 0.0

        # mesmo período do ano passado, pra comparação
        receita_ant = dm_ant = occ_ant = None
        tem_ano_anterior = False
        try:
            dados_an = buscar_relatorio(dominio, token, D_INI_MES_ANT, D_FIM_MES_ANT, property_code)
            meses_an = resumo_mensal(dados_an)
            tem_ano_anterior = tem_dados_relevantes(meses_an)
            if tem_ano_anterior:
                receita_ant = sum(m["receita"] for m in meses_an)
                dias_ant = sum(m["dias"] for m in meses_an) or 1
                occ_ant = sum(m["ocupacao"] * m["dias"] for m in meses_an) / dias_ant / 100
                diarias_an_validas = [m["diaria_media"] for m in meses_an if m["diaria_media"] > 0]
                dm_ant = sum(diarias_an_validas) / len(diarias_an_validas) if diarias_an_validas else None
        except Exception:
            pass  # se der erro no ano passado, segue só com o ano atual

        return nome, {
            "receita": receita, "dm": dm, "occ": occ,
            "receita_ant": receita_ant, "dm_ant": dm_ant, "occ_ant": occ_ant,
            "tem_ano_anterior": tem_ano_anterior,
        }, None
    except Exception as e:
        return nome, None, str(e)


def buscar_meta(cur, hotel_nome):
    cur.execute(
        """SELECT meta_receita, meta_receita_hotel, meta_dm, meta_occ
           FROM metas_hoteis WHERE hotel_nome=%s AND ano=%s AND mes=%s""",
        (hotel_nome, HOJE.year, HOJE.month)
    )
    linha = cur.fetchone()
    if not linha:
        return None
    return {
        "receita": float(linha[0]) if linha[0] is not None else None,
        "receita_hotel": float(linha[1]) if linha[1] is not None else None,
        "dm": float(linha[2]) if linha[2] is not None else None,
        "occ": float(linha[3]) if linha[3] is not None else None,
    }


def ja_notificado(cur, hotel_nome):
    cur.execute(
        "SELECT 1 FROM metas_notificadas WHERE hotel_nome=%s AND ano=%s AND mes=%s",
        (hotel_nome, HOJE.year, HOJE.month)
    )
    return cur.fetchone() is not None


def marcar_notificado(cur, hotel_nome):
    cur.execute(
        """INSERT INTO metas_notificadas (hotel_nome, ano, mes)
           VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
        (hotel_nome, HOJE.year, HOJE.month)
    )


def gerar_html_relatorio(resultados, mes_nome_completo, linhas_metas_batidas):
    """Monta a página HTML (cards, cores Easy Hotéis) com o resumo do dia."""

    def fmt_pct(v):
        return f"{v*100:.1f}".replace(".", ",") + "%"

    total_erros = sum(1 for _, (dados, erro) in resultados.items() if erro)
    total_acima_meta = 0
    for nome, (dados, erro) in resultados.items():
        if erro:
            continue
        meta_check = _cache_metas.get(nome)
        if meta_check and meta_check.get("receita") and dados["receita"] >= meta_check["receita"]:
            total_acima_meta += 1

    html_celebracao = ""
    if linhas_metas_batidas:
        itens = "".join(f'<div class="item">🏆 {nome} bateu a meta do mês!</div>' for nome in linhas_metas_batidas)
        html_celebracao = f"""
        <div class="celebracao">
            <div class="titulo">🎉 META BATIDA! 🎉</div>
            {itens}
        </div>"""

    cards = ""
    for nome, (dados, erro) in resultados.items():
        if erro:
            cards += f"""
            <div class="hotel-card erro">
                <div class="hotel-nome">🏨 {nome} <span class="tag-erro">Erro</span></div>
                <div class="erro-msg">{erro[:200]}</div>
            </div>"""
            continue

        meta = _cache_metas.get(nome)

        def linha_comparativo_ano(dados=dados):
            if not (dados.get("tem_ano_anterior") and dados.get("receita_ant")):
                return ""
            var_receita = ((dados["receita"] - dados["receita_ant"]) / dados["receita_ant"] * 100) if dados["receita_ant"] else 0
            ok_var = var_receita >= 0
            html = f"""<div class="linha-metrica"><span class="rotulo">{'📈' if ok_var else '📉'} Receita vs. {HOJE.year - 1}</span>
                <span class="valor {'ok' if ok_var else 'baixo'}">{fmt_moeda(dados['receita_ant'])}
                <span class="meta-info">({'+' if ok_var else ''}{var_receita:.0f}%)</span></span></div>"""

            if dados.get("dm_ant"):
                var_dm = ((dados["dm"] - dados["dm_ant"]) / dados["dm_ant"] * 100) if dados["dm_ant"] else 0
                ok_dm_ano = var_dm >= 0
                html += f"""<div class="linha-metrica"><span class="rotulo">{'📈' if ok_dm_ano else '📉'} Diária vs. {HOJE.year - 1}</span>
                    <span class="valor {'ok' if ok_dm_ano else 'baixo'}">{fmt_moeda(dados['dm_ant'])}
                    <span class="meta-info">({'+' if ok_dm_ano else ''}{var_dm:.0f}%)</span></span></div>"""

            if dados.get("occ_ant"):
                var_occ = dados["occ"] - dados["occ_ant"]
                ok_occ_ano = var_occ >= 0
                html += f"""<div class="linha-metrica"><span class="rotulo">{'📈' if ok_occ_ano else '📉'} Ocupação vs. {HOJE.year - 1}</span>
                    <span class="valor {'ok' if ok_occ_ano else 'baixo'}">{fmt_pct(dados['occ_ant'])}
                    <span class="meta-info">({'+' if ok_occ_ano else ''}{var_occ*100:.1f}p.p.)</span></span></div>"""

            return html

        if not meta or not meta.get("receita"):
            cards += f"""
            <div class="hotel-card sem-meta">
                <div class="hotel-nome">🏨 {nome} <span class="tag-sem-meta">sem meta</span></div>
                <div class="linha-metrica"><span class="rotulo">💰 Receita</span><span class="valor">{fmt_moeda(dados['receita'])}</span></div>
                {linha_comparativo_ano()}
            </div>"""
            continue

        pct_receita = (dados["receita"] / meta["receita"] * 100) if meta["receita"] else 0
        classe_pct = "pct-ok" if pct_receita >= 100 else "pct-baixo"
        acima_da_meta_hoje = pct_receita >= 100
        troco = "🏆 " if acima_da_meta_hoje else ""

        linha_meta_hotel = ""
        if meta.get("receita_hotel"):
            pct_hotel = (dados["receita"] / meta["receita_hotel"] * 100) if meta["receita_hotel"] else 0
            linha_meta_hotel = f'<div class="meta-hotel-extra">meta do hotel: {fmt_moeda(meta["receita_hotel"])} — {pct_hotel:.0f}%</div>'

        linha_dm = ""
        if meta.get("dm"):
            ok_dm = dados["dm"] >= meta["dm"]
            linha_dm = f"""<div class="linha-metrica"><span class="rotulo">🛏️ Diária Média</span>
                <span class="valor {'ok' if ok_dm else 'baixo'}">{fmt_moeda(dados['dm'])} {'✅' if ok_dm else '🔻'}
                <span class="meta-info">meta: {fmt_moeda(meta['dm'])}</span></span></div>"""

        linha_occ = ""
        if meta.get("occ"):
            ok_occ = dados["occ"] >= meta["occ"]
            linha_occ = f"""<div class="linha-metrica"><span class="rotulo">📈 Ocupação</span>
                <span class="valor {'ok' if ok_occ else 'baixo'}">{fmt_pct(dados['occ'])} {'✅' if ok_occ else '🔻'}
                <span class="meta-info">meta: {fmt_pct(meta['occ'])}</span></span></div>"""

        cards += f"""
        <div class="hotel-card">
            <div class="hotel-nome">🏨 {nome} {troco}</div>
            <div class="linha-metrica"><span class="rotulo">💰 Receita</span>
                <span class="valor">{fmt_moeda(dados['receita'])} <span class="pct-meta {classe_pct}">{pct_receita:.0f}%</span></span></div>
            {linha_meta_hotel}
            {linha_dm}
            {linha_occ}
            {linha_comparativo_ano()}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Status Diário — {HOJE.strftime('%d/%m/%Y')}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
    --bg:     #0d0d1a;
    --card:   #1a1a2e;
    --border: rgba(255,255,255,0.07);
    --cyan:   #00bcd4;
    --red:    #e53935;
    --orange: #f57c00;
    --green:  #4caf50;
    --white:  #ffffff;
    --gray:   #9ca3af;
    --gray2:  #6b7280;
    --text:   #e2e8f0;
}}
body {{ background: var(--bg); color: var(--text); font-family: 'Inter', sans-serif; font-size: 15px; line-height: 1.6; }}
.header {{ background: linear-gradient(135deg, #0d0d1a 0%, #1a0a1e 40%, #200a0a 100%); padding: 50px 24px 40px; text-align: center; border-bottom: 1px solid var(--border); position: relative; overflow: hidden; }}
.header::before {{ content:''; position:absolute; top:-80px; left:-80px; width:400px; height:400px; background:radial-gradient(circle,rgba(0,188,212,0.12) 0%,transparent 70%); pointer-events:none; }}
.header::after  {{ content:''; position:absolute; bottom:-80px; right:-80px; width:400px; height:400px; background:radial-gradient(circle,rgba(76,175,80,0.12) 0%,transparent 70%); pointer-events:none; }}
.brand {{ font-size:13px; letter-spacing:4px; color:var(--cyan); text-transform:uppercase; margin-bottom:14px; font-weight:500; }}
.header h1 {{ font-size:clamp(22px,4vw,32px); font-weight:700; color:var(--white); margin-bottom:6px; }}
.header h2 {{ font-size:clamp(12px,2vw,14px); font-weight:300; color:var(--gray); letter-spacing:1px; margin-bottom:22px; }}
.badges {{ display:flex; justify-content:center; gap:10px; flex-wrap:wrap; }}
.badge {{ background:rgba(255,255,255,0.06); border:1px solid var(--border); border-radius:20px; padding:6px 16px; font-size:12px; color:var(--gray); }}
.badge b {{ color:var(--white); font-weight:600; }}
.badge.trofeu {{ background:rgba(76,175,80,0.12); border-color:rgba(76,175,80,0.35); }}
.badge.trofeu b {{ color:var(--green); }}
.badge.erro {{ background:rgba(229,57,53,0.12); border-color:rgba(229,57,53,0.35); }}
.badge.erro b {{ color:var(--red); }}
.container {{ max-width:760px; margin:0 auto; padding:28px 18px 50px; }}
.celebracao {{ background:linear-gradient(135deg, rgba(76,175,80,0.1), rgba(0,188,212,0.05)); border:1px solid rgba(76,175,80,0.3); border-radius:14px; padding:22px 26px; margin-bottom:26px; text-align:center; }}
.celebracao .titulo {{ font-size:17px; font-weight:700; color:var(--green); margin-bottom:10px; }}
.celebracao .item {{ font-size:13px; color:var(--text); font-weight:500; padding:4px 0; }}
.hotel-card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:18px 22px; margin-bottom:14px; }}
.hotel-card.erro {{ border-left:3px solid var(--red); }}
.hotel-card.sem-meta {{ opacity:0.75; }}
.hotel-nome {{ font-size:15px; font-weight:700; color:var(--white); margin-bottom:10px; display:flex; align-items:center; gap:8px; }}
.tag-erro {{ font-size:10px; font-weight:700; color:var(--red); background:rgba(229,57,53,0.12); padding:2px 10px; border-radius:10px; text-transform:uppercase; letter-spacing:0.5px; }}
.tag-sem-meta {{ font-size:10px; font-weight:700; color:var(--gray2); background:rgba(255,255,255,0.05); padding:2px 10px; border-radius:10px; text-transform:uppercase; letter-spacing:0.5px; }}
.linha-metrica {{ display:flex; justify-content:space-between; align-items:center; padding:7px 0; border-top:1px solid var(--border); font-size:13px; }}
.linha-metrica:first-of-type {{ border-top:none; }}
.linha-metrica .rotulo {{ color:var(--gray); display:flex; align-items:center; gap:6px; }}
.linha-metrica .valor {{ font-weight:700; color:var(--white); }}
.linha-metrica .meta-info {{ font-size:11px; color:var(--gray2); margin-left:6px; font-weight:400; }}
.ok {{ color:var(--green); }}
.baixo {{ color:var(--orange); }}
.pct-meta {{ font-size:12px; font-weight:700; padding:2px 10px; border-radius:12px; }}
.pct-ok {{ background:rgba(76,175,80,0.15); color:var(--green); }}
.pct-baixo {{ background:rgba(245,124,0,0.15); color:var(--orange); }}
.erro-msg {{ font-size:12px; color:var(--red); background:rgba(229,57,53,0.06); border-radius:8px; padding:8px 12px; margin-top:6px; }}
.meta-hotel-extra {{ font-size:11px; color:var(--gray2); margin-top:2px; }}
.footer {{ text-align:center; font-size:12px; color:var(--gray2); padding-top:20px; }}
.footer span {{ color:var(--cyan); font-weight:600; }}
</style>
</head>
<body>
<div class="header">
    <div class="brand">easy hotéis · www.easyhoteis.com</div>
    <h1>Status Diário</h1>
    <h2>{HOJE.strftime('%d/%m/%Y')} · Mês de {mes_nome_completo} (realizado + previsão até o fim do mês)</h2>
    <div class="badges">
        <div class="badge">🏨 Hotéis: <b>{len(resultados)}</b></div>
        <div class="badge trofeu">🏆 Acima da meta hoje: <b>{total_acima_meta}</b></div>
        <div class="badge">🎉 Novidade de hoje: <b>{len(linhas_metas_batidas)}</b></div>
        <div class="badge erro">⚠️ Erros: <b>{total_erros}</b></div>
    </div>
</div>
<div class="container">
    {html_celebracao}
    {cards}
    <div class="footer">Hits — Painel Multi-Hotéis · <span>easy hotéis</span> · Uso interno e confidencial</div>
</div>
</body>
</html>"""


def enviar_whatsapp(mensagem):
    url = f"https://api.z-api.io/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
    headers = {"Client-Token": ZAPI_CLIENT_TOKEN, "Content-Type": "application/json"}
    payload = {"phone": ZAPI_GRUPO_ID, "message": mensagem}
    r = requests.post(url, json=payload, headers=headers, timeout=20)
    print(f"Envio WhatsApp (texto) -> status {r.status_code} | {r.text[:200]}")


def enviar_documento_whatsapp(conteudo_html, nome_arquivo):
    import base64
    url = f"https://api.z-api.io/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-document/html"
    headers = {"Client-Token": ZAPI_CLIENT_TOKEN, "Content-Type": "application/json"}
    base64_conteudo = base64.b64encode(conteudo_html.encode("utf-8")).decode("utf-8")
    payload = {
        "phone": ZAPI_GRUPO_ID,
        "document": f"data:text/html;base64,{base64_conteudo}",
        "fileName": nome_arquivo,
    }
    r = requests.post(url, json=payload, headers=headers, timeout=30)
    print(f"Envio WhatsApp (HTML) -> status {r.status_code} | {r.text[:200]}")


def texto_comparativo_ano(dados):
    """Monta o trecho de texto comparando receita, DM e OCC com o ano passado."""
    if not (dados.get("tem_ano_anterior") and dados.get("receita_ant")):
        return ""
    texto = ""
    var_receita = ((dados["receita"] - dados["receita_ant"]) / dados["receita_ant"] * 100) if dados["receita_ant"] else 0
    sinal_var = "📈" if var_receita >= 0 else "📉"
    texto += (f"\n   {sinal_var} Receita vs. {HOJE.year - 1}: {fmt_moeda(dados['receita_ant'])} "
              f"({'+' if var_receita >= 0 else ''}{var_receita:.0f}%)")

    if dados.get("dm_ant"):
        var_dm = ((dados["dm"] - dados["dm_ant"]) / dados["dm_ant"] * 100) if dados["dm_ant"] else 0
        sinal_dm_ano = "📈" if var_dm >= 0 else "📉"
        texto += (f"\n   {sinal_dm_ano} Diária vs. {HOJE.year - 1}: {fmt_moeda(dados['dm_ant'])} "
                  f"({'+' if var_dm >= 0 else ''}{var_dm:.0f}%)")

    if dados.get("occ_ant"):
        var_occ = dados["occ"] - dados["occ_ant"]
        sinal_occ_ano = "📈" if var_occ >= 0 else "📉"
        texto += (f"\n   {sinal_occ_ano} Ocupação vs. {HOJE.year - 1}: {dados['occ_ant']*100:.1f}% "
                  f"({'+' if var_occ >= 0 else ''}{var_occ*100:.1f} p.p.)")

    return texto


def main():
    faltando = [v for v in ["DATABASE_URL", "ZAPI_INSTANCE_ID", "ZAPI_TOKEN", "ZAPI_CLIENT_TOKEN", "ZAPI_GRUPO_ID"]
                if not os.environ.get(v)]
    if faltando:
        print(f"⚠️ Faltam variáveis de ambiente: {faltando}")
        return

    print("Buscando receita, diária média e ocupação do mês de todos os hotéis...")
    resultados = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futuros = [executor.submit(buscar_dados_mes, nome, dominio) for nome, dominio in HOTEIS_HITS.items()]
        for futuro in futuros:
            nome, dados, erro = futuro.result()
            resultados[nome] = (dados, erro)
            if erro:
                print(f"  {nome}: erro - {erro}")
            else:
                print(f"  {nome}: {fmt_moeda(dados['receita'])} | DM {fmt_moeda(dados['dm'])} | OCC {dados['occ']*100:.1f}%")

    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cur = conn.cursor()

    linhas_resumo = []
    linhas_metas_batidas = []

    for nome, (dados, erro) in resultados.items():
        if erro:
            linhas_resumo.append(f"⚠️ *{nome}*: erro ao buscar dados\n   ({erro[:200]})")
            continue

        meta = buscar_meta(cur, nome)
        _cache_metas[nome] = meta  # guarda pra reaproveitar no HTML
        if not meta:
            linha_sem_meta = f"🏨 *{nome}*: {fmt_moeda(dados['receita'])} (sem meta cadastrada)"
            linha_sem_meta += texto_comparativo_ano(dados)
            linhas_resumo.append(linha_sem_meta)
            continue

        # linha de receita
        pct_receita = (dados["receita"] / meta["receita"] * 100) if meta["receita"] else 0
        linha = f"🏨 *{nome}*\n   💰 Receita: {fmt_moeda(dados['receita'])} ({pct_receita:.0f}% da meta de {fmt_moeda(meta['receita'])})"
        if meta.get("receita_hotel"):
            pct_hotel = (dados["receita"] / meta["receita_hotel"] * 100) if meta["receita_hotel"] else 0
            linha += f"\n   (meta do hotel: {fmt_moeda(meta['receita_hotel'])} — {pct_hotel:.0f}%)"

        # linha de DM
        if meta.get("dm"):
            diff_dm = dados["dm"] - meta["dm"]
            sinal_dm = "✅" if diff_dm >= 0 else "🔻"
            linha += f"\n   🛏️ Diária Média: {fmt_moeda(dados['dm'])} {sinal_dm} (meta: {fmt_moeda(meta['dm'])})"

        # linha de OCC
        if meta.get("occ"):
            diff_occ = dados["occ"] - meta["occ"]
            sinal_occ = "✅" if diff_occ >= 0 else "🔻"
            linha += f"\n   📈 Ocupação: {dados['occ']*100:.1f}% {sinal_occ} (meta: {meta['occ']*100:.1f}%)"

        # comparação com o mesmo período do ano passado (receita, DM e OCC)
        linha += texto_comparativo_ano(dados)

        linhas_resumo.append(linha)

        if meta["receita"] and dados["receita"] >= meta["receita"] and not ja_notificado(cur, nome):
            linhas_metas_batidas.append(nome)
            marcar_notificado(cur, nome)

    conn.commit()
    cur.close()
    conn.close()

    mes_nome = D_INI_MES.strftime("%B/%Y")
    mensagem = f"📊 *Status Diário — {HOJE.strftime('%d/%m/%Y')}*\n"
    mensagem += f"Mês de {mes_nome} (realizado + previsão até o fim do mês):\n\n"
    mensagem += "\n\n".join(linhas_resumo)

    if linhas_metas_batidas:
        mensagem += "\n\n🎉🎉 *META BATIDA!* 🎉🎉\n"
        for nome in linhas_metas_batidas:
            mensagem += f"🏆 {nome} bateu a meta do mês!\n"

    print("\n--- MENSAGEM ---")
    print(mensagem)
    print("----------------\n")

    enviar_whatsapp(mensagem)

    # logo em seguida, manda o arquivo HTML bonito
    html_relatorio = gerar_html_relatorio(resultados, mes_nome, linhas_metas_batidas)
    nome_arquivo = f"status-diario-{HOJE.strftime('%Y-%m-%d')}.html"
    enviar_documento_whatsapp(html_relatorio, nome_arquivo)


if __name__ == "__main__":
    main()
