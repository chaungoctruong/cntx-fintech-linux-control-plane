# Lot, Margin, Spread, Swap

Lot là khối lượng lệnh. Lot càng lớn thì PnL và rủi ro biến động càng mạnh.

Margin phụ thuộc broker, symbol, leverage, contract size và loại tài khoản. Không được bịa số margin nếu không có contract specification của broker.

Spread là chênh lệch bid/ask. Spread giãn có thể làm bot ít vào lệnh, bỏ kèo hoặc khớp giá xấu.

Swap là phí/lãi qua đêm. Swap phụ thuộc broker, symbol, loại lệnh long/short, ngày triple swap và điều kiện tài khoản. Khi user hỏi "đi 3 lot EURUSD qua đêm tốn bao nhiêu", câu trả lời đúng là:
- Không bịa con số nếu chưa có swap long/short từ broker.
- Hỏi broker/server hoặc yêu cầu mở contract specification.
- Công thức thực tế phụ thuộc cách broker niêm yết swap.
- Nhắc rủi ro lot lớn và phí qua đêm có thể thay đổi.
