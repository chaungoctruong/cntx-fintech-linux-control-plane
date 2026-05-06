# Runtime Health SQL

## Nhiệm vụ
- Chứa SQL tổng hợp sức khỏe runtime theo ngưỡng stale truyền từ code Python.
- Bao gồm runner/deployment/slot/account/events summary.

## Lưu ý an toàn
- Giữ param `%s` cho ngưỡng stale để linh hoạt theo môi trường.
- Không đưa logic nghiệp vụ ghi vào nhóm query read-only này.
