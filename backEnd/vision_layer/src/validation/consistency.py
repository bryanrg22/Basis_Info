"""Self-consistency checking via multi-pass VLM inference."""

import logging
from collections import Counter
from typing import Optional

from PIL import Image
from pydantic import BaseModel, Field

from ..api_clients.vlm import VLMClient
from ..schemas.artifact import VLMClassification

logger = logging.getLogger(__name__)


class ConsistencyResult(BaseModel):
    """Result of self-consistency check."""

    component_type: str = Field(..., description="Majority-voted component type")
    agreement_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Fraction of passes that agreed",
    )
    all_types: list[str] = Field(
        ..., description="All component types from each pass"
    )
    material: Optional[str] = Field(
        default=None, description="Majority-voted material if consistent"
    )
    material_agreement: Optional[float] = Field(
        default=None, description="Material agreement score"
    )
    needs_review: bool = Field(
        default=False, description="Flag if agreement is low"
    )
    review_reason: Optional[str] = Field(
        default=None, description="Reason for review flag"
    )


class ConsistencyChecker:
    """Check VLM consistency via multiple inference passes.

    Running the VLM multiple times on the same crop and voting on
    the results helps identify uncertain classifications.
    """

    def __init__(
        self,
        vlm_client: Optional[VLMClient] = None,
        num_passes: int = 3,
        agreement_threshold: float = 0.67,
        temperature_variance: float = 0.1,
    ):
        """Initialize consistency checker.

        Args:
            vlm_client: VLM client to use (created if not provided).
            num_passes: Number of inference passes.
            agreement_threshold: Minimum agreement to consider consistent.
            temperature_variance: Temperature variation between passes.
        """
        self._vlm_client = vlm_client
        self.num_passes = num_passes
        self.agreement_threshold = agreement_threshold
        self.temperature_variance = temperature_variance

    @property
    def vlm_client(self) -> VLMClient:
        if self._vlm_client is None:
            self._vlm_client = VLMClient()
        return self._vlm_client

    async def check_consistency(
        self,
        image: Image.Image,
        context: Optional[str] = None,
    ) -> ConsistencyResult:
        """Run multiple VLM passes and compute consistency.

        Args:
            image: PIL Image to classify.
            context: Optional context string.

        Returns:
            ConsistencyResult with voting results.
        """
        classifications = []

        for i in range(self.num_passes):
            try:
                # Slight temperature variation for diversity
                temp = 0.1 + (i * self.temperature_variance / self.num_passes)

                # Create temporary client with varied temperature
                temp_client = VLMClient(
                    model=self.vlm_client.model,
                    temperature=temp,
                )

                classification = await temp_client.classify_pil_image(
                    image=image,
                    context=context,
                )
                classifications.append(classification)

            except Exception as e:
                logger.warning(f"Consistency pass {i+1} failed: {e}")
                continue

        if not classifications:
            return ConsistencyResult(
                component_type="unknown",
                agreement_score=0.0,
                all_types=["error"],
                needs_review=True,
                review_reason="All consistency passes failed",
            )

        return self._compute_result(classifications)

    async def check_consistency_url(
        self,
        image_url: str,
        context: Optional[str] = None,
    ) -> ConsistencyResult:
        """Run consistency check on image URL.

        Args:
            image_url: URL of image to classify.
            context: Optional context string.

        Returns:
            ConsistencyResult with voting results.
        """
        classifications = []

        for i in range(self.num_passes):
            try:
                classification = await self.vlm_client.classify_image_url(
                    image_url=image_url,
                    context=context,
                )
                classifications.append(classification)

            except Exception as e:
                logger.warning(f"Consistency pass {i+1} failed: {e}")
                continue

        if not classifications:
            return ConsistencyResult(
                component_type="unknown",
                agreement_score=0.0,
                all_types=["error"],
                needs_review=True,
                review_reason="All consistency passes failed",
            )

        return self._compute_result(classifications)

    def _compute_result(
        self,
        classifications: list[VLMClassification],
    ) -> ConsistencyResult:
        """Compute voting results from classifications."""
        # Extract component types
        types = [c.component_type.lower().strip() for c in classifications]
        type_counts = Counter(types)

        # Majority vote for component type
        most_common_type, type_count = type_counts.most_common(1)[0]
        type_agreement = type_count / len(classifications)

        # Material voting
        materials = [c.material.lower().strip() for c in classifications if c.material]
        material = None
        material_agreement = None

        if materials:
            material_counts = Counter(materials)
            most_common_material, mat_count = material_counts.most_common(1)[0]
            material = most_common_material
            material_agreement = mat_count / len(materials)

        # Determine if needs review
        needs_review = type_agreement < self.agreement_threshold
        review_reason = None

        if needs_review:
            review_reason = (
                f"Low component type agreement: {type_agreement:.0%} "
                f"({type_count}/{len(classifications)} agreed on '{most_common_type}')"
            )

        return ConsistencyResult(
            component_type=most_common_type,
            agreement_score=type_agreement,
            all_types=types,
            material=material,
            material_agreement=material_agreement,
            needs_review=needs_review,
            review_reason=review_reason,
        )

    def merge_with_classification(
        self,
        original: VLMClassification,
        consistency: ConsistencyResult,
    ) -> VLMClassification:
        """Merge consistency results back into classification.

        Uses consistency results to override original if agreement is high.
        """
        # If consistency is high, use voted results
        if consistency.agreement_score >= self.agreement_threshold:
            return VLMClassification(
                component_type=consistency.component_type,
                material=consistency.material or original.material,
                condition=original.condition,
                color=original.color,
                brand=original.brand,
                model=original.model,
                dimensions_note=original.dimensions_note,
                installation_type=original.installation_type,
                additional_attributes={
                    **original.additional_attributes,
                    "consistency_score": consistency.agreement_score,
                    "consistency_types": consistency.all_types,
                },
                raw_response=original.raw_response,
            )

        # Low agreement - keep original but add metadata
        return VLMClassification(
            component_type=original.component_type,
            material=original.material,
            condition=original.condition,
            color=original.color,
            brand=original.brand,
            model=original.model,
            dimensions_note=original.dimensions_note,
            installation_type=original.installation_type,
            additional_attributes={
                **original.additional_attributes,
                "consistency_score": consistency.agreement_score,
                "consistency_types": consistency.all_types,
                "low_consistency_warning": True,
            },
            raw_response=original.raw_response,
        )
