from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response

from app.api.auth import CurrentUserDependency
from app.modules.chat.errors import ChatError
from app.modules.support.nr_recommendations import PositionNrRecommendationsResponse
from app.modules.support.unit_nr_recommendations import UnitNrRecommendationsResponse


router = APIRouter(prefix="/support", tags=["support"])


@router.post(
    "/cargos/{cargo_id}/nrs-sugeridas",
    response_model=PositionNrRecommendationsResponse,
)
async def suggest_nrs_for_position(
    cargo_id: Annotated[int, Path(ge=1)],
    user: CurrentUserDependency,
    request: Request,
    response: Response,
) -> PositionNrRecommendationsResponse:
    """Sugere NRs para um cargo sem persistir ou criar vínculos."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.nr_recommendation_service.analyze(cargo_id, user)
    except ChatError as error:
        raise HTTPException(error.status_code, error.detail) from None


@router.post(
    "/unidades/{unidade_id}/nrs-sugeridas",
    response_model=UnitNrRecommendationsResponse,
)
async def suggest_nrs_for_unit(
    unidade_id: Annotated[int, Path(ge=1)],
    user: CurrentUserDependency,
    request: Request,
    response: Response,
) -> UnitNrRecommendationsResponse:
    """Sugere NRs para uma unidade sem persistir ou criar vínculos."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.unit_nr_recommendation_service.analyze(
            unidade_id,
            user,
        )
    except ChatError as error:
        raise HTTPException(error.status_code, error.detail) from None
