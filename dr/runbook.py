"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                               hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                               SAU t_outage trong chaos-events (không thể trùng — operator
                               không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                               log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                               đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                               và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                               weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                               runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr import health_checker as hc  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """Ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    rec = {
        "ts": now,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)),
        "step": n,
        "name": name,
        **kw,
    }
    with LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"RUNBOOK step {n} [{name}]:", json.dumps(rec))
    return rec


def confirm(auto: bool, msg: str) -> bool:
    """auto=True -> True; ngược lại hỏi y/N. Đừng bỏ hàm này đi."""
    if auto:
        return True
    ans = input(f"{msg} [y/N]: ").strip().lower()
    return ans in ("y", "yes")


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    t_start = time.time()

    # Bước 1: xac_nhan_outage
    health_log = pathlib.Path("reports/health-events.jsonl")
    chaos_path = pathlib.Path("chaos/chaos-events.jsonl")

    t_outage = None
    if chaos_path.exists():
        for line in reversed(chaos_path.read_text().splitlines()):
            if line.strip():
                try:
                    c = json.loads(line)
                    if c.get("action") == "kill" and c.get("region") == primary:
                        t_outage = c.get("ts")
                        break
                except Exception:
                    pass

    detected_by_hc = False
    hc_event = None
    if t_outage is not None:
        t_wait_hc = time.time()
        while time.time() - t_wait_hc < 25.0:
            if health_log.exists():
                for line in reversed(health_log.read_text().splitlines()):
                    if line.strip():
                        try:
                            ev = json.loads(line)
                            if (
                                ev.get("event") == "state_change"
                                and ev.get("region") == primary
                                and ev.get("to") == "UNHEALTHY"
                                and ev.get("ts") >= t_outage
                            ):
                                detected_by_hc = True
                                hc_event = ev
                                break
                        except Exception:
                            pass
            if detected_by_hc:
                break
            time.sleep(0.5)

    p_ok, p_reason = hc.probe(primary, timeout=2.0)
    t_ok, t_reason = hc.probe(target, timeout=2.0)
    step(
        1,
        "xac_nhan_outage",
        primary=primary,
        primary_ready=p_ok,
        primary_reason=p_reason,
        target=target,
        target_ready=t_ok,
        target_reason=t_reason,
        detected_by_hc=detected_by_hc,
        hc_detect_ts=hc_event.get("ts") if hc_event else None,
    )

    # Bước 2: thong_bao_incident
    t_notify = time.time()
    notice_delay_s = round(t_notify - t_outage, 2) if t_outage is not None else None

    if not confirm(auto, f"Xac nhan failover tu {primary} sang {target}?"):
        step(2, "thong_bao_incident", confirmed=False, reason="operator_aborted")
        return {"ok": False, "reason": "operator_aborted"}

    step(
        2,
        "thong_bao_incident",
        confirmed=True,
        t_outage=t_outage,
        t_notify=t_notify,
        notice_delay_s=notice_delay_s,
    )

    # Bước 3: scale_gpu_pool - gọi failover.failover MỘT LẦN DUY NHẤT
    fo_result = fo.failover(target=target, backend=backend, wait=60.0)
    step(3, "scale_gpu_pool", target=target, failover_ok=fo_result.get("ok"))
    if not fo_result.get("ok"):
        return {"ok": False, "error": "failover_failed", "detail": fo_result}

    # Bước 4: verify_state_replica - chỉ đọc kết quả từ dict bước 3 trả về
    step(
        4,
        "verify_state_replica",
        target=target,
        vector_count=fo_result.get("vector_count"),
        weights=fo_result.get("weights"),
        rpo_seconds=fo_result.get("rpo_seconds"),
        docs_lost=fo_result.get("docs_lost"),
        embed_model_version=fo_result.get("embed_model_version"),
    )

    # Bước 5: dns_cutover - đọc lại kết quả cutover
    step(5, "dns_cutover", target=target, ok=fo_result.get("ok"))

    # Bước 6: verify_golden_signals - 10 request thật vào region phụ
    latencies = []
    errors = 0
    num_reqs = 10
    with httpx.Client(timeout=5.0) as client:
        for _ in range(num_reqs):
            t0 = time.time()
            try:
                r = client.get(f"{URL[target]}/v1/infer")
                lat = (time.time() - t0) * 1000.0
                latencies.append(lat)
                if r.status_code != 200:
                    errors += 1
            except Exception:
                lat = (time.time() - t0) * 1000.0
                latencies.append(lat)
                errors += 1

    latencies.sort()
    idx_p95 = min(len(latencies) - 1, int(len(latencies) * 0.95))
    p95_ms = round(latencies[idx_p95], 2) if latencies else None
    error_rate = round(errors / num_reqs, 2)
    step(
        6,
        "verify_golden_signals",
        requests=num_reqs,
        p95_latency_ms=p95_ms,
        error_rate=error_rate,
    )

    # Bước 7: post_incident - elapsed_s + lệnh đo RTO
    elapsed_s = round(time.time() - t_start, 2)
    measure_cmd = "python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300"
    step(7, "post_incident", elapsed_s=elapsed_s, measure_cmd=measure_cmd)

    return {
        "ok": True,
        "elapsed_s": elapsed_s,
        "failover": fo_result,
        "golden_signals": {"p95_latency_ms": p95_ms, "error_rate": error_rate},
        "measure_cmd": measure_cmd,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
