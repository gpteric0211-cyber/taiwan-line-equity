from __future__ import annotations

from typing import Any

from core.data_quality import DataQualityStatus, assess_chart_image_analysis


IMAGE_DATA_QUALITY_VERSION = "ImageDataQualityV1"


def assess_chart_image_analysis_v1(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Apply the strict V1 classification schema around the protected quality gate."""

    payload = raw if isinstance(raw, dict) else {}
    result = assess_chart_image_analysis(payload)
    reasons = list(result.get("reasons") or [])
    schema_valid = type(payload.get("is_stock_chart")) is bool
    image_quality = str(result.get("image_quality") or "unusable")
    confidence = float(result.get("overall_confidence") or 0.0)
    classification_ready = bool(
        schema_valid
        and image_quality in {"high", "medium"}
        and confidence >= 0.6
    )
    if not schema_valid:
        reasons.insert(0, "is_stock_chart_must_be_a_required_boolean")
    reasons = list(dict.fromkeys(reasons))
    if not schema_valid:
        result.update(
            {
                "ready": False,
                "status": DataQualityStatus.UNAVAILABLE.value,
                "reason": ",".join(reasons),
                "reasons": reasons,
            }
        )
    result.update(
        {
            "quality_version": IMAGE_DATA_QUALITY_VERSION,
            "classification_schema_valid": schema_valid,
            "classification_ready": classification_ready,
        }
    )
    return result
