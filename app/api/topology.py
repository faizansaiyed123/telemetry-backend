"""Service registry and dependency topology API."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin, require_authenticated
from app.db.session import get_db
from app.models.db import Service, ServiceDependency, SyntheticCheck, SyntheticCheckRun, User
from app.models.observability import (
    ServiceCreate,
    ServiceDependencyCreate,
    ServiceDependencyResponse,
    ServiceResponse,
    ServiceUpdate,
    TopologyEdge,
    TopologyNode,
    TopologyResponse,
)
from app.services.audit import add_audit_log

router = APIRouter(prefix="/api/services", tags=["services"])


def _service_response(service: Service) -> ServiceResponse:
    return ServiceResponse.model_validate(service)


@router.get("", response_model=list[ServiceResponse])
def list_services(
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> list[ServiceResponse]:
    return [_service_response(service) for service in db.scalars(select(Service).order_by(Service.name))]


@router.post("", response_model=ServiceResponse, status_code=status.HTTP_201_CREATED)
def create_service(
    payload: ServiceCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ServiceResponse:
    if db.scalar(select(Service).where(Service.name == payload.name)):
        raise HTTPException(status_code=409, detail="A service with this name already exists")
    service = Service(
        id=str(uuid4()),
        name=payload.name,
        environment=payload.environment,
        description=payload.description,
        created_by=current_user.id,
    )
    db.add(service)
    db.flush()
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="service.created",
        resource_type="service",
        resource_id=service.id,
        details={"name": service.name, "environment": service.environment},
    )
    db.commit()
    db.refresh(service)
    return _service_response(service)


@router.patch("/{service_id}", response_model=ServiceResponse)
def update_service(
    service_id: str,
    payload: ServiceUpdate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ServiceResponse:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found")
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        values["name"] = " ".join(values["name"].strip().split())
        if not values["name"]:
            raise HTTPException(status_code=422, detail="Service name must not be blank")
        duplicate = db.scalar(
            select(Service).where(Service.name == values["name"], Service.id != service.id)
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="A service with this name already exists")
    if "environment" in values:
        values["environment"] = " ".join(values["environment"].strip().split())
        if not values["environment"]:
            raise HTTPException(status_code=422, detail="Service environment must not be blank")
    for key, value in values.items():
        setattr(service, key, value)

    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="service.updated",
        resource_type="service",
        resource_id=service.id,
        details={"fields": list(values)},
    )
    db.commit()
    db.refresh(service)
    return _service_response(service)


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_service(
    service_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found")
    db.delete(service)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="service.deleted",
        resource_type="service",
        resource_id=service_id,
    )
    db.commit()


@router.post("/{service_id}/dependencies", response_model=ServiceDependencyResponse, status_code=status.HTTP_201_CREATED)
def add_dependency(
    service_id: str,
    payload: ServiceDependencyCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ServiceDependencyResponse:
    if db.get(Service, service_id) is None:
        raise HTTPException(status_code=404, detail="Source service not found")
    if db.get(Service, payload.target_service_id) is None:
        raise HTTPException(status_code=404, detail="Target service not found")
    if service_id == payload.target_service_id:
        raise HTTPException(status_code=422, detail="A service cannot depend on itself")
    existing = db.scalar(
        select(ServiceDependency).where(
            ServiceDependency.source_service_id == service_id,
            ServiceDependency.target_service_id == payload.target_service_id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Dependency already exists")

    dependency = ServiceDependency(
        source_service_id=service_id,
        target_service_id=payload.target_service_id,
        relationship=payload.relationship.strip(),
        criticality=payload.criticality,
    )
    db.add(dependency)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="service_dependency.created",
        resource_type="service_dependency",
        resource_id=f"{service_id}->{payload.target_service_id}",
    )
    db.commit()
    db.refresh(dependency)
    return ServiceDependencyResponse.model_validate(dependency)


@router.delete("/{service_id}/dependencies/{target_service_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_dependency(
    service_id: str,
    target_service_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    dependency = db.scalar(
        select(ServiceDependency).where(
            ServiceDependency.source_service_id == service_id,
            ServiceDependency.target_service_id == target_service_id,
        )
    )
    if dependency is None:
        raise HTTPException(status_code=404, detail="Dependency not found")
    db.delete(dependency)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="service_dependency.deleted",
        resource_type="service_dependency",
        resource_id=f"{service_id}->{target_service_id}",
    )
    db.commit()


@router.get("/{service_id}/dependencies", response_model=list[ServiceDependencyResponse])
def list_dependencies(
    service_id: str,
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> list[ServiceDependencyResponse]:
    if db.get(Service, service_id) is None:
        raise HTTPException(status_code=404, detail="Service not found")
    return [
        ServiceDependencyResponse.model_validate(item)
        for item in db.scalars(
            select(ServiceDependency).where(ServiceDependency.source_service_id == service_id)
        )
    ]


topology_router = APIRouter(prefix="/api/topology", tags=["topology"])


@topology_router.get("", response_model=TopologyResponse)
def get_topology(
    _: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> TopologyResponse:
    services = list(db.scalars(select(Service).order_by(Service.name)))
    dependencies = list(db.scalars(select(ServiceDependency)))

    check_rows = db.execute(
        select(SyntheticCheck.service_id, func.count(SyntheticCheck.id))
        .where(SyntheticCheck.service_id.is_not(None))
        .group_by(SyntheticCheck.service_id)
    ).all()
    check_counts = {service_id: int(count) for service_id, count in check_rows}

    latest_run_ranked = (
        select(
            SyntheticCheckRun.check_id,
            SyntheticCheckRun.success,
            func.row_number().over(
                partition_by=SyntheticCheckRun.check_id,
                order_by=SyntheticCheckRun.checked_at.desc(),
            ).label("rn"),
        )
        .subquery()
    )
    healthy_rows = db.execute(
        select(SyntheticCheck.service_id, func.count(SyntheticCheck.id))
        .join(
            latest_run_ranked,
            (latest_run_ranked.c.check_id == SyntheticCheck.id)
            & (latest_run_ranked.c.rn == 1),
        )
        .where(
            SyntheticCheck.service_id.is_not(None),
            latest_run_ranked.c.success.is_(True),
        )
        .group_by(SyntheticCheck.service_id)
    ).all()
    healthy_counts = {service_id: int(count) for service_id, count in healthy_rows}

    incoming: dict[str, int] = {service.id: 0 for service in services}
    outgoing: dict[str, int] = {service.id: 0 for service in services}
    edges: list[TopologyEdge] = []
    for dependency in dependencies:
        outgoing[dependency.source_service_id] = outgoing.get(dependency.source_service_id, 0) + 1
        incoming[dependency.target_service_id] = incoming.get(dependency.target_service_id, 0) + 1
        edges.append(
            TopologyEdge(
                source=dependency.source_service_id,
                target=dependency.target_service_id,
                relationship=dependency.relationship,
                criticality=dependency.criticality,
            )
        )

    nodes = [
        TopologyNode(
            id=service.id,
            name=service.name,
            environment=service.environment,
            check_count=check_counts.get(service.id, 0),
            healthy_check_count=healthy_counts.get(service.id, 0),
            incoming_dependencies=incoming.get(service.id, 0),
            outgoing_dependencies=outgoing.get(service.id, 0),
        )
        for service in services
    ]
    return TopologyResponse(
        generated_at=datetime.now(timezone.utc),
        nodes=nodes,
        edges=edges,
    )
