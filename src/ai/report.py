"""AI-powered forensic report generation."""
from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    from .openrouter import OpenRouterClient
except ImportError:
    from openrouter import OpenRouterClient

SYSTEM_PROMPT = textwrap.dedent("""\
    Você é um perito forense digital especializado em análise de registros do Windows.
    Gere laudos periciais completos, formais e técnicos em português do Brasil.

    Estrutura obrigatória do laudo:
    # LAUDO PERICIAL DIGITAL — ANÁLISE DE REGISTRO DO WINDOWS

    ## 1. IDENTIFICAÇÃO
    ## 2. OBJETIVO
    ## 3. METODOLOGIA
    ## 4. MATERIAL EXAMINADO
    ## 5. ANÁLISE TÉCNICA
    ### 5.1 Estrutura do Hive
    ### 5.2 Chaves e Valores Identificados
    ### 5.3 Indicadores de Comprometimento (IoC)
    ### 5.4 Análise de Timeline
    ### 5.5 Persistência e Autostart
    ### 5.6 Artefatos Deletados (se houver)
    ## 6. CONCLUSÕES
    ## 7. RECOMENDAÇÕES
    ## 8. ASSINATURA DO PERITO

    Use tabelas markdown onde adequado. Seja técnico, objetivo e formal.
    Destaque em **negrito** achados críticos e suspeitos.
    Inclua `caminhos de registro` em code inline.
""")


def _trunc(s: str, n: int = 800) -> str:
    return s if len(s) <= n else s[:n] + f"\n... [{len(s) - n} chars omitted]"


def _fmt_value(v: dict) -> str:
    val = str(v.get("value", ""))
    if len(val) > 120:
        val = val[:120] + "…"
    return f"| `{v.get('name', '(Default)') or '(Default)'}` | {v.get('type','')} | {val} |"


def build_context(
    hive_path: str,
    hive_info: dict,
    ls_data: dict,
    recovery: dict | None = None,
    extra_paths: list[dict] | None = None,
) -> str:
    """Build a rich context string for the report prompt."""
    key    = ls_data.get("key", {})
    subs   = ls_data.get("subkeys", [])
    vals   = ls_data.get("values", [])
    lines  = [
        f"ARQUIVO: {hive_path}",
        f"VERSÃO DO HIVE: {hive_info.get('version','?')}",
        f"ÚLTIMA MODIFICAÇÃO GLOBAL: {hive_info.get('timestamp','?')}",
        f"TAMANHO DO HIVE: {hive_info.get('hive_size',0):,} bytes",
        "",
        f"CHAVE ANALISADA: {key.get('path','?')}",
        f"ÚLTIMA ESCRITA: {key.get('timestamp','?')}",
        f"SUBCHAVES: {key.get('num_subkeys',0)}",
        f"VALORES: {key.get('num_values',0)}",
        "",
    ]

    if vals:
        lines += [
            "VALORES DA CHAVE:",
            "| Nome | Tipo | Dado |",
            "|------|------|------|",
        ]
        for v in vals[:50]:
            lines.append(_fmt_value(v))
        if len(vals) > 50:
            lines.append(f"... ({len(vals)-50} valores omitidos)")
        lines.append("")

    if subs:
        lines.append(f"SUBCHAVES DIRETAS ({len(subs)}):")
        for s in subs[:30]:
            lines.append(
                f"  [{s.get('name','')}]  última escrita: {s.get('timestamp','')}  "
                f"({s.get('num_subkeys',0)} subs, {s.get('num_values',0)} vals)"
            )
        if len(subs) > 30:
            lines.append(f"  ... ({len(subs)-30} omitidas)")
        lines.append("")

    if recovery:
        dk = recovery.get("deleted_keys", [])
        dv = recovery.get("deleted_values", [])
        if dk or dv:
            lines += [
                f"ARTEFATOS DELETADOS RECUPERADOS:",
                f"  Chaves deletadas: {len(dk)}",
                f"  Valores deletados: {len(dv)}",
            ]
            for rk in dk[:10]:
                k2 = rk.get("key", {})
                lines.append(f"  - [{k2.get('name','')}]  ts={k2.get('timestamp','')}")
            for rv in dv[:10]:
                v2 = rv.get("value", {})
                lines.append(f"  - `{v2.get('name','')}` = {str(v2.get('value',''))[:80]}")
            lines.append("")

    if extra_paths:
        lines.append("OUTRAS CHAVES DE INTERESSE:")
        for ep in extra_paths[:5]:
            k2 = ep.get("key", {})
            lines.append(f"  {k2.get('path','?')}  ts={k2.get('timestamp','?')}")
        lines.append("")

    return "\n".join(lines)


