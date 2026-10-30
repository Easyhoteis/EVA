"""Busca as metas que estão na Central (tela Metas) para a EVA usar no resumo do WhatsApp.

Usa duas variáveis do Heroku: CENTRAL_URL e CENTRAL_INGEST_SECRET. Só lê, nunca altera nada.
Se a Central estiver fora do ar ou não tiver a meta do hotel, devolve None e a EVA usa a meta antiga do banco dela.
"""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Any

import requests

_cache: dict[tuple[int, int], list[dict[str, Any]] | None] = {}


def _norm(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


def _palavras(texto: str) -> frozenset[str]:
    sem_acento = unicodedata.normalize("NFD", texto or "").encode("ascii", "ignore").decode().lower()
    return frozenset(p for p in re.split(r"[^a-z0-9]+", sem_acento) if p and p not in {"hotel", "hoteis", "de", "do", "da"})


def buscar_metas(ano: int, mes: int | None = None, timeout: int = 30) -> list[dict[str, Any]]:
    """Lista de metas da Central: hits_key, hotel, mes (AAAA-MM), meta_receita, meta_receita_hotel, meta_diaria_media, meta_ocupacao_pct."""
    base = os.environ["CENTRAL_URL"].rstrip("/")
    segredo = os.environ["CENTRAL_INGEST_SECRET"]
    params: dict[str, Any] = {"ano": ano}
    if mes:
        params["mes"] = mes
    r = requests.get(f"{base}/api/metas-eva", params=params, headers={"Authorization": f"Bearer {segredo}"}, timeout=timeout)
    r.raise_for_status()
    return r.json()["metas"]


def meta_central_do_hotel(nome: str, dominio: str | None, ano: int, mes: int) -> dict[str, float | None] | None:
    """Meta do hotel no mês, no mesmo formato que a EVA já usa (ocupação como fração). None se não achar."""
    chave = (ano, mes)
    if chave not in _cache:
        try:
            _cache[chave] = buscar_metas(ano, mes)
        except Exception as erro:  # nunca derruba o robô
            print(f"  (metas da Central indisponíveis: {erro})")
            _cache[chave] = None
    lista = _cache[chave]
    if not lista:
        return None

    sub = _norm((dominio or "").split(".")[0].replace("https://", "").replace("http://", ""))
    n_nome, p_nome = _norm(nome), _palavras(nome)
    achada = None
    for m in lista:
        if sub and _norm(m["hits_key"]) == sub:
            achada = m
            break
    if achada is None:
        for m in lista:
            if _norm(m["hotel"]) == n_nome or _norm(m["hits_key"]) == n_nome or (p_nome and _palavras(m["hotel"]) == p_nome):
                achada = m
                break
    if achada is None or not achada.get("meta_receita"):
        return None
    occ = achada.get("meta_ocupacao_pct")
    return {
        "receita": achada["meta_receita"],
        "receita_hotel": achada.get("meta_receita_hotel"),
        "dm": achada.get("meta_diaria_media"),
        "occ": (occ / 100) if occ is not None else None,
    }


if __name__ == "__main__":
    import datetime as dt

    hoje = dt.date.today()
    for m in buscar_metas(hoje.year, hoje.month):
        print(m["hits_key"], "|", m["hotel"], "|", m["mes"], "|", m["meta_receita"])
