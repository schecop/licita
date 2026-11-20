"""What the extraction reads out of the pliegos: field groups, each with its own retrieval query.

One model call per group, over the chunks retrieved for that group's query. Grouping keeps each
prompt short and on one topic (the retrieval for "solvencia" and for "criterios de adjudicación"
finds different clauses), and one call per group, not per field, keeps the cost per tender at
~5 calls. The queries are in the pliegos' own wording, because BM25 matches words.

Every field comes back as {value, chunk_id, quote}:
- value set, with a citation: found and cited;
- value null, with a citation: the pliego says explicitly there is none ("No se exige
  clasificación");
- value null, no citation: not found in the retrieved chunks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NUMBER_OR_NULL: dict[str, Any] = {"type": ["number", "null"]}


@dataclass(frozen=True)
class Field:
    name: str
    value_schema: dict[str, Any]
    description: str  # what to extract, as the model reads it


@dataclass(frozen=True)
class FieldGroup:
    name: str
    query: str
    fields: tuple[Field, ...]


GROUPS: tuple[FieldGroup, ...] = (
    FieldGroup(
        "economics",
        "presupuesto base de licitación valor estimado del contrato importe IVA excluido "
        "plazo de ejecución duración meses prórroga",
        (
            Field(
                "budget_eur", NUMBER_OR_NULL, "Presupuesto base de licitación sin IVA, en euros."
            ),
            Field(
                "estimated_value_eur",
                NUMBER_OR_NULL,
                "Valor estimado del contrato sin IVA (incluye prórrogas y modificaciones), "
                "en euros.",
            ),
            Field(
                "duration_months",
                NUMBER_OR_NULL,
                "Plazo de ejecución inicial en meses, sin contar prórrogas.",
            ),
        ),
    ),
    FieldGroup(
        "deadline",
        "plazo de presentación de ofertas proposiciones fecha hora límite",
        (
            Field(
                "submission_deadline",
                {"type": ["string", "null"]},
                "Fecha y hora límite de presentación de ofertas, 'YYYY-MM-DD HH:MM' (hora de "
                "Madrid; 23:59 si no se indica hora).",
            ),
        ),
    ),
    FieldGroup(
        "solvency",
        "solvencia económica financiera volumen anual de negocios solvencia técnica profesional "
        "servicios similares importe certificación ISO esquema nacional de seguridad clasificación",
        (
            Field(
                "min_annual_turnover_eur",
                NUMBER_OR_NULL,
                "Volumen anual de negocios mínimo exigido como solvencia económica, en euros.",
            ),
            Field(
                "min_similar_services_eur",
                NUMBER_OR_NULL,
                "Importe mínimo de servicios similares exigido como solvencia técnica, en euros "
                "(el importe tal como lo pide el pliego, anual o acumulado).",
            ),
            Field(
                "certifications",
                {"type": ["array", "null"], "items": {"type": "string"}},
                "Certificaciones exigidas (ISO, ENS con su categoría...). Lista vacía con cita si "
                "el pliego dice que no se exigen.",
            ),
            Field(
                "classification",
                {"type": ["string", "null"]},
                "Clasificación empresarial exigida (grupo, subgrupo, categoría). null con cita si "
                "el pliego dice que no se exige.",
            ),
        ),
    ),
    FieldGroup(
        "award",
        "criterios de adjudicación puntuación ponderación oferta económica juicio de valor "
        "fórmulas puntos",
        (
            Field(
                "award_criteria",
                {
                    "type": ["array", "null"],
                    "items": {
                        "type": "object",
                        "properties": {
                            "criterion": {"type": "string"},
                            "weight": {"type": ["number", "null"]},
                        },
                        "required": ["criterion", "weight"],
                    },
                },
                "Criterios de adjudicación con su ponderación en puntos. La cita es la del "
                "primer criterio.",
            ),
        ),
    ),
    # what the contract is and who must work on it: the decision weighs both against the
    # company ("objeto ajeno a su actividad", a team it cannot field); the feed only has a title
    FieldGroup(
        "scope",
        "objeto del contrato prestación del servicio alcance adscripción de medios personales "
        "equipo de trabajo perfiles profesionales titulación experiencia jefe de proyecto",
        (
            Field(
                "contract_object",
                {"type": ["string", "null"]},
                "Objeto del contrato en una frase, con las palabras del pliego.",
            ),
            Field(
                "required_staff",
                {"type": ["array", "null"], "items": {"type": "string"}},
                "Medios personales que el licitador debe adscribir al contrato: un elemento por "
                "perfil, con número, titulación y experiencia mínima si constan. Lista vacía con "
                "cita si el pliego dice que no se exigen; null sin cita si no aparecen.",
            ),
        ),
    ),
)


def tool_schema(group: FieldGroup) -> dict[str, Any]:
    """JSON Schema of the `record_fields` tool for one group: {field: {value, chunk_id, quote}}."""
    return {
        "type": "object",
        "properties": {
            f.name: {
                "type": "object",
                "description": f.description,
                "properties": {
                    "value": f.value_schema,
                    "chunk_id": {"type": ["string", "null"]},
                    "quote": {"type": ["string", "null"]},
                },
                "required": ["value", "chunk_id", "quote"],
            }
            for f in group.fields
        },
        "required": [f.name for f in group.fields],
    }