def generate_report(
    client: OpenRouterClient,
    context: str,
    perito_name: str = "Perito Não Identificado",
    *,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    """Generate a full forensic report. Returns markdown string."""
    now     = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    user_msg = (
        f"Gere um laudo pericial completo com base nos dados abaixo.\n"
        f"Nome do perito: {perito_name}\n"
        f"Data/Hora: {now}\n\n"
        f"```\n{_trunc(context, 8000)}\n```"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_msg},
    ]
    return client.complete(messages, max_tokens=4096, on_chunk=on_chunk)


def save_pdf(markdown_text: str, output_path: str) -> str:
    """Convert markdown report to PDF using fpdf2."""
    from fpdf import FPDF
    import re

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_margins(20, 20, 20)

    # Try to load a Unicode font; fall back to built-in
    _add_unicode_font(pdf)

    line_height = 6

    def clean(s: str) -> str:
        # Remove markdown formatting for plain PDF rendering
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        s = re.sub(r"\*(.+?)\*",     r"\1", s)
        s = re.sub(r"`(.+?)`",       r"\1", s)
        return s

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()

        if line.startswith("# ") and not line.startswith("## "):
            pdf.set_font("Helvetica", "B", 16)
            pdf.set_text_color(0, 120, 200)
            pdf.multi_cell(0, 10, clean(line[2:]))
            pdf.ln(3)
        elif line.startswith("## "):
            pdf.set_font("Helvetica", "B", 13)
            pdf.set_text_color(0, 90, 160)
            pdf.ln(4)
            pdf.multi_cell(0, 8, clean(line[3:]))
            pdf.set_draw_color(0, 90, 160)
            pdf.set_line_width(0.3)
            pdf.line(pdf.get_x(), pdf.get_y(), pdf.w - 20, pdf.get_y())
            pdf.ln(2)
        elif line.startswith("### "):
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(40, 40, 80)
            pdf.ln(2)
            pdf.multi_cell(0, 7, clean(line[4:]))
        elif line.startswith("|"):
            # Table row
            pdf.set_font("Courier", "", 8)
            pdf.set_text_color(60, 60, 60)
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c.replace("-","").replace(":","").replace(" ","")) == set()
                   for c in cells if c):
                # Separator row — skip
                continue
            col_w = max(1, (pdf.w - 40) / max(1, len(cells)))
            for cell in cells:
                txt = clean(cell)
                if len(txt) > 40:
                    txt = txt[:37] + "…"
                pdf.cell(col_w, line_height, txt, border=1, ln=0)
            pdf.ln(line_height)
        elif line.startswith("- ") or line.startswith("* "):
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(30, 30, 30)
            pdf.cell(8, line_height, "•")
            pdf.multi_cell(0, line_height, clean(line[2:]))
        elif line == "":
            pdf.ln(3)
        else:
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(30, 30, 30)
            pdf.multi_cell(0, line_height, clean(line))

    pdf.output(str(output))
    return str(output)


def _add_unicode_font(pdf) -> None:
    """Try to register a Unicode-capable font; fall back silently."""
    import os, sys
    candidates = [
        # Windows system fonts
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\times.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                pdf.add_font("Unicode", "", path, uni=True)
                pdf.set_font("Unicode", "", 10)
                return
            except Exception:
                continue
    # Fall back to built-in Helvetica (no full Unicode but works for Latin)
    pdf.set_font("Helvetica", "", 10)
