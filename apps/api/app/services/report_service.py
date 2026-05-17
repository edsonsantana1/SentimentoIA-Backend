import csv
import io
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape

from fastapi.responses import Response, StreamingResponse
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.database import get_db


class ReportService:
    """Exportação CSV/PDF baseada no MongoDB, compatível com search_id e batch_id."""

    BRAND_DARK = colors.HexColor("#111827")
    BRAND_BLUE = colors.HexColor("#2563eb")
    BRAND_GREEN = colors.HexColor("#059669")
    BRAND_RED = colors.HexColor("#dc2626")
    BRAND_AMBER = colors.HexColor("#d97706")
    LIGHT_BG = colors.HexColor("#f8fafc")
    BORDER = colors.HexColor("#d1d5db")

    @staticmethod
    def _text(value: Any, fallback: str = "-") -> str:
        value = fallback if value is None else str(value)
        value = " ".join(value.split())
        return escape(value or fallback)

    @staticmethod
    def _format_datetime(value: Any = None) -> str:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    @staticmethod
    def _styles() -> dict[str, ParagraphStyle]:
        base = getSampleStyleSheet()
        return {
            "title": ParagraphStyle(
                "SentimentoIATitle",
                parent=base["Title"],
                fontName="Helvetica-Bold",
                fontSize=20,
                leading=24,
                textColor=ReportService.BRAND_DARK,
                spaceAfter=10,
            ),
            "subtitle": ParagraphStyle(
                "SentimentoIASubtitle",
                parent=base["Normal"],
                fontSize=10,
                leading=14,
                textColor=colors.HexColor("#4b5563"),
                spaceAfter=8,
            ),
            "section": ParagraphStyle(
                "SentimentoIASection",
                parent=base["Heading2"],
                fontName="Helvetica-Bold",
                fontSize=13,
                leading=16,
                textColor=ReportService.BRAND_DARK,
                spaceBefore=12,
                spaceAfter=8,
            ),
            "body": ParagraphStyle(
                "SentimentoIABody",
                parent=base["BodyText"],
                fontSize=9.2,
                leading=13,
                textColor=colors.HexColor("#1f2937"),
                spaceAfter=5,
            ),
            "small": ParagraphStyle(
                "SentimentoIASmall",
                parent=base["Normal"],
                fontSize=7.5,
                leading=9,
                textColor=colors.HexColor("#6b7280"),
            ),
            "rightSmall": ParagraphStyle(
                "SentimentoIARightSmall",
                parent=base["Normal"],
                fontSize=7.5,
                leading=9,
                alignment=TA_RIGHT,
                textColor=colors.HexColor("#6b7280"),
            ),
            "center": ParagraphStyle(
                "SentimentoIACenter",
                parent=base["Normal"],
                alignment=TA_CENTER,
                fontSize=9,
                leading=12,
                textColor=colors.HexColor("#374151"),
            ),
        }

    @staticmethod
    def _footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#6b7280"))
        canvas.drawString(doc.leftMargin, 1.05 * cm, "SentimentoIA | Relatório gerado automaticamente")
        canvas.drawRightString(A4[0] - doc.rightMargin, 1.05 * cm, f"Página {doc.page}")
        canvas.restoreState()

    @staticmethod
    def _load_mentions(db, user_id: str, search_id: str) -> list[dict[str, Any]]:
        mentions = list(
            db.mentions.find(
                {"user_id": user_id, "$or": [{"search_id": search_id}, {"batch_id": search_id}]},
                {"raw": 0},
            ).sort("published_at", -1)
        )
        return mentions

    @staticmethod
    def _load_analysis(db, user_id: str, search_id: str) -> dict[str, Any]:
        job = db.search_jobs.find_one(
            {"user_id": user_id, "search_id": search_id, "status": "completed"},
            {"llm_analysis": 1, "metrics": 1, "query": 1},
        )
        if job and job.get("llm_analysis"):
            return {
                "query": job.get("query", ""),
                "metrics": job.get("metrics", {}),
                "llm_analysis": job.get("llm_analysis", {}),
            }

        insight = db.insights.find_one(
            {
                "user_id": user_id,
                "$or": [{"batch_id": search_id}, {"search_id": search_id}, {"context_id": search_id}],
                "archived": False,
            },
            sort=[("created_at", -1)],
        )
        if insight:
            return {
                "query": (insight.get("snapshot") or {}).get("brand", ""),
                "metrics": {},
                "llm_analysis": {
                    "executive_summary": insight.get("executive_summary", ""),
                    "risks": insight.get("risks", []),
                    "opportunities": insight.get("opportunities", []),
                    "recommended_actions": insight.get("recommended_actions", []),
                    "decision_guidance": insight.get("decision_guidance", ""),
                    "trend": insight.get("trend", "indefinido"),
                },
            }

        return {"query": "", "metrics": {}, "llm_analysis": {}}

    @staticmethod
    def export_csv(user_id: str, search_id: str) -> StreamingResponse:
        db = get_db()
        mentions = ReportService._load_mentions(db, user_id, search_id)

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "search_id", "query", "source", "author", "published_at", "rating",
            "sentiment", "confidence", "criticality", "urgency_score",
            "reputation_score", "aspects", "url", "text"
        ])

        for m in mentions:
            writer.writerow([
                m.get("search_id") or m.get("batch_id") or search_id,
                m.get("query") or m.get("entity") or "",
                m.get("source") or "",
                m.get("author") or "",
                m.get("published_at") or "",
                m.get("rating") or "",
                m.get("sentiment") or "",
                m.get("confidence") or "",
                m.get("criticality") or "",
                m.get("urgency_score") or "",
                m.get("reputation_score") or "",
                ";".join(m.get("aspects") or []),
                m.get("url") or "",
                (m.get("text") or "").replace("\n", " "),
            ])

        content = buffer.getvalue().encode("utf-8-sig")
        return StreamingResponse(
            io.BytesIO(content),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="relatorio-{search_id}.csv"'},
        )

    @staticmethod
    def _kpi_table(data: list[list[Any]], col_widths: list[int] | None = None) -> Table:
        table = Table(data, colWidths=col_widths or [160, 320], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), ReportService.BRAND_DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 1), (-1, -1), ReportService.LIGHT_BG),
            ("GRID", (0, 0), (-1, -1), 0.35, ReportService.BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ReportService.LIGHT_BG]),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return table

    @staticmethod
    def export_pdf(user_id: str, search_id: str) -> Response:
        db = get_db()
        mentions = ReportService._load_mentions(db, user_id, search_id)
        analysis_data = ReportService._load_analysis(db, user_id, search_id)
        llm = analysis_data.get("llm_analysis", {})
        query_name = analysis_data.get("query", "") or (mentions[0].get("query") if mentions else "Busca não identificada")

        from app.services.enrichment_service import EnrichmentService
        metrics = EnrichmentService.aggregate(mentions) if mentions else {}
        styles = ReportService._styles()

        generated_at = ReportService._format_datetime()
        total_mentions = int(metrics.get("total_mentions", len(mentions)))
        sentiment_dist = metrics.get("sentiment_distribution") or {}
        source_dist = metrics.get("source_distribution") or {}
        critical_count = int(metrics.get("critical_mentions", 0))
        reputation_score = metrics.get("reputation_score", 0)
        average_urgency = float(metrics.get("average_urgency", 0) or 0)
        trend_value = ReportService._text(metrics.get("trend") or llm.get("trend") or "Indefinido")

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=34, leftMargin=34, topMargin=58, bottomMargin=48)
        story: list[Any] = []

        story.append(Paragraph("Relatório Executivo de Reputação Digital", styles["title"]))
        story.append(Paragraph("Análise estruturada de menções, indicadores de reputação e orientações táticas.", styles["subtitle"]))
        story.append(ReportService._kpi_table([
            ["Campo", "Valor"],
            ["Marca / Consulta", Paragraph(ReportService._text(query_name), styles["body"])],
            ["Identificador da busca", Paragraph(ReportService._text(search_id), styles["body"])],
            ["Data de geração", generated_at],
            ["Total de menções capturadas", str(total_mentions)],
            ["Score de reputação", str(reputation_score)],
            ["Urgência média", f"{average_urgency:.2f}"],
            ["Tendência operacional", trend_value],
            ["Menções críticas", str(critical_count)],
        ]))
        story.append(Spacer(1, 14))

        narrative = []
        if total_mentions:
            narrative.append(
                f"O volume de {total_mentions} menções reflete a atividade atual de reputação para a consulta."
            )
            if critical_count:
                narrative.append(
                    f"Existem {critical_count} menções críticas, o que exige monitoramento imediato e priorização das respostas." 
                )
            if average_urgency >= 0.70:
                narrative.append(
                    "A urgência média elevada indica que a percepção do público requer ação operacional de curto prazo."
                )
            if reputation_score and reputation_score < 45:
                narrative.append(
                    "O índice de reputação sugere risco reputacional aumentado, sendo necessária revisão de posicionamento e resposta a menções negativas." 
                )
            if not narrative:
                narrative.append(
                    "A situação atual não revelou sinais críticos imediatos, porém a vigilância contínua permanece necessária."
                )
        else:
            narrative.append(
                "Não há menções suficientes para formar um diagnóstico quantitativo robusto. Execute nova busca com ajuste de parâmetros." 
            )

        story.append(Paragraph("1. Diagnóstico", styles["section"]))
        for sentence in narrative:
            story.append(Paragraph(ReportService._text(sentence), styles["body"]))

        story.append(Paragraph("2. Tendências", styles["section"]))
        story.append(Paragraph(
            ReportService._text(
                f"A tendência identificada no período é: {trend_value}. Este indicador orienta alocação de esforços operacionais e prioridades de comunicação."
            ), styles["body"]
        ))
        if sentiment_dist:
            for sentiment, count in sentiment_dist.items():
                story.append(Paragraph(
                    ReportService._text(f"Sentimento '{sentiment}' representa {count} menções no conjunto analisado."),
                    styles["body"],
                ))

        story.append(Paragraph("3. Ações Recomendadas", styles["section"]))
        actions = llm.get("recommended_actions") or []
        if actions:
            for idx, action in enumerate(actions[:8], start=1):
                story.append(Paragraph(f"{idx}. {ReportService._text(action)}", styles["body"]))
        else:
            story.append(Paragraph(
                "Não há recomendações estruturadas disponíveis no momento. Reavalie a fonte de dados ou gere um novo insight.",
                styles["body"],
            ))

        story.append(Paragraph("4. Métricas Operacionais", styles["section"]))
        metrics_table = [
            ["Métrica", "Valor"],
            ["Total de menções", str(total_mentions)],
            ["Menções críticas", str(critical_count)],
            ["Urgência média", f"{average_urgency:.2f}"],
            ["Score de reputação", str(reputation_score)],
            ["Tendência", trend_value],
        ]
        for src, count in source_dist.items():
            metrics_table.append([f"Fonte: {src}", str(count)])
        story.append(ReportService._kpi_table(metrics_table))

        story.append(Paragraph("5. Amostra de menções", styles["section"]))
        sample = [["Fonte", "Autor", "Sentimento", "Criticidade", "Texto"]]
        for m in mentions[:10]:
            sample.append([
                ReportService._text(str(m.get("source", ""))[:18]),
                ReportService._text(str(m.get("author", ""))[:18]),
                ReportService._text(m.get("sentiment", "")),
                ReportService._text(m.get("criticality", "")),
                Paragraph(ReportService._text((m.get("text", "") or "")[:170]), styles["small"]),
            ])
        story.append(ReportService._kpi_table(sample, [55, 60, 60, 60, 245]))

        doc.build(story, onFirstPage=ReportService._footer, onLaterPages=ReportService._footer)
        pdf = buffer.getvalue()
        buffer.close()

        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="relatorio-executivo-{search_id}.pdf"'},
        )

    @staticmethod
    def _normalize_priority_filter(priority: str | None) -> str | None:
        candidate = str(priority or "").strip().lower().replace(" ", "_")
        if candidate in {"alta", "high", "critica", "critical"}:
            return "high"
        if candidate in {"media", "medium", "moderada", "moderate"}:
            return "medium"
        if candidate in {"baixa", "low", "ok"}:
            return "low"
        return None

    @staticmethod
    def _normalize_resolution_filter(resolution: str | None) -> str | None:
        candidate = str(resolution or "").strip().lower().replace(" ", "_")
        if candidate in {"resolved", "resolvido", "done", "concluido", "concluído"}:
            return "resolved"
        if candidate in {"in_progress", "em_andamento", "processing", "working"}:
            return "in_progress"
        if candidate in {"pending", "pendente", "open", "novo", "new"}:
            return "pending"
        return None

    @staticmethod
    def _load_insights(
        db,
        user_id: str,
        priority: str | None,
        resolution: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"user_id": user_id, "archived": {"$ne": True}}
        normalized_priority = ReportService._normalize_priority_filter(priority)
        normalized_resolution = ReportService._normalize_resolution_filter(resolution)

        if normalized_priority:
            query["priority"] = normalized_priority
        if normalized_resolution:
            query["resolution"] = normalized_resolution

        return list(db.insights.find(query).sort("created_at", -1).limit(max(1, min(limit, 500))))

    @staticmethod
    def export_insights_markdown(
        user_id: str,
        priority: str | None = None,
        resolution: str | None = None,
        limit: int = 100,
    ) -> StreamingResponse:
        db = get_db()
        insights = ReportService._load_insights(db, user_id, priority, resolution, limit)
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines: list[str] = [
            "# Exportação de Insights - SentimentoIA",
            "",
            f"Gerado em: {generated_at}",
            f"Total de insights: {len(insights)}",
            "",
        ]

        if not insights:
            lines.append("Nenhum insight encontrado para os filtros informados.")
        else:
            for idx, insight in enumerate(insights, start=1):
                timestamp = insight.get("timestamp") or insight.get("created_at") or "-"
                lines.extend([
                    f"## {idx}. {insight.get('company') or (insight.get('snapshot') or {}).get('brand') or 'Empresa não informada'}",
                    "",
                    f"- Prioridade: {insight.get('priority') or 'medium'}",
                    f"- Urgência: {insight.get('urgency') or 'medium'}",
                    f"- Status: {insight.get('status') or 'open'}",
                    f"- Resolução: {insight.get('resolution') or 'pending'}",
                    f"- Timestamp: {timestamp}",
                    "",
                    f"**Causa raiz:** {insight.get('root_cause') or 'Não informado'}",
                    "",
                    f"**Ação recomendada:** {insight.get('recommended_action') or 'Não informado'}",
                    "",
                    f"**Resumo executivo:** {insight.get('executive_summary') or 'Não informado'}",
                    "",
                    f"**Direcionamento:** {insight.get('decision_guidance') or 'Não informado'}",
                    "",
                    "---",
                    "",
                ])

        payload = "\n".join(lines).encode("utf-8")
        return StreamingResponse(
            io.BytesIO(payload),
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="insights.md"'},
        )

    @staticmethod
    def export_insights_pdf(
        user_id: str,
        priority: str | None = None,
        resolution: str | None = None,
        limit: int = 100,
    ) -> Response:
        db = get_db()
        insights = ReportService._load_insights(db, user_id, priority, resolution, limit)
        styles = ReportService._styles()

        priority_counts = Counter(str(i.get("priority") or "medium") for i in insights)
        resolution_counts = Counter(str(i.get("resolution") or "pending") for i in insights)

        generated_at = ReportService._format_datetime()
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=34, leftMargin=34, topMargin=58, bottomMargin=48)
        story: list[Any] = []

        story.append(Paragraph("Exportação Executiva de Insights", styles["title"]))
        story.append(Paragraph("Relatório consolidado de insights, prioridades e recomendações para o time de reputação.", styles["subtitle"]))
        story.append(ReportService._kpi_table([
            ["Indicador", "Valor"],
            ["Data de geração", generated_at],
            ["Total de insights", str(len(insights))],
            ["Prioridade alta", str(priority_counts.get("high", 0))],
            ["Prioridade média", str(priority_counts.get("medium", 0))],
            ["Prioridade baixa", str(priority_counts.get("low", 0))],
            ["Pendentes", str(resolution_counts.get("pending", 0))],
            ["Em andamento", str(resolution_counts.get("in_progress", 0))],
            ["Resolvidos", str(resolution_counts.get("resolved", 0))],
        ]))
        story.append(Spacer(1, 12))

        if not insights:
            story.append(Paragraph("Nenhum insight disponível para os filtros selecionados. Execute uma análise ou reduza o filtro aplicado.", styles["body"]))
        else:
            story.append(Paragraph("1. Panorama Geral", styles["section"]))
            summary_lines = [
                f"Foram encontrados {len(insights)} insights não arquivados no período selecionado.",
                f"A categoria de prioridade mais frequente é '{ReportService._text(max(priority_counts, key=priority_counts.get) if priority_counts else 'média')}'.",
                f"O principal estado de resolução é '{ReportService._text(max(resolution_counts, key=resolution_counts.get) if resolution_counts else 'pendente')}'.",
            ]
            for line in summary_lines:
                story.append(Paragraph(ReportService._text(line), styles["body"]))

            story.append(Paragraph("2. Concentração de Prioridades", styles["section"]))
            table_data = [["Empresa", "Prioridade", "Urgência", "Status", "Resolução", "Resumo executivo"]]
            for insight in insights[:35]:
                company = ReportService._text(str(insight.get("company") or (insight.get("snapshot") or {}).get("brand") or "-")[:30])
                table_data.append([
                    company,
                    ReportService._text(insight.get("priority") or "medium"),
                    ReportService._text(insight.get("urgency") or "medium"),
                    ReportService._text(insight.get("status") or "open"),
                    ReportService._text(insight.get("resolution") or "pending"),
                    Paragraph(ReportService._text(str(insight.get("executive_summary") or "-")[:140]), styles["small"]),
                ])
            story.append(ReportService._kpi_table(table_data, [80, 55, 55, 55, 65, 230]))
            story.append(Spacer(1, 10))

            story.append(Paragraph("3. Insights em Destaque", styles["section"]))
            for idx, insight in enumerate(insights[:18], start=1):
                company = ReportService._text(str(insight.get("company") or (insight.get("snapshot") or {}).get("brand") or "Empresa não informada"))
                story.append(Paragraph(f"{idx}. {company}", styles["section"]))
                story.append(Paragraph("<b>Resumo executivo:</b> " + ReportService._text(insight.get("executive_summary") or "Não informado"), styles["body"]))
                story.append(Paragraph("<b>Causa raiz:</b> " + ReportService._text(insight.get("root_cause") or "Não informado"), styles["body"]))
                story.append(Paragraph("<b>Ação recomendada:</b> " + ReportService._text(insight.get("recommended_action") or "Não informado"), styles["body"]))
                story.append(Paragraph("<b>Direcionamento:</b> " + ReportService._text(insight.get("decision_guidance") or "Não informado"), styles["body"]))
                if insight.get("risks"):
                    story.append(Paragraph("<b>Riscos principais:</b> " + ReportService._text("; ".join(str(r) for r in (insight.get("risks") or [])[:4])), styles["body"]))
                if insight.get("recommended_actions"):
                    story.append(Paragraph("<b>Próximas ações:</b> " + ReportService._text("; ".join(str(a) for a in (insight.get("recommended_actions") or [])[:4])), styles["body"]))
                story.append(Spacer(1, 8))

        doc.build(story, onFirstPage=ReportService._footer, onLaterPages=ReportService._footer)
        pdf = buffer.getvalue()
        buffer.close()

        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="insights-executivo.pdf"'},
        )
