# Bằng chứng RTO và RPO — Lab 23

Mọi con số trong báo cáo này đều được truy xuất trực tiếp từ dòng log thực tế theo định dạng đường_dẫn:số_dòng.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---|---|---|
| t_outage | 2026-10-09T10:12:18 | chaos kill | `chaos/chaos-events.jsonl:2` |
| Request fail đầu tiên | +0.1s | dòng ok:false đầu tiên sau t_outage | `reports/drill-1-nodr.jsonl:5` |
| Request thành công sau đó | không có | không có dòng ok:true nào sau t_outage | `reports/measure-drill-1.json` |
| RTO | NO_RECOVERY | tools/measure_rto.py | `reports/measure-drill-1.json` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---|---|---|
| t_outage (mốc 0) | 0.0s | action:kill | `chaos/chaos-events.jsonl:4` |
| User thấy lỗi đầu tiên | +0.0s | dòng ok:false đầu tiên sau t_outage | `reports/drill-2-withdr.jsonl:25` |
| Health check phát hiện | +15.0s | to:UNHEALTHY, region:a | `reports/health-events.jsonl:2` |
| Snapshot restore xong | +17.3s | step:2_restore_snapshot | `reports/failover-events.jsonl:2` |
| Region phụ ready | +23.4s | step:4_wait_ready | `reports/failover-events.jsonl:4` |
| DNS cutover | +23.4s | step:5_dns_cutover | `reports/failover-events.jsonl:5` |
| RTO đo được | +28.2s | dòng ok:true đầu tiên sau lỗi | `reports/drill-2-withdr.jsonl:39` |

| Chỉ số | Đo được | Mục tiêu | Verdict |
|---|---|---|---|
| RTO — Inference API | 28.2s | 300.0s (5 phút) | PASS |
| RPO — Vector DB | 4.0s / 2 doc | 300.0s (5 phút) | PASS |

## 3. RTO gồm những thành phần nào

| Thành phần | Giây | Nó đến từ đâu | Giảm được bằng cách nào |
|---|---|---|---|
| Health-check detect floor | 15.0s | interval_s nhân threshold trong `reports/health-events.jsonl:2` | Hạ interval_s hoặc threshold nhưng cần cân nhắc rủi ro flapping |
| Snapshot restore | 2.3s | Khoảng thời gian từ khi phát hiện đến khi hoàn tất bước 2_restore trong `reports/failover-events.jsonl:2` | Tăng tần suất snapshot hoặc sử dụng ổ cứng SSD NVMe tốc độ cao |
| GPU pool warm-up | 6.1s | waited_s ở bước 4_wait_ready trong `reports/failover-events.jsonl:4` | Duy trì pool ở trạng thái warm với model weights đã nạp sẵn trên VRAM |
| DNS/LB TTL cache | 4.8s | Độ trễ từ lúc đổi active_region đến request thành công đầu tiên trong `reports/drill-2-withdr.jsonl:39` | Giảm EDGE_TTL_SECONDS hoặc dùng Global Load Balancer có chủ động chuyển mạch |
