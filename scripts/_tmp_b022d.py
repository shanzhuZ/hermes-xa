# -*- coding: utf-8 -*-
import json, time
from collect_01 import db
tid="b022e968-573b-47d7-adeb-9b89aa06e14c"
r=db.fetch_one("SELECT status,message,payload_json,updated_at FROM collect_phase_steps WHERE task_id=%s AND step_key='step11_report'",(tid,))
print("step11", r["status"], r["message"], r["updated_at"])
p=json.loads(r["payload_json"] or "{}")
print("payload", json.dumps(p, ensure_ascii=False, indent=2))
sa=float(p.get("flow_continue_started_at") or 0)
print("inflight_age_sec", round(time.time()-sa,1) if sa else None)
from report_04.gates import posts_substantively_ready, can_advance_to_analysis
print("posts_ready", posts_substantively_ready(tid), "analysis", can_advance_to_analysis(tid))
from report_04.session_continue import infer_continue_kind, continue_inflight, _payload
print("kind", infer_continue_kind(tid), "inflight", continue_inflight(tid))
print("p11_payload", _payload(tid))
