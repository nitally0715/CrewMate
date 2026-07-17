"""SpecReportAgentFunction Lambda entry point."""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import boto3
from pydantic import ValidationError

from ncs_collector.models import ApplicantSpecInput
from ncs_collector.trade_requirements import TradeNotFoundError
from shared import db
from shared.auth import get_principal
from shared.responses import ApiError
from shared.schemas import build_notification, now_iso
from shared.state import Role
from spec_report.aws_rules import S3RuleRepository
from spec_report.orchestrator import SpecReportService
from spec_report.qnet import DynamoQualificationCache, QNetHttpAdapter, QNetQualificationService
from spec_report.report_agent import ReportAgentRunner
from spec_report.retrieval import BedrockKnowledgeBaseRetriever
from spec_report.storage import S3ReportStorage

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_SERVICE: SpecReportService | None = None
_LAMBDA_CLIENT = None


def _notify_report_result(
    *,
    user_id: str,
    report_id: str,
    target_trade: str,
    created_at: str,
    succeeded: bool,
) -> None:
    """Best-effort completion notification; report generation must not depend on it."""
    outcome = "COMPLETED" if succeeded else "FAILED"
    title = "스펙 보고서 생성 완료" if succeeded else "스펙 보고서 생성 실패"
    message = (
        f"{target_trade} 스펙 보고서가 준비되었습니다. 보고서 목록에서 확인해주세요."
        if succeeded
        else f"{target_trade} 스펙 보고서를 만들지 못했습니다. 잠시 후 다시 시도해주세요."
    )
    try:
        db.put_notification(build_notification(
            user_id=user_id,
            type=f"SPEC_REPORT_{outcome}",
            title=title,
            message=message,
            notification_id=f"NOTI_REPORT_{report_id}_{outcome}",
            created_at=created_at,
        ))
    except Exception:  # noqa: BLE001 - notification failure must not fail the report job
        logger.exception("spec_report_notification_failed report_id=%s outcome=%s", report_id, outcome)


def _response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(payload, ensure_ascii=False),
    }


def build_service() -> SpecReportService:
    repository = S3RuleRepository()
    retriever = BedrockKnowledgeBaseRetriever()
    qnet = QNetQualificationService(
        QNetHttpAdapter(
            timeout=float(os.environ.get("QNET_TIMEOUT_SECONDS", "5")),
            retries=int(os.environ.get("QNET_MAX_RETRIES", "1")),
            min_interval=float(os.environ.get("QNET_MIN_INTERVAL_SECONDS", "0.5")),
        ),
        DynamoQualificationCache(),
    )
    return SpecReportService(
        repository,
        retriever,
        qnet,
        agent_runner=ReportAgentRunner(retriever, qnet),
        storage=S3ReportStorage(),
    )


def _service() -> SpecReportService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = build_service()
    return _SERVICE


def _storage() -> S3ReportStorage:
    storage = _service().storage
    if not isinstance(storage, S3ReportStorage):
        raise RuntimeError("S3 report storage is not configured")
    return storage


def _parse_applicant(event: dict[str, Any]) -> ApplicantSpecInput:
    raw_body = event.get("body") if isinstance(event, dict) else event
    if isinstance(raw_body, str):
        payload = json.loads(raw_body)
    elif isinstance(raw_body, dict):
        payload = raw_body
    elif isinstance(event, dict) and "targetTrade" in event:
        payload = event
    else:
        raise ValueError("request body is required")
    return ApplicantSpecInput.model_validate(payload)


def _require_worker(event: dict[str, Any]):
    principal = get_principal(event)
    principal.require_role(Role.WORKER)
    return principal


