#!/usr/bin/env python3
"""모모 리드 API용 최소 stdio MCP 서버.

Cursor가 이 프로세스를 띄워 tools/list · tools/call 을 JSON-RPC로 주고받는다.
외부 패키지(npx/uvx) 없이 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("LEAD_API_BASE_URL", "https://emons-sms-webhook.onrender.com").rstrip("/")
TOKEN = os.environ.get("LEAD_API_TOKEN", "").strip()
PROTOCOL = "2024-11-05"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _request(method: str, path: str, query: dict | None = None, body: dict | None = None) -> dict:
    if not TOKEN:
        return {"ok": False, "error": "LEAD_API_TOKEN missing in MCP env"}
    url = BASE_URL + path
    if query:
        filtered = {k: str(v) for k, v in query.items() if v is not None and v != ""}
        if filtered:
            url += "?" + urllib.parse.urlencode(filtered)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"ok": True, "status": resp.status}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"ok": False, "error": raw[:400], "status": e.code}
        if isinstance(parsed, dict):
            parsed.setdefault("ok", False)
            parsed.setdefault("status", e.code)
        return parsed
    except Exception as e:
        return {"ok": False, "error": str(e)}


TOOLS = [
    {
        "name": "search_leads",
        "description": (
            "리드 목록/검색. 전화번호 1개=리드 1건. "
            "오늘 신규는 stage=1_신규. 문자 발송 금지."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "전화번호. 있으면 단건"},
                "stage": {"type": "string", "description": "1_신규|2_상담중|3_견적발송|4_계약완료|5_실패|6_보류"},
                "store": {"type": "string"},
                "customer_type": {"type": "string"},
                "q": {"type": "string", "description": "이름/메모 검색어"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "get_lead",
        "description": "리드 단건 상세 + 최근 상담 이력. 단계 변경 전에 반드시 먼저 조회.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "integer"},
                "history_limit": {"type": "integer", "default": 20},
            },
            "required": ["lead_id"],
        },
    },
    {
        "name": "upsert_lead",
        "description": (
            "전화번호로 리드 upsert. 같은 전화면 새 row를 만들지 않고 UPDATE. "
            "문자 자동발송 없음."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string"},
                "name": {"type": "string"},
                "memo": {"type": "string"},
                "lead_source": {"type": "string"},
                "store_name": {"type": "string"},
                "employee_names": {"type": "string"},
                "customer_type": {"type": "string"},
                "next_contact_date": {"type": "string", "description": "YYYY-MM-DD"},
                "source_system": {"type": "string", "default": "grok_bot"},
            },
            "required": ["phone"],
        },
    },
    {
        "name": "update_lead",
        "description": "리드 단계/담당/다음연락일 수정. 변경 전 get_lead로 현재 단계를 확인.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "integer"},
                "lead_stage": {"type": "string"},
                "memo": {"type": "string"},
                "employee_names": {"type": "string"},
                "next_contact_date": {"type": "string"},
                "customer_type": {"type": "string"},
                "source_system": {"type": "string", "default": "grok_bot"},
            },
            "required": ["lead_id"],
        },
    },
    {
        "name": "add_lead_note",
        "description": "상담 한 줄을 app_chat_history에 append. 기존 memo를 덮어쓰지 않음.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "integer"},
                "summary": {"type": "string"},
                "full_text": {"type": "string"},
                "channel": {"type": "string", "default": "grok_note"},
                "handled_by": {"type": "string", "default": "grok_bot"},
            },
            "required": ["lead_id", "summary"],
        },
    },
]


def _call_tool(name: str, args: dict) -> dict:
    if name == "search_leads":
        return _request("GET", "/v1/leads", query={
            "phone": args.get("phone"),
            "stage": args.get("stage"),
            "store": args.get("store"),
            "customer_type": args.get("customer_type"),
            "q": args.get("q"),
            "limit": args.get("limit", 20),
        })
    if name == "get_lead":
        return _request("GET", f"/v1/leads/{int(args['lead_id'])}", query={
            "history_limit": args.get("history_limit", 20),
        })
    if name == "upsert_lead":
        body = {k: v for k, v in args.items() if v is not None}
        body.setdefault("source_system", "grok_bot")
        return _request("POST", "/v1/leads", body=body)
    if name == "update_lead":
        lead_id = int(args.pop("lead_id"))
        body = {k: v for k, v in args.items() if v is not None}
        body.setdefault("source_system", "grok_bot")
        return _request("PATCH", f"/v1/leads/{lead_id}", body=body)
    if name == "add_lead_note":
        lead_id = int(args.pop("lead_id"))
        body = {k: v for k, v in args.items() if v is not None}
        body.setdefault("channel", "grok_note")
        body.setdefault("handled_by", "grok_bot")
        return _request("POST", f"/v1/leads/{lead_id}/notes", body=body)
    return {"ok": False, "error": f"unknown tool: {name}"}


def _reply(msg_id, result=None, error=None) -> None:
    payload: dict = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle(msg: dict) -> None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        _reply(msg_id, {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "momo-leads", "version": "1.0.0"},
            "instructions": (
                "리드 도구는 전화번호 1개 = 리드 1건. 중복 INSERT 금지. "
                "단계 변경 전 get_lead. 문자 발송 금지."
            ),
        })
        return
    if method == "notifications/initialized":
        return
    if method == "ping":
        _reply(msg_id, {})
        return
    if method == "tools/list":
        _reply(msg_id, {"tools": TOOLS})
        return
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = _call_tool(name, args)
            _reply(msg_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": not bool(result.get("ok", True)),
            })
        except Exception as e:
            _log(f"tools/call 실패: {e}")
            _reply(msg_id, {
                "content": [{"type": "text", "text": str(e)}],
                "isError": True,
            })
        return
    if msg_id is not None:
        _reply(msg_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> None:
    _log(f"momo-leads MCP started base={BASE_URL} token_set={bool(TOKEN)}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _log(f"invalid json: {line[:120]}")
            continue
        _handle(msg)


if __name__ == "__main__":
    main()
