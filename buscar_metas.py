"""Busca as metas que estão na Central (tela Metas) para a EVA usar no resumo do WhatsApp.

Usa as mesmas variáveis do envio: CENTRAL_URL e CENTRAL_INGEST_SECRET. Só lê, nunca altera nada.
"""
from __future__ import annotations

import os
from typing import Any

import requests


def buscar_metas(ano: int, mes: int | None = None, timeout: int = 30) -> dict[str, dict[str, dict[str, Any]]]:
    """Devolve {chave_do_hotel: {"2026-10": {meta_receita, meta_diaria_media, meta_ocupacao_pct, ...}}}."""
    base = os.environ["CENTRAL_URL"].rstrip("/")
    segredo = os.environ["CENTRAL_INGEST_SECRET"]
    params: dict[str, Any] = {"ano": ano}
    if mes:
        params["mes"] = mes
    resposta = requests.get(f"{base}/api/metas-eva", params=params, headers={"Authorization": f"Bearer {segredo}"}, timeout=timeout)
    resposta.raise_for_status()
    saida: dict[str, dict[str, dict[str, Any]]] = {}
    for linha in resposta.json()["metas"]:
        saida.setdefault(linha["hits_key"], {})[linha["mes"]] = linha
    return saida


if __name__ == "__main__":
    import datetime as dt

    hoje = dt.date.today()
    for chave, meses in buscar_metas(hoje.year, hoje.month).items():
        for mes, m in meses.items():
            print(chave, mes, m["meta_receita"])