def _invoke_async(payload: dict[str, Any], context: Any) -> None:
    global _LAMBDA_CLIENT
    if _LAMBDA_CLIENT is None:
        _LAMBDA_CLIENT = boto3.client("lambda")
    function_name = getattr(context, "invoked_function_arn", None) or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    if not function_name:
        raise RuntimeError("Lambda function name is unavailable")
    response = _LAMBDA_CLIENT.invoke(
        FunctionName=function_name,
        InvocationType="Event",
        Payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    if int(response.get("StatusCode", 0)) != 202:
        raise RuntimeError("asynchronous Lambda invocation was not accepted")


def _start_job(event: dict[str, Any], context: Any) -> dict[str, Any]:
    principal = _require_worker(event)
    applicant = _parse_applicant(event).model_copy(update={"persist_report": True})
    report_id = str(uuid.uuid4())
    notification_created_at = now_iso()
    storage = _storage()
    storage.start_job(
        report_id,
        owner_user_id=principal.user_id,
        target_trade=applicant.target_trade,
    )
    try:
        _invoke_async({
            "_specReportAction": "GENERATE",
            "reportId": report_id,
            "applicant": applicant.model_dump(mode="json", by_alias=True),
            "_notificationUserId": principal.user_id,
            "_notificationCreatedAt": notification_created_at,
        }, context)
    except Exception:
        storage.fail_job(report_id, "ASYNC_INVOKE_FAILED")
        _notify_report_result(
            user_id=principal.user_id,
            report_id=report_id,
            target_trade=applicant.target_trade,
            created_at=notification_created_at,
            succeeded=False,
        )
        raise
    return _response(202, {"reportId": report_id, "status": "PROCESSING"})


def _get_job(event: dict[str, Any]) -> dict[str, Any]:
    principal = _require_worker(event)
    params = event.get("pathParameters") or {}
    report_id = params.get("reportId") or (event.get("path") or "").rstrip("/").split("/")[-1]
    job = _storage().get_job(report_id)
    if not job:
        return _response(404, {"error": {"code": "REPORT_NOT_FOUND", "message": "저장된 보고서를 찾을 수 없습니다."}})
    if job.get("owner_user_id") != principal.user_id:
        return _response(403, {"error": {"code": "FORBIDDEN", "message": "다른 사용자의 보고서에 접근할 수 없습니다."}})
    status = job.get("status") or "PROCESSING"
    if status == "COMPLETED":
        result = _storage().read(report_id)
        result["status"] = status
        return _response(200, result)
    result = {"reportId": report_id, "status": status}
    if status == "FAILED":
        result["error"] = {
            "code": job.get("error_code") or "REPORT_GENERATION_FAILED",
            "message": "보고서 생성에 실패했습니다. 다시 시도해주세요.",
        }
    return _response(200, result)


def _list_jobs(event: dict[str, Any]) -> dict[str, Any]:
    principal = _require_worker(event)
    jobs = _storage().list_jobs(principal.user_id)
    return _response(200, [
        {
            "reportId": item.get("report_id"),
            "targetTrade": item.get("target_trade") or "직종 미확인",
            "status": item.get("status") or "PROCESSING",
            "createdAt": item.get("created_at"),
            "completedAt": item.get("completed_at"),
            "errorCode": item.get("error_code"),
        }
        for item in jobs
    ])


def _run_async_job(event: dict[str, Any]) -> dict[str, Any]:
    report_id = str(event.get("reportId") or "")
    storage = _storage()
    user_id = str(event.get("_notificationUserId") or "")
    notification_created_at = str(event.get("_notificationCreatedAt") or now_iso())
    target_trade = "요청한 직종"
    try:
        applicant = ApplicantSpecInput.model_validate(event.get("applicant") or {}).model_copy(
            update={"persist_report": True}
        )
        target_trade = applicant.target_trade
        _service().generate(applicant, report_id=report_id)
        if user_id:
            _notify_report_result(
                user_id=user_id,
                report_id=report_id,
                target_trade=target_trade,
                created_at=notification_created_at,
                succeeded=True,
            )
        return {"status": "COMPLETED", "reportId": report_id}
    except Exception as exc:
        storage.fail_job(report_id)
        if user_id:
            _notify_report_result(
                user_id=user_id,
                report_id=report_id,
                target_trade=target_trade,
                created_at=notification_created_at,
                succeeded=False,
            )
        logger.exception(
            "spec_report_async_failed report_id=%s exception=%s",
            report_id,
            type(exc).__name__,
        )
        return {"status": "FAILED", "reportId": report_id}


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    if event.get("_specReportAction") == "GENERATE":
        return _run_async_job(event)

    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or event.get("resource") or ""
    if method == "OPTIONS":
        return _response(200, {})
    try:
        if method == "POST" and path.rstrip("/") == "/reports/spec-gap/jobs":
            return _start_job(event, _context)
        if method == "GET" and path.rstrip("/") == "/reports/spec-gap/jobs":
            return _list_jobs(event)
        if method == "GET" and "/reports/spec-gap/jobs/" in path:
            return _get_job(event)
    except ApiError as exc:
        return exc.to_response()
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        logger.info("spec_report_rejected error_code=INVALID_INPUT")
        return _response(400, {"error": {"code": "INVALID_INPUT", "message": str(exc)}})
    except Exception as exc:
        logger.exception("spec_report_job_failed exception=%s", type(exc).__name__)
        return _response(500, {"error": {"code": "REPORT_JOB_FAILED", "message": "보고서 작업 처리 중 오류가 발생했습니다."}})

    try:
        applicant = _parse_applicant(event)
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        logger.info("spec_report_rejected error_code=INVALID_INPUT")
        return _response(400, {"error": {"code": "INVALID_INPUT", "message": str(exc)}})

    try:
        report, markdown, stored = _service().generate(applicant)
        result: dict[str, Any] = {
            "report": report.model_dump(mode="json", by_alias=True),
            "persisted": bool(stored),
        }
        if markdown is not None:
            result["markdown"] = markdown
        if stored:
            result["storage"] = stored
        return _response(200, result)
    except TradeNotFoundError:
        logger.info("spec_report_failed error_code=TRADE_NOT_FOUND target_trade=%s", applicant.target_trade)
        return _response(404, {"error": {"code": "TRADE_NOT_FOUND", "message": "구조화 규칙에서 직종을 찾을 수 없습니다."}})
    except Exception as exc:  # deployment/config/storage errors; no sensitive payload logging
        logger.exception("spec_report_failed error_code=REPORT_GENERATION_FAILED exception=%s", type(exc).__name__)
        return _response(500, {"error": {"code": "REPORT_GENERATION_FAILED", "message": "보고서 생성 중 오류가 발생했습니다."}})
