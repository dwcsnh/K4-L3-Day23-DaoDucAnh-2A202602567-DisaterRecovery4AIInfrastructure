# Postmortem — DR Drill Lab 23

Báo cáo đánh giá sự cố theo quy chuẩn Blameless Postmortem. Mục tiêu tập trung vào phân tích hệ thống và quy trình vận hành để cải thiện hạ tầng, không quy trách nhiệm cá nhân.

## 1. Timeline

| ISO time | Sự kiện | Evidence |
|---|---|---|
| 2026-10-09T10:45:08 | Outage bắt đầu trên region a do sự cố mạng netblock | `chaos/chaos-events.jsonl:4` |
| 2026-10-09T10:45:08 | Người dùng đầu tiên gặp lỗi timeout 503 qua edge proxy | `reports/drill-2-withdr.jsonl:25` |
| 2026-10-09T10:45:23 | Health check phát hiện 3 lần fail liên tiếp và đánh dấu UNHEALTHY | `reports/health-events.jsonl:2` |
| 2026-10-09T10:45:25 | Operator nhận cảnh báo và xác nhận failover sang region b | `reports/runbook-run.jsonl:2` |
| 2026-10-09T10:45:36 | Hệ thống phục hồi hoàn toàn, request đầu tiên thành công từ region b | `reports/drill-2-withdr.jsonl:39` |

## 2. RTO và RPO đo được so với mục tiêu — gap ở bước nào

- RTO mục tiêu: 300.0s, đo được: 28.2s, gap: 271.8s (đạt yêu cầu và vượt chỉ tiêu đề ra).
- RPO mục tiêu: 300.0s, đo được: 4.0s (mất 2 document), gap: 296.0s (nằm trong ngưỡng cho phép).
- Bước tốn nhiều thời gian nhất: Health check detection floor chiếm 15.0 giây (53.2% tổng thời gian RTO). Nguyên nhân là hệ thống cần 3 chu kỳ đo liên tiếp (mỗi chu kỳ 5 giây) để tránh báo động giả. Bước tốn thời gian thứ hai là GPU pool warm-up với 6.1 giây (21.6% tổng RTO) để khởi động pool suy luận tại region phụ.

## 3. Root cause (5 whys)

1. Tại sao ứng dụng trả về lỗi 503?
Vì region a bị cô lập kết nối mạng (netblock), không thể phản hồi các request suy luận.

2. Tại sao traffic không chuyển sang region b ngay lập tức?
Vì cần thời gian xác nhận outage tối thiểu 15 giây nhằm tránh hiện tượng flapping gây đảo chiều lưu lượng đột ngột.

3. Tại sao region b chưa thể phục vụ ngay khi region a ngừng hoạt động?
Vì region b hoạt động ở chế độ Warm Standby, chưa được nạp đầy đủ model weights và vector database vào bộ nhớ phục vụ.

4. Tại sao phát sinh mất mát dữ liệu 2 documents (RPO 4.0s)?
Vì cơ chế sao lưu snapshot định kỳ theo chu kỳ 30 giây, dẫn đến các document ingest trong khoảng thời gian giữa hai chu kỳ chưa kịp sao lưu lên replica.

5. Nếu đây là outage thực tế trên môi trường production, bước nào trong runbook dễ gặp rủi ro thất bại nhất?
Bước tải model weights và snapshot qua mạng liên vùng nếu dung lượng model lớn (hàng chục GB) hoặc bước cấp phát GPU nếu cloud provider không đủ tài nguyên tại region phụ.

## 4. Action items (có owner và deadline)

| # | Action item | Owner | Deadline | Giảm RTO/RPO bao nhiêu giây |
|---|---|---|---|---|
| 1 | Triển khai Change Data Capture (CDC) streaming replication cho vector database | Database Team | 2026-11-01 | Giảm RPO xuống dưới 1.0 giây |
| 2 | Duy trì GPU pool ở trạng thái pre-warmed sẵn sàng trên region phụ | MLOps Team | 2026-11-15 | Giảm RTO được 6.0 giây ở bước warm-up |
| 3 | Tối ưu hóa Edge TTL xuống 2 giây và tích hợp Global Load Balancer thông minh | SRE Team | 2026-11-20 | Giảm RTO thêm khoảng 3.0 giây ở bước DNS cache |

## 5. Ba câu hỏi bắt buộc trả lời

1. interval nhân threshold của bạn là bao nhiêu giây? Nó chiếm bao nhiêu phần trăm RTO?
Với interval = 5s và threshold = 3, ngưỡng phát hiện là 15.0 giây. Nó chiếm 53.2% trong tổng số 28.2 giây RTO đo được.

2. Nếu hạ interval xuống 1s, RTO giảm mấy giây và phải trả giá gì về flapping?
Nếu hạ interval xuống 1s với cùng threshold 3, thời gian phát hiện giảm xuống 3 giây, giúp RTO giảm 12.0 giây. Tuy nhiên, hệ thống sẽ rất dễ bị flapping khi chỉ cần mạng bị delay cục bộ hoặc CPU tăng tải nhẹ cũng khiến hệ thống bị coi là hỏng, gây failover liên tục giữa hai region và làm hệ thống mất ổn định nghiêm trọng.

3. Nếu outage kéo dài 6 giờ và region chính mất dữ liệu vĩnh viễn, docs_lost có ý nghĩa gì với khách hàng?
2 documents bị mất tương ứng với 2 yêu cầu hoặc giao dịch thực tế của khách hàng đã được tiếp nhận nhưng bị mất vĩnh viễn trong hệ thống RAG. Khách hàng sẽ không thể truy vấn lại nội dung này và doanh nghiệp cần có cơ chế transaction log replay hoặc event streaming broker để nạp lại dữ liệu thiếu hụt.
