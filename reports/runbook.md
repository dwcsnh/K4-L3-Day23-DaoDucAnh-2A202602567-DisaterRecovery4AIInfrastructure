# Runbook 1 trang — Region chính down

Runbook này được thiết kế để kỹ sư trực on-call có thể thực thi chính xác vào ban đêm lúc 3h sáng. Mỗi bước đều có lệnh chạy trực tiếp, tiêu chí hoàn thành rõ ràng và vai trò chịu trách nhiệm.

| # | Bước | Lệnh | Biết là xong khi | Ai làm |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python3 chaos/kill_region.py status` | Region a trả về alive=false hoặc ready=false 3 lần liên tiếp, region b sẵn sàng | Kỹ sư trực on-call |
| 2 | Mở incident và bấm giờ RTO | `python3 dr/runbook.py --primary a --target b --backend fs` | Sự kiện được ghi nhận vào `reports/runbook-run.jsonl`, kênh trực nhận cảnh báo | Kỹ sư trực on-call / Incident Commander |
| 3 | Restore state ở region phụ | `python3 state/snapshot.py get --region b --backend fs` | File `state/region-b/vectors.sqlite` và model weights được nạp thành công | Kỹ sư trực on-call / Tự động qua runbook |
| 4 | Scale pool từ warm lên full | `printf full > state/region-b/pool_state && curl -s http://localhost:8002/readyz` | Endpoint `/readyz` của region b phản hồi HTTP 200 và ready=true | Kỹ sư trực on-call / Tự động qua runbook |
| 5 | Chuyển hướng lưu lượng DNS/LB (cutover) | `printf b > edge/active_region` | Gọi `curl -s http://localhost:8080/edge/state` hiển thị active_region=b | Incident Commander / Kỹ sư trực on-call |
| 6 | Kiểm tra golden signals | `python3 -c "import httpx; [print(httpx.get('http://localhost:8080/v1/infer').status_code) for _ in range(10)]"` | 10 request kiểm tra trả về HTTP 200, p95 latency dưới 50ms, error rate bằng 0% | Kỹ sư trực on-call |
| 7 | Đo RTO và lập báo cáo sự cố | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | Kết quả đo RTO trả về rto_verdict khác null và ghi nhận vào hồ sơ sự cố | Incident Commander |

Rollback (failover ngược):

Điều kiện trả traffic về region A:
1. Tiến trình phục vụ của region A đã được khôi phục, kiểm tra tiến trình không bị crash lặp lại.
2. Endpoint `/readyz` của region A phản hồi HTTP 200 liên tục tối thiểu 15 phút.
3. Dữ liệu mới phát sinh tại region B trong thời gian sự cố đã được đồng bộ ngược về region A, đảm bảo không thất thoát dữ liệu.
4. Tải hệ thống ở mức bình thường hoặc thấp điểm, tránh chuyển đổi khi tải đang cao điểm.

Người có thẩm quyền quyết định:
Chỉ Incident Commander hoặc Tech Lead phụ trách hạ tầng mới có thẩm quyền phê duyệt lệnh rollback. Tuyệt đối không áp dụng cơ chế tự động chuyển vùng ngược (auto-failback) mà không có sự kiểm tra của con người, nhằm ngăn chặn nguy cơ flapping (dao động đảo chiều liên tục giữa hai vùng).

Các bước rollback tiêu chuẩn:
1. Chạy snapshot dữ liệu từ region B: `python3 state/snapshot.py put --region b --backend fs`
2. Phục hồi dữ liệu sang region A: `python3 state/snapshot.py get --region a --backend fs`
3. Đảm bảo trạng thái pool của region A là full và `/readyz` trả về 200: `printf full > state/region-a/pool_state`
4. Chuyển DNS trỏ lại region A: `printf a > edge/active_region`
5. Kiểm tra 10 request mẫu qua edge proxy để đảm bảo độ trễ và tỷ lệ lỗi đạt chuẩn.
6. Hạ pool_state của region B về warm sau khi region A vận hành ổn định trong 30 phút.
